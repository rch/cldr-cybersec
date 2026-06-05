#!/usr/bin/env bash
# Build an RKE2-importable multi-image tarball of the Layer-A BOOTSTRAP images —
# rancher/local-path-provisioner:v0.0.30 + busybox:1.37 — so a fresh node can run
# the default-StorageClass provisioner (and its mkdir/teardown helper pod) with NO
# zarf registry and NO internet egress. RKE2 auto-imports any tarball dropped in
# /var/lib/rancher/rke2/agent/images/ into containerd at startup; the ansible
# `common` role copies this there BEFORE rke2 starts. image-gc-high-threshold=100
# (in the RKE2 config) then guarantees it's never pruned.
#
# This breaks the StorageClass<->registry chicken-egg air-gap-faithfully, replacing
# aws:deploy:zarf Phase 2c's old `raw.githubusercontent.com` pull of v0.0.32.
#
# Output: zarf/cybersec-bootstrap-images-amd64.tar  (docker-archive, linux/amd64)
# Idempotent: skips if the tarball already exists (delete it to rebuild).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="$REPO_ROOT/zarf/cybersec-bootstrap-images-amd64.tar"
# Keep in lockstep with zarf/manifests/local-path-provisioner.yaml + the
# bootstrap_images block of zarf/artifacts.manifest.json.
IMAGES=(rancher/local-path-provisioner:v0.0.30 busybox:1.37)

if [ -f "$OUT" ]; then
  echo "✅ bootstrap-images tarball already present: $OUT (rm to rebuild)"
  exit 0
fi

ENGINE="$(command -v podman || command -v docker || true)"
[ -n "$ENGINE" ] || { echo "ERROR: need podman or docker on PATH to build the tarball" >&2; exit 1; }
ENGINE_NAME="$(basename "$ENGINE")"

echo "Pulling bootstrap images (linux/amd64) with $ENGINE_NAME ..."
for img in "${IMAGES[@]}"; do
  "$ENGINE" pull --platform linux/amd64 "$img"
done

echo "Saving multi-image docker-archive → $OUT ..."
if [ "$ENGINE_NAME" = "podman" ]; then
  "$ENGINE" save --multi-image-archive --format docker-archive -o "$OUT" "${IMAGES[@]}"
else
  "$ENGINE" save -o "$OUT" "${IMAGES[@]}"   # docker save is docker-archive, multi-image native
fi

# Sanity gate: a docker-archive of both images is ~40+MB; a tiny file means the
# multi-image save was truncated/partial. (save() errors if an image is absent, so
# success ⇒ both refs are in the archive by construction — this just catches I/O.)
SIZE="$(stat -f%z "$OUT" 2>/dev/null || stat -c%s "$OUT" 2>/dev/null || echo 0)"
if [ "$SIZE" -lt 10000000 ]; then
  echo "ERROR: $OUT is only $SIZE bytes — the multi-image save looks truncated" >&2
  rm -f "$OUT"; exit 1
fi
echo "✅ $OUT ($(du -h "$OUT" | cut -f1))  [refs: ${IMAGES[*]}]"
