#!/usr/bin/env bash
# Tilt custom_build helper: build with podman and push to Zarf registry
#
# Called by Tilt with: tilt/build-and-push.sh $EXPECTED_REF
# Only runs on Tier 2 (Dockerfile or requirements change).
set -euo pipefail

EXPECTED_REF="$1"
KUBECONFIG="${KUBECONFIG:-$HOME/.kube/rke2.yaml}"
REGISTRY="127.0.0.1:31999"

# Ensure podman socket is available
if ! podman info --format json >/dev/null 2>&1; then
    echo "Starting podman system service..."
    podman system service --time=0 "unix:///run/user/$(id -u)/podman/podman.sock" &
    sleep 1
fi

# Get registry credentials from Zarf state secret
ZARF_STATE=$(kubectl --kubeconfig="$KUBECONFIG" get secret -n zarf zarf-state \
    -o jsonpath='{.data.state}' | base64 -d)

PUSH_USER=$(echo "$ZARF_STATE" | python3 -c \
    "import sys,json; print(json.load(sys.stdin)['registryInfo']['pushUsername'])")
PUSH_PASS=$(echo "$ZARF_STATE" | python3 -c \
    "import sys,json; print(json.load(sys.stdin)['registryInfo']['pushPassword'])")

# Login to Zarf registry
podman login "$REGISTRY" -u "$PUSH_USER" -p "$PUSH_PASS" --tls-verify=false 2>/dev/null

# Build image
echo "Building $EXPECTED_REF ..."
podman build -t "$EXPECTED_REF" -f zarf/images/Dockerfile.cybersec-dask .

# Push to Zarf registry
echo "Pushing $EXPECTED_REF ..."
podman push "$EXPECTED_REF" --tls-verify=false
