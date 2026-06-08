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

case "${1:-}" in
  tag)      content_tag ;;
  image)    do_image ;;
  package)  do_package ;;
  redeploy) echo "redeploy: not yet implemented (increment 2 — image-delta push + drift-aware roll)"; exit 2 ;;
  *) echo "usage: ops.sh {tag|image|package|redeploy}" >&2; exit 2 ;;
esac
