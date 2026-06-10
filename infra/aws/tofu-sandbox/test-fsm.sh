#!/usr/bin/env bash
# ONE-command, end-to-end live validation of the T1 registry/storage FSM (catalog.py
# _pre_init_cleanup / _rem_registry_running).
#
# Drives the COMPLETE sequence itself — provision a throwaway air-gap RKE2 node,
# transport THIS repo's engine, cut egress — then for each wedged registry/storage
# permutation it INDUCES the state, converges, and asserts T1.registry-running recovers
# to [ok]/[fixed], reporting the FSM's own unwind. Destroys the node on success (kept on
# --keep, or on any failure for inspection). This is the live validation the unit tests
# can't give: real PVC↔PV binding, real StorageClass capture, real conservation of the
# registry images across a PV reset.
#
#   just sandbox-test-fsm           # full cycle → destroy
#   just sandbox-test-fsm --keep    # full cycle → keep the node
#
# (The `just` recipe runs sandbox-config first to hydrate build/sandbox/.)
set -uo pipefail   # NOT -e: the matrix must continue past a failing case.

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SB="$SELF_DIR/run-sandbox.sh"
REPO_ROOT="$(cd "$SELF_DIR/../../.." && pwd)"
BUILD_DIR="${SANDBOX_BUILD_DIR:-$REPO_ROOT/build/sandbox}"
ENV_FILE="$BUILD_DIR/sandbox.env"
[ -f "$ENV_FILE" ] || { echo "❌ $ENV_FILE missing — run 'just sandbox-config' first" >&2; exit 2; }
# shellcheck source=/dev/null
source "$ENV_FILE"   # KEY, S3_*, ...

KEEP=0; [ "${1:-}" = "--keep" ] && KEEP=1
SSH_OPTS=(-i "$KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=15)
KCTL="sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml"
IP=""
PASS=0; FAIL=0; FAILED_CASES=()
on_node() { ssh "${SSH_OPTS[@]}" "ec2-user@$IP" "$@"; }
_strip() { perl -pe 's/\e\[[0-9;]*[a-zA-Z]//g' 2>/dev/null || cat; }

finish() {
  local code="${1:-0}"
  if [ "$KEEP" = 1 ]; then
    echo "→ node KEPT (--keep)${IP:+ ($IP)} — 'just sandbox-destroy' when done."
  elif [ "$code" -eq 0 ]; then
    echo "→ all green — destroying the sandbox"; bash "$SB" destroy || true
  else
    echo "→ failure/abort — node KEPT${IP:+ ($IP)} for inspection. 'just sandbox-destroy' when done."
  fi
  exit "$code"
}

# ── full lifecycle (the one-command sequence) ─────────────────────────────────
echo "▶ End-to-end FSM validation: provision → transport → air-gap → matrix"
echo "── provision (egress ON) + wait for RKE2 Ready ──"
bash "$SB" up        || { echo "❌ provision (up) failed"; finish 1; }
echo "── transport packages + stage THIS repo's engine + MinIO (egress ON) ──"
bash "$SB" transport || { echo "❌ transport failed"; finish 1; }
echo "── cut egress (closed world) ──"
bash "$SB" airgap    || { echo "❌ airgap failed"; finish 1; }

IP="$(bash "$SB" ip 2>/dev/null)"
[ -n "$IP" ] && [ "$IP" != "None" ] || { echo "❌ node IP unresolved after up"; finish 1; }
on_node "rm -rf ~/cybersec-converge/converge/__pycache__" 2>/dev/null || true
echo "✓ node $IP up, air-gapped, engine staged — running the matrix"

# induce → converge → assert T1.registry-running recovered.
run_case() {
  local name="$1" induce_fn="$2"
  echo; echo "═══════════ CASE: $name"
  "$induce_fn"
  local out clean t1 act
  out="$(bash "$SB" converge 2>&1)" || true
  clean="$(printf '%s\n' "$out" | _strip)"
  t1="$(printf '%s\n' "$clean" | grep -E 'T1[[:space:]].*registry-running' | head -1)"
  act="$(printf '%s\n' "$clean" | grep -oE \
        'un-defaulted StorageClass[^];]*|reset static registry PV[^];]*|force-finalized[^];]*|created static registry PV[^];]*' \
        | head -3 | paste -sd'; ' -)"
  if printf '%s' "$t1" | grep -qiE '\[ *(ok|fixed) *\]'; then
    echo "  ✓ T1 recovered:${t1#*registry-running}"
    [ -n "$act" ] && echo "    ↳ FSM unwind: $act"
    PASS=$((PASS + 1))
  else
    echo "  ✗ T1 did NOT recover"
    echo "    ${t1:-<no T1.registry-running line in output>}"
    printf '%s\n' "$clean" | grep -iE 'rc=1|MANUAL|Pending|won.t bind|ExternalProvision|registry' \
      | tail -5 | sed 's/^/      /'
    FAIL=$((FAIL + 1)); FAILED_CASES+=("$name")
  fi
}

# ── inducers ──────────────────────────────────────────────────────────────────
# Deleting the zarf ns tears down ONLY the registry; the app survives in its own
# namespaces and the pushed images survive on the Retain hostPath — so each converge
# re-inits T1 fast (T2 images stay [ok]). That image survival also validates CONSERVATION.

induce_baseline() { :; }   # a plain converge must stay green

induce_default_sc() {      # THE field bug: a default SC captures the fresh registry PVC
  cat <<'Y' | on_node "$KCTL apply -f -" >/dev/null 2>&1 || true
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: sb-fsm-default
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: example.com/sb-nonexistent-provisioner
Y
  on_node "$KCTL delete ns zarf --wait=false" >/dev/null 2>&1 || true
}

induce_released_pv() {     # delete the PVC out from under the Retain PV → Released
  on_node "$KCTL -n zarf delete pvc zarf-docker-registry --wait=false" >/dev/null 2>&1 || true
  on_node "$KCTL delete ns zarf --wait=false" >/dev/null 2>&1 || true
}

induce_class_drift() {     # the static PV carries a class the (un-defaulted) "" PVC can't bind
  on_node "$KCTL delete ns zarf --wait=false" >/dev/null 2>&1 || true
  on_node "$KCTL delete pv zarf-registry-pv --ignore-not-found" >/dev/null 2>&1 || true
  cat <<'Y' | on_node "$KCTL apply -f -" >/dev/null 2>&1 || true
apiVersion: v1
kind: PersistentVolume
metadata: {name: zarf-registry-pv}
spec:
  capacity: {storage: 5Gi}
  accessModes: [ReadWriteOnce]
  persistentVolumeReclaimPolicy: Retain
  storageClassName: "sb-bogus-drift"
  hostPath: {path: /var/lib/zarf-registry, type: DirectoryOrCreate}
  claimRef: {namespace: zarf, name: zarf-docker-registry}
Y
}

# ── the permutation matrix ────────────────────────────────────────────────────
run_case "baseline (idempotent converge stays green)" induce_baseline
run_case "default StorageClass capture (the field bug)" induce_default_sc
run_case "Released registry PV (PVC deleted)"           induce_released_pv
run_case "class-drifted static PV"                      induce_class_drift

on_node "$KCTL delete storageclass sb-fsm-default --ignore-not-found" >/dev/null 2>&1 || true

echo; echo "════════════ FSM validation: ${PASS} passed, ${FAIL} failed ════════════"
[ "$FAIL" -gt 0 ] && printf '   failed: %s\n' "${FAILED_CASES[*]}"
finish "$FAIL"
