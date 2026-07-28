# shellcheck shell=bash
# K8s target detection shared by the devenv K8s process group (devenv.nix) so
# `devenv up -d` brings the whole stack up on any box without configuration:
#   - laptop (no RKE2)  -> provision/attach a local k3d cluster
#   - server (RKE2)     -> attach to the existing RKE2 cluster; NEVER install
#                          onto it (the zarf/converge engine owns those workloads)
#   - CYBERSEC_K8S_TARGET=none -> K8s group idles (core stack only)
#
# Source this file; do not execute it.

# Echo the resolved target: k3d | rke2 | none.
# Priority: explicit CYBERSEC_K8S_TARGET, then RKE2-on-this-host detection,
# then k3d as the laptop default.
k8s_detect_target() {
  case "${CYBERSEC_K8S_TARGET:-}" in
    k3d|rke2|none)
      echo "${CYBERSEC_K8S_TARGET}"
      return 0
      ;;
    "") ;;
    *)
      echo "WARN: unknown CYBERSEC_K8S_TARGET='${CYBERSEC_K8S_TARGET}' (expected k3d|rke2|none); auto-detecting" >&2
      ;;
  esac
  if [ -n "${KUBECONFIG:-}" ] && [ -f "$KUBECONFIG" ] && \
     grep -qE "rancher|rke2" "$KUBECONFIG" 2>/dev/null; then
    echo rke2
  elif [ -r /etc/rancher/rke2/rke2.yaml ] || [ -f "$HOME/.kube/rke2.yaml" ]; then
    echo rke2
  else
    echo k3d
  fi
}

# Echo the kubeconfig path for a target (may not exist yet for k3d).
# rke2: explicit KUBECONFIG > ~/.kube/rke2.yaml > /etc/rancher/rke2/rke2.yaml
# k3d:  the devenv-managed kubeconfig written by the k8s-cluster process
k8s_resolve_kubeconfig() {
  local target="$1"
  if [ "$target" = "rke2" ]; then
    for candidate in "${KUBECONFIG:-}" "$HOME/.kube/rke2.yaml" /etc/rancher/rke2/rke2.yaml; do
      if [ -n "$candidate" ] && [ -r "$candidate" ]; then
        echo "$candidate"
        return 0
      fi
    done
    return 1
  fi
  echo "$PWD/.devenv/state/kubeconfig"
}

# Park a process that has nothing to do for this target. Sleeping (instead of
# exiting) keeps process-compose restart policies from looping on a clean exit.
k8s_idle() {
  echo "$*"
  while true; do sleep 3600; done
}

# Wait for the Kubernetes API behind $KUBECONFIG to answer. Args: [tries] [delay]
k8s_wait_api() {
  local tries="${1:-60}" delay="${2:-5}"
  for _ in $(seq 1 "$tries"); do
    if kubectl get --raw /readyz >/dev/null 2>&1; then
      return 0
    fi
    sleep "$delay"
  done
  return 1
}
