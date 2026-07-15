#!/usr/bin/env bash
# Drive the live AWS RKE2 air-gap cluster to TARGET STATE with the convergence
# engine (zarf/converge). Deterministic + idempotent: discover → diff target →
# remediate → repeat to a fixpoint. This is the AWS TRANSPORT SHIM around the
# node-local entrypoint `converge-node.sh`: it resolves cluster coordinates from
# tofu, stages the engine + that script on the control plane over the bastion hop,
# and runs converge-node.sh there (kubectl-only at runtime). The actual convergence
# invocation lives in ONE place — converge-node.sh — so the AWS and bare-node paths
# can't drift.
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
  verify|apply|dry-run|teardown) ;;   # converge-node.sh maps these to engine flags
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

# --- stage the engine + node entrypoint on the control plane -----------------
# User-owned dir; converge-node.sh resolves kubectl/zarf/the package node-side and
# runs the engine with PYTHONDONTWRITEBYTECODE so no root-owned __pycache__ is left
# behind to block the next restage. The package itself is auto-located from /var/tmp
# by converge-node.sh (needed only for component-deploy fixes; verify works without).
STAGE="/home/ec2-user/cybersec-converge"
cp_ssh "rm -rf $STAGE && mkdir -p $STAGE/manifests" </dev/null
scp -q -i "$SSH_KEY" -o ProxyCommand="$PROXY" "${SSH_OPTS[@]}" -r \
  zarf/converge zarf/artifacts.manifest.json zarf/scripts/converge-node.sh \
  "ec2-user@$CONTROL_IP:$STAGE/" </dev/null
# The bundled local-path manifest is used only in the OPT-IN dynamic-provisioning
# mode (CONVERGE_DYNAMIC_PROVISIONING=1); stage it so that path stays self-contained.
# The resilient default never applies it (the registry binds a claimRef hostPath PV).
scp -q -i "$SSH_KEY" -o ProxyCommand="$PROXY" "${SSH_OPTS[@]}" \
  zarf/manifests/local-path-provisioner.yaml "ec2-user@$CONTROL_IP:$STAGE/manifests/" </dev/null

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

# --- run the node entrypoint on the control plane (single source of truth) ----
# converge-node.sh resolves kubectl/zarf/the package + modality itself; we pass the
# creds-file location + worker count + dynamic-provisioning toggle as ENV (never argv).
# DASK_WORKER_REPLICAS isn't a secret; the engine still caps to live capacity, but
# seeding it avoids an oversubscribed first pass that then has to be reaped.
RUN_ENV="PYTHONDONTWRITEBYTECODE=1"
[ -n "$CREDS_REMOTE" ] && RUN_ENV="$RUN_ENV CONVERGE_CREDS_FILE=$CREDS_REMOTE"
[ -n "${DASK_WORKER_REPLICAS:-}" ] && RUN_ENV="$RUN_ENV DASK_WORKER_REPLICAS=$DASK_WORKER_REPLICAS"
[ -n "${CONVERGE_DYNAMIC_PROVISIONING:-}" ] && RUN_ENV="$RUN_ENV CONVERGE_DYNAMIC_PROVISIONING=$CONVERGE_DYNAMIC_PROVISIONING"
set +e
cp_ssh "cd $STAGE && sudo -n env $RUN_ENV bash converge-node.sh $MODE" </dev/null
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
