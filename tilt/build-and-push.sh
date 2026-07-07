#!/usr/bin/env bash
# Tilt custom_build helper: build the cybersec-dask image and deliver it to the
# cluster. Called by Tilt with: tilt/build-and-push.sh $EXPECTED_REF
# Only runs on Tier 2 (Dockerfile or requirements change).
#
# Two delivery modes:
#   - Laptop k3d (TILT_K3D_IMPORT=<cluster>): registry-less — build locally then
#     `k3d image import` straight into the cluster nodes. Robust on the macOS
#     podman VM (no registry node, no push tunnel).
#   - Workstation RKE2 (default): push to the Zarf in-cluster registry, auth'd via
#     the zarf-state secret.
set -euo pipefail

EXPECTED_REF="$1"
ZARF_REGISTRY="127.0.0.1:31999"

BUILDER="$(command -v podman >/dev/null 2>&1 && echo podman || echo docker)"

# Ensure the podman socket is available (no-op for docker)
if [ "$BUILDER" = "podman" ] && ! podman info --format json >/dev/null 2>&1; then
    echo "Starting podman system service..."
    podman system service --time=0 "unix:///run/user/$(id -u)/podman/podman.sock" &
    sleep 1
fi

echo "Building $EXPECTED_REF ..."
"$BUILDER" build -t "$EXPECTED_REF" -f zarf/images/Dockerfile.cybersec-dask .

if [ -n "${TILT_K3D_IMPORT:-}" ]; then
    # podman stores bare tags under localhost/; containerd normalizes the pod ref
    # (Tilt sets it to the bare $EXPECTED_REF) to docker.io/library/. Retag to the
    # qualified name so the imported image is what the pod ref resolves to.
    QUALIFIED="docker.io/library/${EXPECTED_REF#docker.io/library/}"
    "$BUILDER" tag "$EXPECTED_REF" "$QUALIFIED" 2>/dev/null || true
    echo "Importing $QUALIFIED into k3d cluster $TILT_K3D_IMPORT ..."
    k3d image import "$QUALIFIED" -c "$TILT_K3D_IMPORT"
else
    # Workstation RKE2: authenticate to the Zarf registry via the zarf-state secret.
    KUBECONFIG="${KUBECONFIG:-$HOME/.kube/rke2.yaml}"
    ZARF_STATE=$(kubectl --kubeconfig="$KUBECONFIG" get secret -n zarf zarf-state \
        -o jsonpath='{.data.state}' | base64 -d)
    PUSH_USER=$(echo "$ZARF_STATE" | python3 -c \
        "import sys,json; print(json.load(sys.stdin)['registryInfo']['pushUsername'])")
    PUSH_PASS=$(echo "$ZARF_STATE" | python3 -c \
        "import sys,json; print(json.load(sys.stdin)['registryInfo']['pushPassword'])")
    "$BUILDER" login "$ZARF_REGISTRY" -u "$PUSH_USER" -p "$PUSH_PASS" --tls-verify=false 2>/dev/null
    echo "Pushing $EXPECTED_REF ..."
    "$BUILDER" push "$EXPECTED_REF" --tls-verify=false
fi
