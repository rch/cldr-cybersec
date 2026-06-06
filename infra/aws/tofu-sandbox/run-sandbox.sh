#!/usr/bin/env bash
# Engine for the throwaway air-gap converge-validation sandbox. Driven by `just sandbox*`
# (which runs hydrate.py first to write build/sandbox/{sandbox.env,sandbox.auto.tfvars}).
#
# ALL per-developer state — tofu state, the generated SSH key, the resolved /32 — lives
# under build/sandbox (gitignored). Nothing secret is committed, and the AWS account is
# asserted before every mutate, so this is safe + repeatable for any developer.
#
#   up         provision (egress ON) + wait for RKE2 Ready
#   transport  scp deploy+init packages to /var/tmp, stage the engine, deploy MinIO
#   airgap     cut egress (closed world); inbound SSH survives; confirms outbound blocked
#   online     restore egress
#   converge   the one-command resilient deploy (converge-node.sh apply); creds off argv
#   verify     converge --verify + pod/agent/ingress check
#   teardown   clean-slate the app stack (registry + node images conserved)
#   destroy    tofu destroy (THIS sandbox only)
#   ssh [cmd]  ssh to the node;  status;  ip
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SB_TOFU="$REPO_ROOT/infra/aws/tofu-sandbox"
EXPECT_ACCOUNT="050330818249"

BUILD_DIR="${SANDBOX_BUILD_DIR:-$REPO_ROOT/build/sandbox}"
ENV_FILE="$BUILD_DIR/sandbox.env"
TFVARS="$BUILD_DIR/sandbox.auto.tfvars"
[ -f "$ENV_FILE" ] || { echo "❌ $ENV_FILE missing — run 'just sandbox-config' (hydrate) first" >&2; exit 2; }
# shellcheck source=/dev/null
source "$ENV_FILE"          # KEY PACKAGE INIT_PACKAGE S3_* DASK_WORKER_REPLICAS ZARF_VERSION

export TF_DATA_DIR="$BUILD_DIR/.terraform"     # keep plugins/.terraform out of the committed tree
TF=(tofu -chdir="$SB_TOFU")
TFSTATE=(-state="$BUILD_DIR/terraform.tfstate")          # absolute => unaffected by -chdir
SSH_OPTS=(-i "$KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=15)
KCTL="sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml"

ACTION="${1:-status}"; shift || true

assert_account() {
  local a; a="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)"
  [ "$a" = "$EXPECT_ACCOUNT" ] || { echo "❌ AWS account '$a' != $EXPECT_ACCOUNT — refusing." >&2; exit 2; }
  echo "✅ account $a"
}
# Resolve the node IP from AWS by tag (robust; no dependence on tofu-output state flags).
node_ip() {
  aws ec2 describe-instances --region "$S3_REGION" \
    --filters "Name=tag:Project,Values=cybersec-sandbox" \
              "Name=instance-state-name,Values=running,pending" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text 2>/dev/null
}
on_node() { ssh "${SSH_OPTS[@]}" "ec2-user@$(node_ip)" "$@"; }

case "$ACTION" in
  up)
    assert_account
    mkdir -p "$BUILD_DIR/.terraform"
    "${TF[@]}" init -input=false >/dev/null
    "${TF[@]}" apply -input=false -auto-approve "${TFSTATE[@]}" -var-file="$TFVARS" -var allow_egress=true
    IP="$(node_ip)"; echo "⏳ waiting for RKE2 on $IP ..."
    for i in $(seq 1 30); do
      r="$(ssh "${SSH_OPTS[@]}" -o ConnectTimeout=8 "ec2-user@$IP" \
            "$KCTL get nodes --no-headers 2>/dev/null | grep -c ' Ready '" 2>/dev/null || echo 0)"
      [ "$r" = "1" ] && { echo "✅ node Ready ($IP)"; exit 0; }
      sleep 10
    done
    echo "⚠ RKE2 not Ready after ~5m — check 'run-sandbox.sh status'"; exit 1
    ;;

  transport)
    IP="$(node_ip)"
    [ -f "$PACKAGE" ] || { echo "❌ deploy package not found: $PACKAGE (build it: cd zarf && zarf package create --confirm)" >&2; exit 2; }
    # zarf-init package: in a REAL air-gap it ships in the transport bundle; for the
    # rehearsal we fetch it here (laptop has internet, before the egress cut) if absent.
    if [ ! -f "$INIT_PACKAGE" ]; then
      echo "→ fetching zarf-init package ($ZARF_VERSION)"
      mkdir -p "$(dirname "$INIT_PACKAGE")"
      curl -fsSL -o "$INIT_PACKAGE" \
        "https://github.com/zarf-dev/zarf/releases/download/$ZARF_VERSION/zarf-init-amd64-$ZARF_VERSION.tar.zst"
    fi
    echo "→ Layer-A packages to /var/tmp (deploy + zarf-init)"
    scp "${SSH_OPTS[@]}" "$PACKAGE" "ec2-user@$IP:/var/tmp/"
    scp "${SSH_OPTS[@]}" "$INIT_PACKAGE" "ec2-user@$IP:/var/tmp/"
    echo "→ stage the convergence engine"
    on_node "mkdir -p ~/cybersec-converge/manifests"
    scp "${SSH_OPTS[@]}" -r "$REPO_ROOT/zarf/converge" "$REPO_ROOT/zarf/artifacts.manifest.json" \
        "$REPO_ROOT/zarf/scripts/converge-node.sh" "ec2-user@$IP:/home/ec2-user/cybersec-converge/"
    scp "${SSH_OPTS[@]}" "$REPO_ROOT/zarf/manifests/local-path-provisioner.yaml" \
        "ec2-user@$IP:/home/ec2-user/cybersec-converge/manifests/"
    echo "→ deploy in-cluster MinIO (provided-S3 stand-in; pulled while egress is ON)"
    scp "${SSH_OPTS[@]}" "$SB_TOFU/minio.yaml" "ec2-user@$IP:/var/tmp/minio.yaml"
    on_node "$KCTL apply -f /var/tmp/minio.yaml >/dev/null && $KCTL -n minio rollout status deploy/minio --timeout=180s"
    ;;

  airgap)
    assert_account
    "${TF[@]}" apply -input=false -auto-approve "${TFSTATE[@]}" -var-file="$TFVARS" -var allow_egress=false
    echo "🔒 egress CUT"
    on_node "timeout 8 curl -sS -o /dev/null https://github.com && echo '⚠ STILL HAS EGRESS' || echo '✅ outbound BLOCKED (closed world)'"
    ;;

  online)
    assert_account
    "${TF[@]}" apply -input=false -auto-approve "${TFSTATE[@]}" -var-file="$TFVARS" -var allow_egress=true
    echo "🌐 egress RESTORED"
    ;;

  converge)
    IP="$(node_ip)"
    # S3 creds -> node tmpfs via STDIN (never argv); converge reads CONVERGE_CREDS_FILE.
    printf 'S3_ENDPOINT=%s\nS3_BUCKET=%s\nS3_REGION=%s\nS3_ACCESS_KEY=%s\nS3_SECRET_KEY=%s\n' \
      "$S3_ENDPOINT" "$S3_BUCKET" "$S3_REGION" "$S3_ACCESS_KEY" "$S3_SECRET_KEY" \
      | ssh "${SSH_OPTS[@]}" "ec2-user@$IP" "umask 077 && cat > /dev/shm/.sb-creds"
    ssh "${SSH_OPTS[@]}" "ec2-user@$IP" \
      "sudo env CONVERGE_CREDS_FILE=/dev/shm/.sb-creds DASK_WORKER_REPLICAS='$DASK_WORKER_REPLICAS' \
        bash ~/cybersec-converge/converge-node.sh apply; rc=\$?; \
        shred -u /dev/shm/.sb-creds 2>/dev/null || rm -f /dev/shm/.sb-creds; exit \$rc"
    ;;

  verify)
    on_node "sudo bash ~/cybersec-converge/converge-node.sh verify"
    echo "--- pods ---"; on_node "$KCTL get pods -A | grep -iE 'dask|jupyter|panel|zarf|minio' || true"
    ;;

  teardown) on_node "sudo bash ~/cybersec-converge/converge-node.sh teardown" ;;

  destroy)
    assert_account
    "${TF[@]}" destroy -input=false -auto-approve "${TFSTATE[@]}" -var-file="$TFVARS"
    echo "🧹 sandbox destroyed (build/sandbox state cleared)"
    ;;

  ssh)    on_node "$@" ;;
  status) on_node "cat /var/tmp/bootstrap-status 2>/dev/null; $KCTL get nodes 2>/dev/null || echo 'k8s not ready yet'" ;;
  ip)     node_ip ;;
  *) echo "usage: $0 {up|transport|airgap|online|converge|verify|teardown|destroy|ssh|status|ip}" >&2; exit 2 ;;
esac
