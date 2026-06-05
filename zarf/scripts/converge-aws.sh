#!/usr/bin/env bash
# Drive the live AWS RKE2 air-gap cluster to TARGET STATE with the convergence
# engine (zarf/converge). Deterministic + idempotent: discover → diff target →
# remediate → repeat to a fixpoint. This is the operational front-end for the
# proven `python3 -m converge` invocation — it stages the engine on the control
# plane (over the bastion hop) and runs it there, kubectl-only at runtime.
#
# Usage:
#   zarf/scripts/converge-aws.sh [verify|apply|dry-run|teardown]
#     verify   (default) read-only target oracle — reports drift, changes nothing
#     apply              remediate to a fixpoint (Layer-B only; NEVER deletes a
#                        transported image — that guard is structural in the engine)
#     dry-run            show what apply WOULD do
#     teardown           clean-slate the Layer-B app stack (dask/panel-viz/jupyter/
#                        engine); registry/StorageClass + node images are CONSERVED,
#                        so a following `apply` redeploys fast. Turn-key + idempotent.
#
# Credentials: kubectl-only healing needs NONE — workloads read the in-cluster S3
# secret. For a from-scratch deploy that must (re)create that secret, export the
# S3_* vars below before calling this script (this IS the converge path that
# `aws:deploy:zarf` drives — it exports them for you). They flow to zarf via
# ZARF_VAR_* env, never argv: S3_ENDPOINT S3_BUCKET S3_REGION S3_ACCESS_KEY
# S3_SECRET_KEY S3_SESSION_TOKEN. DASK_WORKER_REPLICAS (non-secret) → --set.
#
# SSH access: assumes your egress is already allowed by the bastion SG
# (allowed_ssh_cidrs — e.g. the WARP/Tailscale range). Add a temporary /32 only
# while iterating, and revoke it after.
set -euo pipefail

MODE="${1:-verify}"
case "$MODE" in
  verify)   MODE_FLAG="--verify" ;;
  apply)    MODE_FLAG="--apply" ;;
  dry-run)  MODE_FLAG="--dry-run" ;;
  teardown) MODE_FLAG="--teardown" ;;
  *) echo "usage: $0 [verify|apply|dry-run|teardown]" >&2; exit 2 ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# --- resolve cluster coordinates from tofu state -----------------------------
pushd infra/aws/tofu >/dev/null
BASTION_IP="$(tofu output -raw bastion_public_ip 2>/dev/null || true)"
CONTROL_IP="$(tofu output -json control_plane_private_ips 2>/dev/null | jq -r '.[0] // empty' || true)"
popd >/dev/null
if [ -z "$BASTION_IP" ] || [ -z "$CONTROL_IP" ]; then
  echo "❌ Cluster not provisioned (no bastion/control-plane in tofu state)." >&2
  echo "   Run 'devenv tasks run aws:provision' first." >&2
  exit 1
fi

SSH_KEY="${SSH_KEY:-$HOME/.ssh/cybersec-dask.pem}"
SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=15)
PROXY="ssh -i $SSH_KEY -W %h:%p ${SSH_OPTS[*]} ec2-user@$BASTION_IP"

# ssh to the control plane via the bastion. ProxyCommand is passed as a single
# -o argument (NOT through eval) so ssh expands %h:%p itself — re-expanding it
# through a shell splits the spec and breaks the hop.
cp_ssh() {
  ssh -i "$SSH_KEY" -o ProxyCommand="$PROXY" "${SSH_OPTS[@]}" \
    ec2-user@"$CONTROL_IP" "$@"
}

echo "🎯 converge ($MODE)   bastion=$BASTION_IP   control-plane=$CONTROL_IP"

# --- stage the engine on the control plane -----------------------------------
# User-owned dir + PYTHONDONTWRITEBYTECODE so no root-owned __pycache__ is left
# behind to block the next restage.
STAGE="/home/ec2-user/cybersec-converge"
cp_ssh "rm -rf $STAGE && mkdir -p $STAGE" </dev/null
scp -q -i "$SSH_KEY" -o ProxyCommand="$PROXY" "${SSH_OPTS[@]}" -r \
  zarf/converge zarf/artifacts.manifest.json "ec2-user@$CONTROL_IP:$STAGE/" </dev/null
# Stage the bundled local-path manifest so converge can bootstrap the default
# StorageClass with kubectl (registry-free, node-preloaded image) — breaks the
# SC<->registry chicken-egg on a fresh cluster. Matches the engine's default
# --manifests-dir (<stage>/manifests/).
cp_ssh "mkdir -p $STAGE/manifests" </dev/null
scp -q -i "$SSH_KEY" -o ProxyCommand="$PROXY" "${SSH_OPTS[@]}" \
  zarf/manifests/local-path-provisioner.yaml "ec2-user@$CONTROL_IP:$STAGE/manifests/" </dev/null

# --- locate the transported package (optional) -------------------------------
# Needed only for component-deploy remediations; --verify and kubectl-only fixes
# work without it (those invariants degrade to a precise MANUAL hint).
PKG="$(cp_ssh "ls -t /var/tmp/zarf-package-cybersec-dask-amd64-*.tar.zst 2>/dev/null | head -1" </dev/null | tr -d '\r' || true)"
PKG_ARGS=()
if [ -n "$PKG" ]; then
  PKG_ARGS=(--zarf /usr/local/bin/zarf --package "$PKG")
  echo "   package: $PKG"
else
  echo "   package: <none on control plane> — component-deploy fixes report MANUAL"
fi

# --- optional S3 creds → ZARF_VAR_* env, transported over the SSH channel -----
# Written to tmpfs (RAM, mode 600) and removed after the run, NEVER placed on any
# command line. The engine reads them and forwards to zarf as ZARF_VAR_* env.
CREDS_REMOTE=""
CREDS_LINES=""
for v in S3_ENDPOINT S3_BUCKET S3_REGION S3_ACCESS_KEY S3_SECRET_KEY S3_SESSION_TOKEN; do
  if [ -n "${!v:-}" ]; then CREDS_LINES+="$v=${!v}"$'\n'; fi
done
if [ -n "$CREDS_LINES" ]; then
  CREDS_REMOTE="/dev/shm/.converge-creds"
  printf '%s' "$CREDS_LINES" | cp_ssh "umask 077 && cat > $CREDS_REMOTE"
  echo "   creds: $(printf '%s' "$CREDS_LINES" | grep -c .) S3 var(s) staged to tmpfs (off argv)"
fi

# --- non-secret zarf vars via --set (deterministic first-deploy sizing) ------
# DASK_WORKER_REPLICAS isn't a secret, so it rides argv; T4.workers-capacity will
# still cap to live schedulable capacity, but seeding the dask-cluster deploy with
# the node count avoids an oversubscribed first pass that then has to be reaped.
SET_ARGS=()
[ -n "${DASK_WORKER_REPLICAS:-}" ] && SET_ARGS+=(--set "DASK_WORKER_REPLICAS=${DASK_WORKER_REPLICAS}")

# --- run the engine on the control plane -------------------------------------
CREDS_ARGS=()
[ -n "$CREDS_REMOTE" ] && CREDS_ARGS=(--creds-file "$CREDS_REMOTE")
set +e
cp_ssh "cd $STAGE && sudo -n env PYTHONDONTWRITEBYTECODE=1 KUBECONFIG=/etc/rancher/rke2/rke2.yaml \
  python3 -m converge \
  --kubectl '/var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml' \
  --manifests-dir $STAGE/manifests \
  ${PKG_ARGS[*]} ${CREDS_ARGS[*]} ${SET_ARGS[*]} $MODE_FLAG" </dev/null
RC=$?
set -e

# shred the tmpfs creds regardless of outcome
[ -n "$CREDS_REMOTE" ] && cp_ssh "shred -u $CREDS_REMOTE 2>/dev/null || rm -f $CREDS_REMOTE" </dev/null || true

echo ""
if [ "$RC" -eq 0 ]; then
  echo "✅ converged — deployment matches target state (or verify clean)"
else
  echo "✖ not converged (rc=$RC) — see the status table above"
fi
exit "$RC"
