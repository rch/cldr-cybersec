# -*- mode: python -*-
# Tiltfile for iterating on OTEL Navigator + Engine on RKE2
#
# Two-tier strategy:
#   Tier 1 (live_update): Edit .py files → ~3-5s sync into running pod
#   Tier 2 (full rebuild): Edit Dockerfile/requirements → podman build + push
#
# Usage:
#   KUBECONFIG=~/.kube/rke2.yaml tilt up

# --- Config ---
REGISTRY = '127.0.0.1:31999'
IMAGE_NAME = REGISTRY + '/cybersec-dask'

# Use existing RKE2 cluster (KUBECONFIG=~/.kube/rke2.yaml)
allow_k8s_contexts(k8s_context())

# --- Image build ---
custom_build(
    IMAGE_NAME,
    'tilt/build-and-push.sh $EXPECTED_REF',
    deps=[
        'zarf/images/Dockerfile.cybersec-dask',
        'zarf/images/requirements-airgap.txt',
        'zarf/images/otel-navigator.py',
        'cybersec/engine/',
        'cybersec/pty_proxy/',
    ],
    skips_local_docker=True,
    live_update=[
        # Dockerfile/requirements changes don't match any sync → full rebuild
        fall_back_on([
            'zarf/images/Dockerfile.cybersec-dask',
            'zarf/images/requirements-airgap.txt',
        ]),
        sync('zarf/images/otel-navigator.py', '/app/otel-navigator.py'),
        sync('cybersec/engine/', '/app/cybersec/engine/'),
        sync('cybersec/pty_proxy/', '/app/cybersec/pty_proxy/'),
        # Touch the app file to trigger Panel's watchfiles-based autoreload.
        # kubectl cp (tar extract) doesn't reliably fire inotify events.
        run('touch /app/otel-navigator.py'),
    ],
)

# --- K8s resources (overlay on Zarf-deployed resources) ---
k8s_yaml('tilt/panel-viz-dev.yaml')
k8s_yaml('tilt/engine-dev.yaml')

# otel-navigator: Panel viz + PTY proxy sidecar
# Browser access via NodePort: http://<node-ip>:30506 (Panel), ws://<node-ip>:30765 (PTY)
k8s_resource(
    'otel-navigator',
    port_forwards=[
        port_forward(15006, 5006, host='0.0.0.0', name='panel'),
        port_forward(18765, 8765, host='0.0.0.0', name='pty-ws'),
    ],
    labels=['panel-viz'],
)

# navigator-engine: gRPC server
k8s_resource(
    'navigator-engine',
    port_forwards=[
        port_forward(50051, 50051, host='0.0.0.0', name='grpc'),
    ],
    labels=['panel-viz'],
)
