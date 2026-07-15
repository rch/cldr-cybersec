#!/usr/bin/env bash
# Node-local convergence entrypoint — the PRIMARY way to deploy a cybersec-dask
# release into an existing, air-gapped RKE2. No AWS, no tofu, no bastion: run it ON
# the cluster's control-plane node after transporting the release package + this
# repo's zarf/ tree (or just the staged engine: converge/ + manifests/ +
# artifacts.manifest.json beside this script). It resolves kubectl / zarf / the
# package locally and drives `python3 -m converge` to the deployment's target state.
#
# Usage (on the air-gap node, as root — RKE2's kubeconfig is root-only):
#   sudo zarf/scripts/converge-node.sh [verify|apply|dry-run|teardown] [package.tar.zst]
#
#   verify    (default) read-only target oracle — reports drift, changes nothing
#   apply               remediate to a fixpoint (Layer-B only; NEVER deletes a
#                       transported image — that guard is structural in the engine)
#   dry-run             show what apply WOULD do
#   teardown            clean-slate the Layer-B app stack (registry +PV + node
#                       images CONSERVED, so a following apply redeploys fast)
#
# DEFAULT modality = RESILIENT air-gap: the registry binds a claimRef hostPath PV, so
# NO default StorageClass / local-path-provisioner / bootstrap images are needed. This
# is the only modality public releases target. To opt a resourced multi-node cluster
# back into dynamic provisioning, export CONVERGE_DYNAMIC_PROVISIONING=1.
#
# S3 credentials (only for a from-scratch deploy that (re)creates the in-cluster S3
# secret) — either:
#   - export S3_ENDPOINT S3_BUCKET S3_REGION S3_ACCESS_KEY S3_SECRET_KEY
#     S3_SESSION_TOKEN  (this script stages them to a tmpfs creds file + shreds it), or
#   - point CONVERGE_CREDS_FILE at a KEY=VALUE file you manage (not shredded here).
# Secrets are staged to a tmpfs creds file; the engine delivers them via a 0600
# ZARF_CONFIG ([package.deploy.set]) — bare ZARF_VAR_* env does NOT template in
# zarf v0.70.1. Never put secrets on argv / the process table.
#
# Env tunables: S3_BUCKET (required for a first deploy), DASK_WORKER_REPLICAS
# (default 1 — the resilient single-node baseline; raise for bigger clusters, the
# engine still caps to live capacity), KUBECONFIG, CONVERGE_DYNAMIC_PROVISIONING=1,
# CONVERGE_NO_REGISTRY_PVC=1.
set -euo pipefail

MODE="${1:-verify}"
case "$MODE" in
  verify)   MODE_FLAG="--verify" ;;
  apply)    MODE_FLAG="--apply" ;;
  dry-run)  MODE_FLAG="--dry-run" ;;
  teardown) MODE_FLAG="--teardown" ;;
  *) echo "usage: $0 [verify|apply|dry-run|teardown] [package.tar.zst]" >&2; exit 2 ;;
esac
PKG_ARG="${2:-}"

# --- locate the engine (works from the repo AND from a flat staged dir) -------
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ZARF_DIR=""
for cand in "$SELF" "$SELF/.." "$SELF/../zarf" "$SELF/zarf"; do
  if [ -f "$cand/converge/__init__.py" ]; then ZARF_DIR="$(cd "$cand" && pwd)"; break; fi
done
if [ -z "$ZARF_DIR" ]; then
  echo "❌ cannot find the converge engine (converge/__init__.py) near $SELF" >&2
  exit 2
fi
MANIFESTS_DIR="$ZARF_DIR/manifests"
MANIFEST_JSON="$ZARF_DIR/artifacts.manifest.json"

# --- resolve kubeconfig / kubectl / zarf -------------------------------------
KUBECONFIG="${KUBECONFIG:-/etc/rancher/rke2/rke2.yaml}"
if [ ! -r "$KUBECONFIG" ] && [ "$(id -u)" != 0 ]; then
  echo "❌ $KUBECONFIG not readable — run as root (sudo) on the control-plane node." >&2
  exit 2
fi
ZARF_BIN=""
for c in /usr/local/bin/zarf /var/lib/rancher/rke2/bin/zarf zarf; do
  if [ -x "$c" ] || command -v "$c" >/dev/null 2>&1; then ZARF_BIN="$c"; break; fi
done
KUBECTL_CMD=""
for c in /var/lib/rancher/rke2/bin/kubectl /usr/local/bin/kubectl kubectl; do
  if [ -x "$c" ] || command -v "$c" >/dev/null 2>&1; then
    KUBECTL_CMD="$c --kubeconfig $KUBECONFIG"; break
  fi
done
if [ -z "$KUBECTL_CMD" ]; then
  if [ -n "$ZARF_BIN" ]; then
    KUBECTL_CMD="$ZARF_BIN tools kubectl --kubeconfig $KUBECONFIG"
  else
    echo "❌ no kubectl or zarf binary found on PATH." >&2; exit 2
  fi
fi

# --- locate the transported deploy package (optional for verify / kubectl-only) -
if [ -z "$PKG_ARG" ]; then
  PKG_ARG="$(ls -t /var/tmp/zarf-package-cybersec-dask-amd64-*.tar.zst 2>/dev/null | head -1 || true)"
fi
[ -n "$PKG_ARG" ] && [ -f "$PKG_ARG" ] && PKG_ARG="$(cd "$(dirname "$PKG_ARG")" && pwd)/$(basename "$PKG_ARG")"

# `zarf init` needs the zarf-INIT package (registry/agent/injector images) IN the
# closed world. It has NO --init-package flag and only looks in the CWD or next to the
# zarf binary, so we discover it beside the deploy package and run the engine FROM that
# dir — that's how `zarf init` finds it air-gapped. The init package is LAYER A:
# transport zarf-init-<arch>-<zarfver>.tar.zst alongside the deploy package.
RUN_DIR="$PWD"
if [ -n "$PKG_ARG" ] && [ -f "$PKG_ARG" ]; then
  PKG_DIR="$(dirname "$PKG_ARG")"
  INIT_PKG="$(ls -t "$PKG_DIR"/zarf-init-*-*.tar.zst /var/tmp/zarf-init-*-*.tar.zst 2>/dev/null | head -1 || true)"
  if [ -n "$INIT_PKG" ]; then
    RUN_DIR="$(cd "$(dirname "$INIT_PKG")" && pwd)"
    echo "   zarf-init: $(basename "$INIT_PKG") (engine runs from $RUN_DIR so zarf init finds it)"
  else
    echo "   ⚠ NO zarf-init-*.tar.zst beside the deploy package — 'zarf init' will FAIL air-gapped."
    echo "     Transport it into $PKG_DIR (Layer A: registry/agent/injector images)."
  fi
fi

# --- S3 creds → tmpfs creds file (off argv); the engine forwards as ZARF_VAR_* -
CREDS_FILE="${CONVERGE_CREDS_FILE:-}"
OWN_CREDS=""   # set only if WE created it (a caller-provided file is the caller's to remove)
# Fallback: converge-aws.sh stages creds at this fixed tmpfs path and exports
# CONVERGE_CREDS_FILE — but that env can be dropped crossing `sudo -n env`, which
# silently strips S3 vars from the deploy (empty S3_BUCKET → "s3:" bucket errors).
# Pick the file up by its known path so a lost env var can't lose the creds.
if [ -z "$CREDS_FILE" ] && [ -f /dev/shm/.converge-creds ]; then
  CREDS_FILE="/dev/shm/.converge-creds"
fi
if [ -z "$CREDS_FILE" ]; then
  CREDS_LINES=""
  for v in S3_ENDPOINT S3_BUCKET S3_REGION S3_ACCESS_KEY S3_SECRET_KEY S3_SESSION_TOKEN; do
    [ -n "${!v:-}" ] && CREDS_LINES+="$v=${!v}"$'\n'
  done
  if [ -n "$CREDS_LINES" ]; then
    if [ -d /dev/shm ]; then CREDS_FILE="/dev/shm/.converge-creds.$$"; else CREDS_FILE="/tmp/.converge-creds.$$"; fi
    ( umask 077; printf '%s' "$CREDS_LINES" > "$CREDS_FILE" )
    OWN_CREDS="$CREDS_FILE"
    echo "   creds: $(printf '%s' "$CREDS_LINES" | grep -c .) S3 var(s) staged to tmpfs (off argv)"
  fi
fi
cleanup() { [ -n "$OWN_CREDS" ] && { shred -u "$OWN_CREDS" 2>/dev/null || rm -f "$OWN_CREDS"; }; return 0; }
trap cleanup EXIT

# --- assemble the engine argv (single array → safe under `set -u` on old bash) -
ARGS=(--kubectl "$KUBECTL_CMD")
[ -f "$MANIFEST_JSON" ] && ARGS+=(--manifest "$MANIFEST_JSON")
[ -d "$MANIFESTS_DIR" ] && ARGS+=(--manifests-dir "$MANIFESTS_DIR")
if [ -n "$PKG_ARG" ] && [ -f "$PKG_ARG" ]; then
  [ -n "$ZARF_BIN" ] && ARGS+=(--zarf "$ZARF_BIN")
  ARGS+=(--package "$PKG_ARG")
  echo "   package: $PKG_ARG"
else
  echo "   package: <none> — component-deploy fixes report MANUAL (verify/teardown still work)"
fi
[ -n "$CREDS_FILE" ] && ARGS+=(--creds-file "$CREDS_FILE")
ARGS+=(--set "DASK_WORKER_REPLICAS=${DASK_WORKER_REPLICAS:-1}")
# Optional terminal-WS override (NodePort/tunnel access; empty = auto-detect / ingress /ws)
[ -n "${PTY_PROXY_WS:-}" ] && ARGS+=(--set "PTY_PROXY_WS=${PTY_PROXY_WS}")
case "${CONVERGE_DYNAMIC_PROVISIONING:-}" in 1|true|yes) ARGS+=(--enable-dynamic-provisioning) ;; esac
case "${CONVERGE_NO_REGISTRY_PVC:-}" in 1|true|yes) ARGS+=(--no-registry-pvc) ;; esac
ARGS+=("$MODE_FLAG")

echo "🎯 converge ($MODE)   engine=$ZARF_DIR   kubectl=${KUBECTL_CMD%% *}"

# --- prepare the registry hostPath (resilient claimRef PV) -------------------
# The claimRef registry PV is a hostPath; kubelet creates it ROOT-OWNED, but the zarf
# registry container runs NON-root and fsGroup does NOT chown hostPath volumes — so the
# registry can't write its storage ("mkdir /var/lib/registry/docker: permission denied")
# and every image push 500s. Make it writable BEFORE the engine runs zarf init. The path
# matches REGISTRY_PV_YAML in zarf/converge/catalog.py; harmless in dynamic mode (unused).
if [ "$(id -u)" = 0 ]; then
  mkdir -p /var/lib/zarf-registry && chmod 0777 /var/lib/zarf-registry \
    && echo "   registry hostPath /var/lib/zarf-registry prepared (writable — registry runs non-root)"
fi

# --- run the engine ----------------------------------------------------------
# Run FROM $RUN_DIR (the dir holding the zarf-init package) so `zarf init` finds it
# air-gapped. Every engine path (PYTHONPATH, --manifest(s), --package, --creds-file) is
# absolute, so the cd is safe. `python3 -u` + PYTHONUNBUFFERED keep the progress log live
# through `tee` (block-buffering hides it otherwise); PYTHONDONTWRITEBYTECODE avoids a
# root-owned __pycache__ that would block the next restage.
set +e
cd "$RUN_DIR"
env PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH="$ZARF_DIR" KUBECONFIG="$KUBECONFIG" \
  python3 -u -m converge "${ARGS[@]}"
RC=$?
set -e
exit "$RC"
