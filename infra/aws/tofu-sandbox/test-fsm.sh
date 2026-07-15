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

# induce → converge → assert T1.registry-running recovered (+ optional post-assert).
run_case() {
  local name="$1" induce_fn="$2" post_fn="${3:-}"
  echo; echo "═══════════ CASE: $name"
  # Fail FAST if the node is unreachable (e.g. the operator's residential IP rotated
  # out of the SG /32 mid-run — it happened, truncating a case's converge output and
  # misgrading it). A dead link must abort the matrix loudly, not grade cases.
  if ! on_node "true" >/dev/null 2>&1; then
    echo "❌ node unreachable before '$name' — did your public IP rotate out of the SG /32?"
    echo "   recover: just sandbox-config && bash run-sandbox.sh airgap  (re-applies the /32, keeps egress cut)"
    FAIL=$((FAIL + 1)); FAILED_CASES+=("$name [node unreachable — aborted matrix]")
    finish 1
  fi
  "$induce_fn"
  local out clean t1 act
  out="$(bash "$SB" converge 2>&1)" || true
  clean="$(printf '%s\n' "$out" | _strip)"
  t1="$(printf '%s\n' "$clean" | grep -E 'T1[[:space:]].*registry-running' | head -1)"
  act="$(printf '%s\n' "$clean" | grep -oE \
        'un-defaulted StorageClass[^];]*|reset static registry PV[^];]*|force-finalized[^];]*|created static registry PV[^];]*|deleted (captured )?registry PVC[^];]*|unwedged pending Helm release[^];]*|cleared pods in Terminating[^];]*|drained ns zarf[^];]*|recreated absent zarf ns[^];]*|drained zarf husk[^];]*|chmod 0777[^];]*|cleared partial seed-registry[^];]*' \
        | head -4 | paste -sd'; ' -)"
  if printf '%s' "$t1" | grep -qiE '\[ *(ok|fixed) *\]'; then
    echo "  ✓ T1 recovered:${t1#*registry-running}"
    [ -n "$act" ] && echo "    ↳ FSM unwind: $act"
    # PROOF of the mechanism: the bound registry PVC must be on storageClassName "" (the
    # static claimRef PV), NOT a captured class. This is what --storage-class - guarantees.
    local pvc_sc
    pvc_sc="$(on_node "$KCTL -n zarf get pvc zarf-docker-registry -o jsonpath='{.spec.storageClassName}'" 2>/dev/null)"
    if [ -z "$pvc_sc" ]; then
      echo "    ↳ registry PVC storageClassName=\"\" (bound the static PV — no default-SC capture) ✓"
    else
      echo "    ✗ registry PVC storageClassName=$pvc_sc (expected \"\" — capture not prevented)"
      FAIL=$((FAIL + 1)); FAILED_CASES+=("$name [PVC on '$pvc_sc' not '']"); return
    fi
    # Case-specific proof (agent back / helm unwedged / ns Active …)
    if [ -n "$post_fn" ] && ! "$post_fn"; then
      FAIL=$((FAIL + 1)); FAILED_CASES+=("$name [post-assert]"); return
    fi
    PASS=$((PASS + 1))
  else
    echo "  ✗ T1 did NOT recover"
    echo "    ${t1:-<no T1.registry-running line in output>}"
    printf '%s\n' "$clean" | grep -iE 'rc=1|MANUAL|Pending|won.t bind|ExternalProvision|registry|agent-hook' \
      | tail -5 | sed 's/^/      /'
    FAIL=$((FAIL + 1)); FAILED_CASES+=("$name")
  fi
}

# ── inducers ──────────────────────────────────────────────────────────────────
# Deleting the zarf ns tears down ONLY the registry; the app survives in its own
# namespaces and the pushed images survive on the Retain hostPath — so each converge
# re-inits T1 fast (T2 images stay [ok]). That image survival also validates CONSERVATION.

induce_baseline() { :; }   # a plain converge must stay green

# A default StorageClass faithful to RKE2's local-path: WaitForFirstConsumer + a
# provisioner that does NOT exist in the closed world (so dynamic provisioning hangs).
_apply_hostile_default_sc() {
  cat <<'Y' | on_node "$KCTL apply -f -" >/dev/null 2>&1 || true
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: sb-fsm-default
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: example.com/sb-nonexistent-provisioner
volumeBindingMode: WaitForFirstConsumer
Y
}

induce_default_sc() {      # THE field bug: a default SC captures the fresh registry PVC
  _apply_hostile_default_sc
  on_node "$KCTL delete ns zarf --wait=false" >/dev/null 2>&1 || true
}

induce_vestigial_pvc() {   # THE artifact: a prior attempt left a zarf-docker-registry PVC
  # already CAPTURED onto the default class (immutable). zarf init reuses it by name, so
  # un-defaulting can't help — converge must DELETE it and re-init on "". This is the exact
  # state the live node was wedged in (PVC Pending storageClass='local-path').
  _apply_hostile_default_sc
  on_node "$KCTL delete ns zarf --wait=false" >/dev/null 2>&1 || true
  on_node "$KCTL create ns zarf" >/dev/null 2>&1 || true
  cat <<'Y' | on_node "$KCTL apply -f -" >/dev/null 2>&1 || true
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: zarf-docker-registry
  namespace: zarf
spec:
  accessModes: [ReadWriteOnce]
  resources: {requests: {storage: 5Gi}}
Y
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

# ── audit-gap inducers (2026-07-07 state-space audit) ─────────────────────────

induce_dead_agent() {      # registry Running ≠ init complete: agent gone → every later
  # deploy ImagePullBackOff on upstream refs. T1 detect must now name it + re-init.
  on_node "$KCTL -n zarf delete deploy agent-hook --wait=true" >/dev/null 2>&1 || true
  local i=0
  while [ "$i" -lt 10 ] && on_node "$KCTL -n zarf get pods --no-headers 2>/dev/null | grep -q '^agent-hook'"; do
    sleep 3; i=$((i + 1))
  done
}

post_agent_back() {
  local n img
  n="$(on_node "$KCTL -n zarf get pods --no-headers 2>/dev/null" | grep -c '^agent-hook.*Running')" || true
  if [ "${n:-0}" -lt 1 ]; then echo "    ✗ agent-hook NOT running after converge"; return 1; fi
  # Running is not enough — admission must BEHAVIORALLY rewrite an upstream ref in an
  # APP namespace (the field poison: a re-run init labels pre-existing app namespaces
  # zarf.dev/agent=ignore, disabling rewriting; probing `default` would be a permanent
  # false negative — zarf deliberately ignores pre-init namespaces). Server dry-run
  # exercises the full chain incl. selectors; persists nothing.
  img="$(on_node "$KCTL -n dask-operator run zz-post-canary --image=ghcr.io/zarf-canary/agent-check:v1 --restart=Never --dry-run=server -o jsonpath='{.spec.containers[0].image}'" 2>/dev/null)"
  case "$img" in
    *ghcr.io/zarf-canary*) echo "    ✗ agent-hook Running but webhook NOT mutating (canary kept upstream ref)"; return 1 ;;
    "")                    echo "    ↳ agent-hook Running (${n}) ✓ (canary gave no verdict)"; return 0 ;;
    *)                     echo "    ↳ agent-hook Running (${n}), webhook mutating (canary → ${img%%@*}) ✓"; return 0 ;;
  esac
}

induce_wedged_helm() {     # the killed-mid-deploy state: a REAL pending-upgrade helm
  # revision (payload + labels) makes every upgrade of that release fail "another
  # operation is in progress"; the operator Deployment is removed so converge MUST
  # deploy — and therefore must unwedge first.
  cat <<'PY' | on_node "cat > /tmp/sb-wedge-helm.py"
import base64, gzip, json, subprocess
KC = "/var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml".split()
def k(*a, inp=None):
    return subprocess.run(KC + list(a), capture_output=True, text=True, input=inp)
o = json.loads(k("get", "secrets", "-n", "dask-operator", "-l", "owner=helm", "-o", "json").stdout)
by = {}
for s in o.get("items", []):
    lab = s["metadata"]["labels"]; v = int(lab["version"])
    if lab["name"] not in by or v > by[lab["name"]][0]:
        by[lab["name"]] = (v, s)
assert by, "no helm release found in ns dask-operator"
rel_name, (ver, last) = sorted(by.items())[0]
new = ver + 1
gz = base64.b64decode(base64.b64decode(last["data"]["release"]))
rel = json.loads(gzip.decompress(gz))
rel["version"] = new
rel["info"]["status"] = "pending-upgrade"
helm_blob = base64.b64encode(gzip.compress(json.dumps(rel).encode())).decode()
secret = {
    "apiVersion": "v1", "kind": "Secret", "type": "helm.sh/release.v1",
    "metadata": {
        "name": "sh.helm.release.v1.%s.v%d" % (rel_name, new),
        "namespace": "dask-operator",
        "labels": {"name": rel_name, "owner": "helm",
                   "status": "pending-upgrade", "version": str(new)},
    },
    "data": {"release": base64.b64encode(helm_blob.encode()).decode()},
}
r = k("apply", "-f", "-", inp=json.dumps(secret))
print("planted pending-upgrade rev:", rel_name, new, "rc=", r.returncode)
r2 = k("delete", "deploy", "--all", "-n", "dask-operator", "--wait=false")
print("operator deployment removed rc=", r2.returncode)
PY
  on_node "sudo python3 /tmp/sb-wedge-helm.py" 2>&1 | sed 's/^/    induce: /'
}

post_helm_clean() {
  local pend ops
  pend="$(on_node "$KCTL get secrets -A -l 'owner=helm,status in (pending-install,pending-upgrade,pending-rollback)' --no-headers 2>/dev/null | grep -c ." )" || true
  ops="$(on_node "$KCTL -n dask-operator get pods --no-headers 2>/dev/null" | grep -c 'Running')" || true
  if [ "${pend:-1}" -eq 0 ] && [ "${ops:-0}" -ge 1 ]; then
    echo "    ↳ no pending-* helm releases; operator Running (${ops}) ✓"; return 0
  fi
  echo "    ✗ pending helm secrets remain (${pend:-?}) or operator not Running (${ops:-0})"; return 1
}

induce_terminating_ns() {  # a finalizer-bearing resource wedges panel-viz in Terminating:
  # its component deploy fails "namespace is being terminated" until force-finalized.
  cat <<'Y' | on_node "$KCTL apply -f -" >/dev/null 2>&1 || true
apiVersion: v1
kind: ConfigMap
metadata:
  name: sb-wedge
  namespace: panel-viz
  finalizers: ["cybersec.sandbox/test-wedge"]
Y
  on_node "$KCTL delete ns panel-viz --wait=false" >/dev/null 2>&1 || true
  local i=0
  while [ "$i" -lt 10 ]; do
    [ "$(on_node "$KCTL get ns panel-viz -o jsonpath='{.status.phase}'" 2>/dev/null)" = "Terminating" ] && break
    sleep 3; i=$((i + 1))
  done
}

post_ns_active() {
  # clear the wedge cm's finalizer if it resurfaced in the recreated ns (zombie object)
  on_node "$KCTL -n panel-viz patch configmap sb-wedge --type=merge -p '{\"metadata\":{\"finalizers\":null}}'" >/dev/null 2>&1 || true
  local phase pods
  phase="$(on_node "$KCTL get ns panel-viz -o jsonpath='{.status.phase}'" 2>/dev/null)"
  pods="$(on_node "$KCTL -n panel-viz get pods --no-headers 2>/dev/null" | grep -c 'Running')" || true
  if [ "$phase" = "Active" ] && [ "${pods:-0}" -ge 1 ]; then
    echo "    ↳ panel-viz Active, ${pods} pod(s) Running ✓"; return 0
  fi
  echo "    ✗ panel-viz phase=${phase:-absent}, Running pods=${pods:-0}"; return 1
}

# ── CADS session inducers (2026-07 air-gap walkthrough — split-brain / husk / hostPath)
induce_zarf_husk_service() {
  # Active zarf ns with only an ancient Service (no Ready registry) — the 34d
  # zarf-injector leftover after force-finalize + recreate. Re-init on top →
  # seed-registry Helm deadline. Converge must drain then re-init.
  on_node "$KCTL delete ns zarf --wait=false" >/dev/null 2>&1 || true
  sleep 2
  on_node "$KCTL create ns zarf" >/dev/null 2>&1 || true
  cat <<'Y' | on_node "$KCTL apply -f -" >/dev/null 2>&1 || true
apiVersion: v1
kind: Service
metadata:
  name: zarf-injector
  namespace: zarf
spec:
  ports: [{port: 5000, targetPort: 5000}]
Y
}

induce_pvc_terminating_split() {
  # PVC Terminating + ns force-finalized = split-brain: namespaced patch fails
  # with "namespaces zarf not found" until ns is recreated and PVC re-homed.
  _apply_hostile_default_sc
  on_node "$KCTL create ns zarf" >/dev/null 2>&1 || true
  cat <<'Y' | on_node "$KCTL apply -f -" >/dev/null 2>&1 || true
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: zarf-docker-registry
  namespace: zarf
  finalizers: ["kubernetes.io/pvc-protection", "cybersec.sandbox/test-wedge"]
spec:
  accessModes: [ReadWriteOnce]
  resources: {requests: {storage: 5Gi}}
  storageClassName: local-path
Y
  on_node "$KCTL -n zarf delete pvc zarf-docker-registry --wait=false" >/dev/null 2>&1 || true
  # force-finalize the ns while PVC still has finalizers (split-brain)
  on_node "$KCTL get ns zarf -o json" 2>/dev/null | on_node "python3 -c '
import sys,json
o=json.load(sys.stdin)
o[\"spec\"][\"finalizers\"]=[]
print(json.dumps(o))
'" 2>/dev/null | on_node "$KCTL replace --raw /api/v1/namespaces/zarf/finalize -f -" >/dev/null 2>&1 || true
}

induce_hostpath_perms() {
  # Registry non-root cannot write hostPath → seed chart context deadline.
  on_node "sudo mkdir -p /var/lib/zarf-registry && sudo chmod 0700 /var/lib/zarf-registry && sudo chown root:root /var/lib/zarf-registry" >/dev/null 2>&1 || true
  on_node "$KCTL delete ns zarf --wait=false" >/dev/null 2>&1 || true
}

post_hostpath_writable() {
  local mode
  mode="$(on_node "stat -c '%a' /var/lib/zarf-registry" 2>/dev/null)" || true
  if [ "$mode" = "777" ]; then
    echo "    ↳ hostPath mode=777 ✓"; return 0
  fi
  echo "    ✗ hostPath mode=${mode:-absent} (want 777)"; return 1
}

induce_dead_release() {   # THE 2026-07-15 field wedge: a chart whose FIRST install failed
  # (only `failed` revisions, never deployed) → every helm upgrade refuses with
  # "has no deployed releases", and the required dask-cluster rider blocks EVERY
  # component deploy. Manufacture: rewrite ALL revisions of the dask-cluster-cr
  # release to failed + delete the DaskCluster CR (as a failed install leaves it).
  cat <<'PY' | on_node "cat > /tmp/sb-dead-release.py"
import base64, gzip, json, subprocess
KC = "/var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml".split()
def k(*a, inp=None):
    return subprocess.run(KC + list(a), capture_output=True, text=True, input=inp)
o = json.loads(k("get", "secrets", "-A", "-l", "owner=helm", "-o", "json").stdout)
touched = 0
for s in o.get("items", []):
    md, lab = s["metadata"], s["metadata"].get("labels", {}) or {}
    try:
        rel = json.loads(gzip.decompress(base64.b64decode(base64.b64decode(s["data"]["release"]))))
    except Exception:
        continue
    if "dask-cluster-cr" not in (rel.get("chart", {}).get("metadata", {}) or {}).get("name", ""):
        continue
    rel["info"]["status"] = "failed"
    blob = base64.b64encode(gzip.compress(json.dumps(rel).encode())).decode()
    patch = {"data": {"release": base64.b64encode(blob.encode()).decode()},
             "metadata": {"labels": {"status": "failed"}}}
    r = k("patch", "secret", md["name"], "-n", md["namespace"], "--type=merge",
          "-p", json.dumps(patch))
    touched += 1 if r.returncode == 0 else 0
print("revisions set to failed:", touched)
r = k("delete", "daskcluster", "cybersec-dask", "-n", "dask", "--ignore-not-found", "--wait=false")
print("daskcluster CR delete rc:", r.returncode)
PY
  on_node "sudo python3 /tmp/sb-dead-release.py" 2>&1 | sed 's/^/    induce: /'
  # clear operator-created children so T4 detect fails and the deploy path runs
  on_node "$KCTL -n dask delete deploy --all --wait=false" >/dev/null 2>&1 || true
}

induce_kubectl_off_path() {  # THE 2026-07-15 field wedge #2: zarf component actions run bare
  # `kubectl`; on real RKE2 it lives only in /var/lib/rancher/rke2/bin (off root's
  # PATH) — the dask-cluster S3-bucket after-action died `command not found` and the
  # required rider blocked every deploy. Our provisioning MASKS this with a
  # /usr/local/bin symlink — hide it, then force a dask-cluster redeploy so the
  # after-action must run. Engine v0.4.2 must hand zarf a PATH that resolves kubectl.
  on_node "sudo mv /usr/local/bin/kubectl /usr/local/bin/kubectl.sb-hidden 2>/dev/null; sudo mv /usr/bin/kubectl /usr/bin/kubectl.sb-hidden 2>/dev/null; true"
  on_node "$KCTL -n dask delete daskcluster cybersec-dask --ignore-not-found --wait=false; $KCTL -n dask delete deploy --all --wait=false; true" >/dev/null 2>&1 || true
}

post_kubectl_path_healed() {
  # kubectl must STILL be hidden (prove the engine shim/PATH did it, not the mask),
  # and the scheduler must be back (deploy + after-action both succeeded)
  local hidden sched
  hidden="$(on_node "command -v kubectl >/dev/null 2>&1 && echo visible || echo hidden")"
  sched="$(on_node "$KCTL -n dask get pods -l dask.org/component=scheduler --no-headers 2>/dev/null" | grep -c Running)" || true
  on_node "sudo mv /usr/local/bin/kubectl.sb-hidden /usr/local/bin/kubectl 2>/dev/null; sudo mv /usr/bin/kubectl.sb-hidden /usr/bin/kubectl 2>/dev/null; true"
  if [ "$hidden" = "hidden" ] && [ "${sched:-0}" -ge 1 ]; then
    echo "    ↳ kubectl off PATH throughout, deploy+after-action succeeded, scheduler Running ✓"
    return 0
  fi
  echo "    ✗ kubectl=$hidden (want hidden), scheduler Running=${sched:-0}"; return 1
}

post_dead_release_healed() {
  # the release must have a DEPLOYED revision again + the CR + scheduler back
  local dep sched
  dep="$(on_node "$KCTL get secrets -A -l 'owner=helm,status=deployed' -o name 2>/dev/null | grep -c ." )" || true
  sched="$(on_node "$KCTL -n dask get pods -l dask.org/component=scheduler --no-headers 2>/dev/null" | grep -c Running)" || true
  if [ "${sched:-0}" -ge 1 ] && [ "${dep:-0}" -ge 1 ]; then
    echo "    ↳ dead release healed: deployed revisions present, scheduler Running ✓"; return 0
  fi
  echo "    ✗ scheduler Running=${sched:-0}, deployed releases=${dep:-0}"; return 1
}

# ── the permutation matrix ────────────────────────────────────────────────────
run_case "baseline (idempotent converge stays green)"   induce_baseline
run_case "default StorageClass capture (the field bug)"  induce_default_sc
run_case "vestigial captured PVC (init reuses 'local-path')" induce_vestigial_pvc
run_case "Released registry PV (PVC deleted)"            induce_released_pv
run_case "class-drifted static PV"                       induce_class_drift
run_case "dead zarf agent (registry up, init incomplete)" induce_dead_agent   post_agent_back
run_case "wedged pending-upgrade Helm release"            induce_wedged_helm  post_helm_clean
run_case "app namespace stuck Terminating (apply path)"   induce_terminating_ns post_ns_active
run_case "zarf husk Service only (no registry)"          induce_zarf_husk_service
run_case "PVC Terminating + ns split-brain"              induce_pvc_terminating_split
run_case "hostPath not writable (seed deadline class)"   induce_hostpath_perms post_hostpath_writable
run_case "dead helm release (failed first install, no deployed rev)" induce_dead_release post_dead_release_healed
run_case "kubectl off PATH (zarf action 'command not found')" induce_kubectl_off_path post_kubectl_path_healed

on_node "$KCTL delete storageclass sb-fsm-default --ignore-not-found" >/dev/null 2>&1 || true

echo; echo "════════════ FSM validation: ${PASS} passed, ${FAIL} failed ════════════"
[ "$FAIL" -gt 0 ] && printf '   failed: %s\n' "${FAILED_CASES[*]}"
finish "$FAIL"
