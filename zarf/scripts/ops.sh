#!/usr/bin/env bash
# Build / package / redeploy ops for the cybersec-dask image.
#
# Promotes this session's tribal redeploy steps into one place:
#   - a CONTENT-DERIVED image tag (BASE-<hash of image inputs>), so every image
#     change is a NEW tag -> the converge image-drift detect rolls it (no manual bump);
#   - a dual-tag + LOCAL-REGISTRY push, so `zarf package create` finds the fresh
#     image via the registry regardless of a stale podman DOCKER_HOST socket;
#   - the closure/size gate;
#   - (redeploy, increment 2) a fast image-delta push to the live registry + a
#     drift-aware converge roll, avoiding the 1.3G full-package transport.
#
# Usage:  ops.sh {tag|image|package|redeploy}
# Driven by `just image|package|redeploy`. Override via env (IMAGE_BASE_VER, LOCAL_REGISTRY).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

IMG="cybersec-dask"
BASE_VER="${IMAGE_BASE_VER:-2025.2.0}"          # the ghcr.io/dask/dask base it builds on
LOCAL_REG="${LOCAL_REGISTRY:-localhost:5555}"
DOCKERFILE="zarf/images/Dockerfile.cybersec-dask"

# Files whose CONTENT defines the image — NOT the tag-bearing manifests (that would
# make the hash self-referential). Mirrors what the Dockerfile COPYs/installs.
_image_inputs() {
  local f
  for f in "$DOCKERFILE" zarf/images/requirements-airgap.txt \
           zarf/images/requirements-agent.txt zarf/images/otel-navigator.py \
           zarf/images/loader.js; do
    [ -f "$f" ] && echo "$f"
  done
  find cybersec config zarf/images/sample-notebooks -type f \
    -not -path '*/__pycache__/*' -not -name '*.pyc' 2>/dev/null
}

content_tag() {
  local h
  h=$(_image_inputs | sort -u | xargs sha256sum 2>/dev/null | sha256sum | cut -c1-10)
  echo "${BASE_VER}-${h}"
}

# The source files that carry the image tag (kept in lockstep with the build).
_tag_files() {
  printf '%s\n' zarf/zarf.yaml zarf/artifacts.manifest.json \
    zarf/manifests/engine.yaml zarf/manifests/panel-viz.yaml zarf/manifests/dask-cluster.yaml
}
current_tag() { grep -hoE "${IMG}:[A-Za-z0-9._-]+" zarf/zarf.yaml | head -1 | cut -d: -f2-; }

bump_tag() {  # idempotent — rewrites the tag in the source manifests only if it changed
  local new="$1" old f
  old="$(current_tag)"
  if [ "$old" = "$new" ]; then echo "  image tag already ${new} (no manifest change)"; return 0; fi
  for f in $(_tag_files); do sed -i "s|${IMG}:${old}|${IMG}:${new}|g" "$f"; done
  echo "  bumped image tag ${old} -> ${new}"
}

_builder() { command -v podman >/dev/null 2>&1 && echo podman || echo docker; }
_registry_up() { curl -sf "http://${LOCAL_REG}/v2/" >/dev/null 2>&1; }

do_image() {
  local tag; tag="$(content_tag)"
  echo "[image] content tag = ${IMG}:${tag}"
  bump_tag "$tag"
  local B; B="$(_builder)"
  echo "[image] $B build (linux/amd64, dual-tag)..."
  $B build --platform linux/amd64 \
    -t "${IMG}:${tag}" -t "${LOCAL_REG}/${IMG}:${tag}" \
    -f "$DOCKERFILE" --build-arg BASE_IMAGE="ghcr.io/dask/dask:${BASE_VER}" .
  if _registry_up; then
    echo "[image] push ${LOCAL_REG}/${IMG}:${tag} (so zarf package create finds it)..."
    $B push --tls-verify=false "${LOCAL_REG}/${IMG}:${tag}"
  else
    echo "[image] WARN: local registry ${LOCAL_REG} unreachable — skipped push;" \
         "zarf package create will fall back to the daemon (may need DOCKER_HOST fixed)."
  fi
  echo "[image] done: ${IMG}:${tag}"
}

do_package() {
  echo "[package] zarf package create (image tag $(current_tag))..."
  ( cd zarf && zarf package create --confirm )
  local pkg; pkg="$(ls -t zarf/zarf-package-${IMG}-amd64-*.tar.zst 2>/dev/null | head -1)"
  [ -n "$pkg" ] || { echo "[package] ERROR: no package produced"; exit 1; }
  echo "[package] closure gate on $(basename "$pkg")..."
  python3 zarf/scripts/check-closure.py "$pkg"
}

do_redeploy() {
  # Reliable path: transport the package (skipped if unchanged) + converge apply
  # (now image-drift-aware, so it rolls the content tag without a forced deploy).
  # The FAST path (image-delta push to the live registry NodePort + `kubectl set
  # image`, moving only the ~MB app layer) is the next optimization — see #30.
  local tag; tag="$(current_tag)"
  echo "[redeploy] target image tag: ${IMG}:${tag}"
  local BASTION CP BUCKET REGION
  pushd infra/aws/tofu >/dev/null
  BASTION="$(tofu output -raw bastion_public_ip 2>/dev/null || true)"
  CP="$(tofu output -json control_plane_private_ips 2>/dev/null | jq -r '.[0] // empty' || true)"
  BUCKET="$(tofu output -raw s3_bucket_name 2>/dev/null || true)"
  REGION="$(tofu output -json cluster_info 2>/dev/null | jq -r '.region // "us-east-1"' 2>/dev/null || echo us-east-1)"
  popd >/dev/null
  [ -n "$BASTION" ] && [ -n "$CP" ] || { echo "[redeploy] cluster not provisioned (no tofu coords)"; exit 1; }
  local want="${AWS_ACCOUNT:-050330818249}" acct
  acct="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)"
  [ "$acct" = "$want" ] || { echo "[redeploy] WRONG ACCOUNT $acct (want $want) — abort"; exit 1; }
  echo "[redeploy] account=$acct bastion=$BASTION cp=$CP bucket=$BUCKET region=$REGION"

  local SSH_KEY="${SSH_KEY:-$HOME/.ssh/cybersec-dask.pem}" SG="${BASTION_SG:-sg-06ec172a5360ee1c0}"
  local MYIP; MYIP="$(curl -s --max-time 10 https://checkip.amazonaws.com)/32"
  trap "aws ec2 revoke-security-group-ingress --group-id $SG --protocol tcp --port 22 --cidr $MYIP >/dev/null 2>&1 && echo '[redeploy][sg] revoked'" EXIT
  aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 --cidr "$MYIP" >/dev/null 2>&1 \
    && echo "[redeploy][sg] authorized $MYIP"
  sleep 8

  local O=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=25 -o ServerAliveInterval=15 -o ServerAliveCountMax=10 -o GSSAPIAuthentication=no)
  local PROXY="ssh -i $SSH_KEY -W %h:%p ${O[*]} ec2-user@$BASTION"
  cp_ssh() { ssh -i "$SSH_KEY" -o ProxyCommand="$PROXY" "${O[@]}" ec2-user@"$CP" "$@"; }
  local PKG; PKG="$(ls -t zarf/zarf-package-${IMG}-amd64-*.tar.zst 2>/dev/null | head -1)"
  [ -n "$PKG" ] || { echo "[redeploy] no package — run: just package"; exit 1; }
  local PN; PN="$(basename "$PKG")"

  scp -i "$SSH_KEY" "${O[@]}" "$SSH_KEY" "ec2-user@$BASTION:/home/ec2-user/.ssh/$(basename "$SSH_KEY")" >/dev/null
  ssh -i "$SSH_KEY" "${O[@]}" ec2-user@"$BASTION" "chmod 600 ~/.ssh/$(basename "$SSH_KEY")"
  local lmd5 rmd5
  lmd5="$( (md5 -q "$PKG" 2>/dev/null || md5sum "$PKG" | cut -d' ' -f1) )"
  rmd5="$(cp_ssh "md5sum /var/tmp/$PN 2>/dev/null | cut -d' ' -f1" || true)"
  if [ -n "$rmd5" ] && [ "$lmd5" = "$rmd5" ]; then
    echo "[redeploy] package already on CP (md5 match) — skipping the 1.3G transport"
  else
    echo "[redeploy] transporting package ($(du -h "$PKG"|cut -f1)) laptop->bastion->CP (slow hop)..."
    scp -i "$SSH_KEY" "${O[@]}" "$PKG" "ec2-user@$BASTION:/var/tmp/$PN"
    ssh -i "$SSH_KEY" "${O[@]}" ec2-user@"$BASTION" \
      "scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/$(basename "$SSH_KEY") /var/tmp/$PN ec2-user@$CP:/var/tmp/$PN"
  fi

  echo "[redeploy] converge apply (drift detect rolls ${tag})..."
  export S3_ENDPOINT="" S3_BUCKET="$BUCKET" S3_REGION="$REGION"
  bash zarf/scripts/converge-aws.sh apply

  echo "[redeploy] verify:"
  cp_ssh 'K="sudo -n /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml -n panel-viz"; $K get pods -o wide | grep -E "navigator-engine|otel-navigator"; echo -n "  image: "; $K get pod -l app=otel-navigator -o jsonpath="{.items[0].spec.containers[0].image}"; echo'
}

case "${1:-}" in
  tag)      content_tag ;;
  image)    do_image ;;
  package)  do_package ;;
  redeploy) do_redeploy ;;
  *) echo "usage: ops.sh {tag|image|package|redeploy}" >&2; exit 2 ;;
esac
