# -*- mode: python -*-
# Tiltfile for iterating on OTEL Navigator + Engine.
#
# Two targets, selected by env (set automatically by `just dev-tilt` for k3d):
#   - laptop k3d   : TILT_K3D_IMPORT=<cluster>  (registry-less; k3d image import)
#   - workstation RKE2 (default): the Zarf in-cluster registry 127.0.0.1:31999
#
# Two-tier strategy:
#   Tier 1 (live_update): Edit .py files -> ~3-5s sync into running pod
#   Tier 2 (full rebuild): Edit Dockerfile/requirements -> build + push
#
# Usage:
#   just dev-tilt                          # laptop k3d (build/k3d/kubeconfig)
#   KUBECONFIG=~/.kube/rke2.yaml tilt up   # workstation RKE2

# --- Image delivery: two modes, selected by env ---
# Laptop k3d (TILT_K3D_IMPORT=<cluster>): registry-less — build-and-push.sh builds
# locally and `k3d image import`s straight into the cluster. This sidesteps the
# k3d-registry-on-podman 'bridge' network gap and the podman-VM push obstacle.
# Workstation RKE2 (default): push to the Zarf in-cluster registry (unchanged).
IMAGE_NAME = 'cybersec-dask'
K3D_IMPORT = os.getenv('TILT_K3D_IMPORT', '')

if not K3D_IMPORT:
    default_registry(os.getenv('TILT_REGISTRY', '127.0.0.1:31999'))

allow_k8s_contexts(k8s_context())

_LIVE_UPDATE = [
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
]

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
    disable_push=bool(K3D_IMPORT),   # k3d-import mode owns delivery itself
    live_update=_LIVE_UPDATE,
)

# --- K8s resources (overlay on the base resources from Zarf or `just dev-deploy`) ---
k8s_yaml('tilt/panel-viz-dev.yaml')
k8s_yaml('tilt/engine-dev.yaml')

# otel-navigator: Panel viz + PTY proxy sidecar
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
