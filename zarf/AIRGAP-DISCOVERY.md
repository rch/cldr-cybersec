# Air-Gap K8s/RKE2 Discovery Phase — Pre-Converge State Audit

**Purpose.** A read-only, copy-paste walkthrough to confirm **every** aspect of the current
air-gapped RKE2 deployment **before** running `converge-node.sh` — with special attention to the
**StorageClass / PVC situation** that has repeatedly wedged `zarf init`. Nothing here mutates the
cluster. Pair it with [AIRGAP-REMEDIATION-COMMANDS.md](AIRGAP-REMEDIATION-COMMANDS.md) (the fix
side) and [AIRGAP-CONVERGE-RUNBOOK.md](AIRGAP-CONVERGE-RUNBOOK.md) (the narrative).

This audit mirrors the convergence engine's own read-only `detect()` probes (tiers T0→T6 in
`zarf/converge/catalog.py`), so what you confirm by hand is exactly what `converge-node.sh verify`
evaluates — run that too once the basics check out.

---

## 0. Setup — run as root on the control-plane node

RKE2's kubeconfig is root-only. Define two helpers in your shell (copy-paste as a block; the
functions persist for the rest of your session):

```bash
# kubectl against the RKE2 cluster (falls back to `zarf tools kubectl` if the binary moved)
export KUBECONFIG=/etc/rancher/rke2/rke2.yaml
kc()  { /var/lib/rancher/rke2/bin/kubectl --kubeconfig "$KUBECONFIG" "$@" 2>/dev/null \
        || zarf tools kubectl --kubeconfig "$KUBECONFIG" "$@"; }
# crictl against RKE2's containerd (for the on-node image / Layer-A audit)
cri() { /var/lib/rancher/rke2/bin/crictl --runtime-endpoint unix:///run/k3s/containerd/containerd.sock "$@"; }

id -u   # must be 0 (root) — if not, prefix everything with sudo
```

Optional one-shot snapshot to a file you can keep with the run record (see §12).

---

## 1. T0 — Node & RKE2 health

```bash
systemctl status rke2-server --no-pager | head -20      # control plane up?
kc get nodes -o wide                                     # every node Ready?
kc get nodes -o custom-columns='NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status,MEMPRESS:.status.conditions[?(@.type=="MemoryPressure")].status,DISKPRESS:.status.conditions[?(@.type=="DiskPressure")].status,PIDPRESS:.status.conditions[?(@.type=="PIDPressure")].status,UNSCHED:.spec.unschedulable,TAINTS:.spec.taints[*].key'
kc get nodes -o custom-columns='NAME:.metadata.name,CPU:.status.allocatable.cpu,MEM:.status.allocatable.memory'
```

| Confirm | Good | Red flag → see remediation |
|---|---|---|
| Node `Ready=True` | yes | `NotReady` → §T0 (kubelet/CNI/RKE2) |
| `unschedulable` / cordon | `False` | `True` → uncordon (T0.node-ready) |
| `node.kubernetes.io/disk-pressure` taint | absent | present → free disk **SAFELY** (T0.no-disk-pressure, **Layer A** — never `crictl rmi --prune`) |
| `MemoryPressure` / `DiskPressure` | `False` | `True` → capacity / disk issue |

Disk headroom + lenient-eviction sanity (the disk-pressure taint is the symptom):

```bash
df -h /var/lib/rancher /var/lib/zarf-registry /var/tmp 2>/dev/null
kc get node -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.allocatable.memory}{"\n"}{end}'
```

**⚠ Existing-node kubelet policy audit.** The on-site RKE2 was NOT provisioned by our ansible, so
it likely runs RKE2 **defaults**: kubelet **image GC at 85% disk** (silently deletes Layer-A images
— *before* any disk-pressure taint appears) and eviction at 10–15%. Confirm what the node actually
has:

```bash
grep -A6 'kubelet-arg' /etc/rancher/rke2/config.yaml 2>/dev/null || echo "NO kubelet-arg overrides — RKE2 DEFAULTS (image GC at 85% WILL prune Layer-A images)"
df -h /var/lib/rancher | awk 'NR==2 {gsub("%","",$5); print ($5>=80) ? "⚠ "$5"% used — within image-GC range on defaults" : "disk "$5"% used (below GC threshold)"}'
```

If defaults are in effect, apply the conservation policy **before** deploying (see the remediation
doc §T0 — literal config block + `systemctl restart rke2-server`).

---

## 2. T0 — Closure: Layer-A images present on the node (containerd)

CLOSURE means everything needed is already inside the closed world. The engine proxies this via
pod health (ImagePullBackOff ⇒ image absent); on the node you can confirm directly with crictl.
**Never pull, never `rmi --prune`** — these images cannot be re-fetched air-gapped.

```bash
cri images | grep -Ei 'cybersec-dask|registry|zarf|agent|injector|local-path|busybox|pause' || echo "NONE matched"
# Transported Layer-A artifacts staged for zarf init / deploy:
ls -lh /var/tmp/zarf-package-cybersec-dask-amd64-*.tar.zst 2>/dev/null
ls -lh /var/tmp/zarf-init-*-*.tar.zst 2>/dev/null          # the piece partial procedures most often LACK
command -v zarf && zarf version                            # the v0.70.1 binary present?
```

| Confirm | Good | Red flag |
|---|---|---|
| deploy package in `/var/tmp` | present | absent → component fixes report MANUAL |
| **zarf-init package** beside it | present | **absent → `zarf init` FAILS air-gapped** (re-transport, Layer A) |
| `zarf` binary | `/usr/local/bin/zarf`, v0.70.1 | absent → install from release |
| bootstrap images (only if `CONVERGE_DYNAMIC_PROVISIONING=1`) | `local-path-provisioner`, `busybox` present | absent → CLOSURE violation, re-import (never pull) |

> **Only ONE deploy-package version should be in `/var/tmp`.** `converge-node.sh` auto-discovers
> the **newest by mtime** — and plain `scp` resets mtime, so a re-copied *older* version can win.
> Remove superseded packages, or always pass the package path explicitly:
> `converge-node.sh apply /var/tmp/zarf-package-cybersec-dask-amd64-<exact-version>.tar.zst`

---

## 3. T0.5 — StorageClass situation  ⚠ the central pre-converge investigation

This is the condition that has repeatedly captured the registry PVC and wedged `zarf init`. Dump
**every** SC with its provisioner, binding mode, reclaim policy, and the **default** annotation:

```bash
kc get storageclass -o custom-columns='NAME:.metadata.name,PROVISIONER:.provisioner,BINDING:.volumeBindingMode,RECLAIM:.reclaimPolicy,DEFAULT:.metadata.annotations.storageclass\.kubernetes\.io/is-default-class' \
  || echo "NO StorageClasses (resilient path expects none — fine)"
```
> A `DEFAULT=true` row whose `BINDING=WaitForFirstConsumer` and `PROVISIONER=rancher.io/local-path`
> with **no running provisioner pod** is the classic captor — exactly the RKE2 built-in `local-path`.

**Decision — is there a default SC, and can its provisioner run air-gapped?**

| Observation | Meaning | Action before converge |
|---|---|---|
| **No StorageClass at all** | the resilient claimRef-PV path's ideal baseline | nothing — the registry binds the static PV |
| A default SC = **RKE2 `local-path`** (`WaitForFirstConsumer`, provisioner pod **not running** air-gapped) | **this is the captor** — it will stamp the registry PVC and hang it `Pending` | the engine un-defaults it + inits with `--storage-class -`; you may pre-confirm with §5 |
| A default SC whose provisioner **is** Running (resourced cluster) | dynamic provisioning genuinely works | consider `CONVERGE_DYNAMIC_PROVISIONING=1` |

Confirm whether the default SC's provisioner is actually alive:

```bash
kc get pods -n local-path-storage -o wide 2>/dev/null || echo "no local-path-storage ns (provisioner not deployed)"
kc -n local-path-storage get pods -l app=local-path-provisioner \
   -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.phase}{"\n"}{end}' 2>/dev/null
```

---

## 4. T0.5 — PersistentVolume situation (esp. the registry PV)

```bash
kc get pv -o custom-columns='NAME:.metadata.name,PHASE:.status.phase,SC:.spec.storageClassName,RECLAIM:.spec.persistentVolumeReclaimPolicy,CAP:.spec.capacity.storage,CLAIM:.spec.claimRef.name,CLAIMUID:.spec.claimRef.uid' \
  || echo "NO PersistentVolumes"
```

Focus on the static registry PV `zarf-registry-pv` and its hostPath data on disk:

```bash
kc get pv zarf-registry-pv -o yaml 2>/dev/null | grep -E 'name:|phase:|storageClassName:|persistentVolumeReclaimPolicy:|path:|claimRef:|uid:' || echo "zarf-registry-pv absent"
ls -ld /var/lib/zarf-registry 2>/dev/null            # exists? perms (should be writable, registry runs non-root)
du -sh /var/lib/zarf-registry 2>/dev/null            # non-trivial size ⇒ pushed images are present (CONSERVE this)
```

| Confirm (`zarf-registry-pv`) | Good (resilient) | Red flag → remediation §T1-D |
|---|---|---|
| `phase` | `Bound` (or `Available` pre-init) | `Released` / `Failed` → reset PV to `""` |
| `storageClassName` | `""` (empty) | non-`""` (drifted) → reset to `""` |
| `persistentVolumeReclaimPolicy` | `Retain` | `Delete` → would lose images; reset |
| `claimRef` | `zarf/zarf-docker-registry` | stale `uid` on an unbound PV → reset |
| hostPath `/var/lib/zarf-registry` | exists, writable, has data | missing/0777-absent → prep (registry 500s on push) |

---

## 5. T0.5 / T1 — PVC situation  ⚠ the registry-PVC capture check

The single most important pre-converge fact. A PVC's `storageClassName` is **immutable**, so a
registry PVC captured onto a dead provisioner can only be **deleted**, not fixed in place.

```bash
kc get pvc -A -o custom-columns='NS:.metadata.namespace,NAME:.metadata.name,PHASE:.status.phase,SC:.spec.storageClassName,VOL:.spec.volumeName' \
  || echo "NO PersistentVolumeClaims"
# The registry PVC specifically, with its binding events:
kc -n zarf get pvc zarf-docker-registry -o wide 2>/dev/null || echo "no zarf-docker-registry PVC yet (clean / pre-init)"
kc -n zarf describe pvc zarf-docker-registry 2>/dev/null | sed -n '/Events:/,$p'
```

**Decision tree for `zarf-docker-registry`:**

| `phase` / `storageClassName` | Diagnosis | Pre-converge action |
|---|---|---|
| **absent** | clean / pre-init | none — init creates it on `""` |
| `Bound`, `sc=""` | already correct (static PV bound) | none |
| **`Pending`, `sc='local-path'`** (or any non-`""`) | **CAPTURED** by the cluster-default SC; provisioner dead air-gapped | **delete it** so init recreates on `""` (remediation §T1-C); images are conserved by the Retain PV |
| `Bound`, `sc='local-path'` | bound to a provisioner volume (resourced cluster) | leave if provisioner truly works; else delete + re-init `""` |

> The converge engine does the delete-and-reinit for you (`--storage-class -`). Confirming the
> capture here just tells you **why** a prior `zarf init` rolled back, and lets you pre-clear it.

---

## 6. T1 — Zarf init / registry state

```bash
kc get namespace zarf -o jsonpath='{.status.phase}{"\n"}' 2>/dev/null || echo "zarf ns ABSENT (not initialized / rolled back)"
kc -n zarf get pods -o wide 2>/dev/null
kc -n zarf get pods -l app=docker-registry \
   -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.phase}{"\n"}{end}' 2>/dev/null
kc -n zarf get svc zarf-docker-registry -o jsonpath='registry NodePort={.spec.ports[0].nodePort}{"\n"}' 2>/dev/null
kc get secret -n zarf zarf-state >/dev/null 2>&1 && echo "zarf-state present (initialized)" || echo "no zarf-state (not initialized)"
zarf tools registry catalog 2>/dev/null || echo "registry catalog unavailable (registry not up / not initialized)"
```

| Confirm | Good | Red flag |
|---|---|---|
| `zarf` ns phase | `Active` | **`Terminating`** → force-finalize (remediation §T1-A) |
| registry pod | `Running` 1/1 | `Pending` (PVC capture) / `ImagePullBackOff` (image absent) |
| `zarf-state` secret | present | absent → not initialized; run init |
| registry catalog | lists `cybersec-dask` | empty → images not pushed (T2) |

### 6a. Zarf **agent** + mutating webhook (the image-rewrite machinery)

A registry that's `Running` is NOT a completed init. The **agent-hook** pods rewrite every deployed
pod's image refs to the internal registry at admission (the helm manifests keep upstream refs like
`ghcr.io` by design — the POD is what gets rewritten). With the agent dead/absent, deploys "work"
but pods `ImagePullBackOff` against `ghcr.io`/`quay.io`. Check:

```bash
kc -n zarf get pods | grep agent-hook || echo "⚠ NO agent-hook pods — init incomplete; re-run zarf init (idempotent)"
kc get mutatingwebhookconfiguration 2>/dev/null | grep -i zarf || echo "no zarf webhook registered"
# ORPHAN check: webhook present while the zarf ns is absent/Terminating = leftover from a
# force-finalized ns (webhooks are cluster-scoped — ns deletion does NOT remove them):
kc get ns zarf >/dev/null 2>&1 || { kc get mutatingwebhookconfiguration 2>/dev/null | grep -qi zarf && echo "⚠ ORPHANED zarf webhook (ns gone) — remediation §T1-E"; }
```

### 6b. ⚠ Namespace poison — the re-init time bomb (CHECK THIS ON SITE)

**`zarf init` labels every PRE-EXISTING namespace `zarf.dev/agent=ignore`** (so it won't disturb
prior workloads). Correct on first init — but every **re-run** of init over an existing deployment
finds the app namespaces already present and **poisons them all**: the agent webhook excludes
ignore-labeled namespaces, so image rewriting is silently OFF there. Latent until any pod churn
(node reboot, eviction, redeploy) — the recreated pod keeps its upstream ref and
`ImagePullBackOff`s forever. **A node where init has been re-run (i.e. any recovered node) almost
certainly carries this poison.** Sandbox-proven 2026-07-08.

```bash
kc get ns --show-labels | grep 'zarf.dev/agent=ignore' | grep -E 'dask|panel-viz|jupyterhub' \
  && echo "⚠ POISONED app namespaces — remediation: strip the label (one command)" \
  || echo "app namespaces clean (agent active)"
# BEHAVIORAL proof — a canary server dry-run in an APP namespace must come back REWRITTEN
# (persists nothing, pulls nothing; probing `default` is a false negative — zarf ignores
# pre-init namespaces deliberately):
kc -n dask-operator run zz-canary --image=ghcr.io/zarf-canary/agent-check:v1 --restart=Never \
   --dry-run=server -o jsonpath='{.spec.containers[0].image}'; echo
# → internal-registry ref (127.0.0.1:31999/...-zarf-…) = agent active ✓ ;  unchanged ghcr.io = bypassed ✗
```

### 6c. Stale S3 env on Dask pods (operator doesn't propagate CR changes)

The dask operator does NOT roll its child Deployments when the DaskCluster CR's env changes — after
any redeploy that fixes S3 creds, scheduler/worker pods can keep the OLD (possibly empty) env
forever. Verify pod env matches the CR (lengths only, no values):

```bash
kc -n dask get daskcluster cybersec-dask -o jsonpath='{.spec.scheduler.spec.containers[0].env[?(@.name=="AWS_ACCESS_KEY_ID")].value}' | wc -c | sed 's/^/CR cred len (incl newline): /'
kc -n dask exec deploy/cybersec-dask-scheduler -- sh -c 'echo pod cred len: ${#AWS_ACCESS_KEY_ID}' 2>/dev/null
# mismatch (CR >1, pod =0) → remediation: delete the dask deployments; the operator recreates from the CR
```

### 6b. Helm release states (a killed deploy leaves WEDGED releases)

Zarf deploys via embedded Helm. A converge/zarf killed mid-deploy leaves the release in
`pending-install`/`pending-upgrade` — after which **every retry fails** with "another operation
(install/upgrade/rollback) is in progress". Audit release states via their secrets (no helm CLI
needed):

```bash
kc get secrets -A -l owner=helm -o custom-columns='NS:.metadata.namespace,RELEASE:.metadata.labels.name,VER:.metadata.labels.version,STATUS:.metadata.labels.status'
# wedged only:
kc get secrets -A -l 'owner=helm,status in (pending-install,pending-upgrade,pending-rollback)' --no-headers 2>/dev/null | grep . \
  && echo "⚠ WEDGED release(s) above — remediation §helm-pending" || echo "no wedged releases"
```

---

## 7. T2 — App images pushed to the internal registry

```bash
zarf tools registry catalog 2>/dev/null | grep -E 'cybersec-dask|dask|jupyter' || echo "no app images in registry"
# Proxy: any pod stuck pulling its image ⇒ that image isn't in the registry (CLOSURE).
kc get pods -A | grep -E 'ImagePullBackOff|ErrImagePull|ErrImageNeverPull' \
  || echo "no image-pull failures observed (images present)"
```

---

## 8. T3–T6 — Components, capacity, ingress

```bash
# T3 operator + CRD
kc get crd daskclusters.kubernetes.dask.org >/dev/null 2>&1 && echo "Dask CRD present" || echo "Dask CRD ABSENT"
kc -n dask-operator get pods -l app.kubernetes.io/name=dask-kubernetes-operator
# T4 scheduler + workers (Pending workers = oversubscription)
kc -n dask get daskcluster cybersec-dask -o jsonpath='DaskCluster: workers spec={.spec.worker.replicas} status={.status.phase}{"\n"}' 2>/dev/null
kc -n dask get pods -l dask.org/component=scheduler
kc -n dask get pods -l dask.org/component=worker -o wide
kc -n dask get pods -l dask.org/component=worker --field-selector=status.phase=Pending 2>/dev/null | grep -c . | sed 's/^/Pending workers: /'
kc -n dask get deploy -l dask.org/component=worker 2>/dev/null   # orphaned excess worker Deployments?
# T5 app
kc -n panel-viz get pods -l app=otel-navigator -o wide          # want 2/2 Running, not Pending
kc -n panel-viz get pods -l app=navigator-engine
kc -n jupyterhub get pods -l component=hub
kc -n jupyterhub get configmap sample-notebooks >/dev/null 2>&1 && echo "sample-notebooks present" || echo "sample-notebooks ABSENT"
# T6 ingress
kc get ingress -A
```

Why is a pod `Pending`? (oversubscription / capacity is the usual otel-navigator stranding):

```bash
kc get pods -A --field-selector=status.phase=Pending -o wide
kc -n panel-viz describe pod -l app=otel-navigator 2>/dev/null | sed -n '/Events:/,$p' | tail -8
```

| Confirm | Good | Red flag → remediation |
|---|---|---|
| Dask CRD + operator | present, 1/1 | absent → deploy `dask-operator` (T3) |
| scheduler | 1/1 Ready | not ready → deploy `dask-cluster` (T4) |
| workers Pending | 0 Pending | >0 Pending or excess Deployments → cap to capacity (T4.workers-capacity) |
| otel-navigator | 2/2 Running | `Pending` (no node fits its memory request) → capacity fix (T5) |
| ingress | `dask-dashboard` + `panel-viz` | missing → deploy `ingress` (T6) |

---

## 9. S3 / data path — confirm WITHOUT putting secrets on argv

The app reads parquet from S3 discovered via `s3://$S3_BUCKET/_active_dataset.json`. A blank
`S3_BUCKET` renders `OTEL_DATA_PATH=s3:///` and bricks the app at runtime ("Invalid bucket name
's3:'"). Confirm what was **rendered into the cluster** (non-secret) and that the secret exists —
never echo secret values:

```bash
kc -n panel-viz get configmap otel-navigator-config -o jsonpath='S3_BUCKET={.data.S3_BUCKET}{"\n"}OTEL_DATA_PATH={.data.OTEL_DATA_PATH}{"\n"}AWS_REGION={.data.AWS_REGION}{"\n"}' 2>/dev/null
kc -n panel-viz get secret otel-navigator-credentials -o jsonpath='cred keys: {range .data.*}{"·"}{end}{"\n"}' 2>/dev/null   # presence only, not values
kc -n dask get pods -l dask.org/component=scheduler -o jsonpath='{.items[0].spec.containers[0].env[?(@.name=="S3_ENDPOINT")].value}{"\n"}' 2>/dev/null
```

Reachability **and auth** of the S3 endpoint from inside the cluster — exec the **already-running
Dask scheduler** (its image has boto3, its env already carries the deployed creds; nothing is
exposed, nothing is pulled — closure-safe). This catches the failure mode `verify` cannot see:
pods all `Ready` but the data path dead (wrong endpoint/creds/bucket).

```bash
kc -n panel-viz get configmap otel-navigator-config -o jsonpath='configured data path: {.data.OTEL_DATA_PATH}{"\n"}' 2>/dev/null
kc -n dask exec deploy/cybersec-dask-scheduler -- python -c "
import os, boto3
from botocore.client import Config
s3 = boto3.client('s3', endpoint_url=os.environ.get('S3_ENDPOINT') or None,
                  region_name=os.environ.get('AWS_REGION') or 'us-east-1',
                  config=Config(signature_version='s3v4', connect_timeout=5, retries={'max_attempts':1}))
b = '<the-given-bucket>'   # set to the deployed S3_BUCKET
s3.head_bucket(Bucket=b); n = s3.list_objects_v2(Bucket=b, MaxKeys=1)
print('S3 AUTH OK — bucket reachable:', b, '| has objects:', n['KeyCount'] >= 1)
"
# ConnectTimeoutError => endpoint unreachable from pods; 403 => wrong creds; 404 => wrong bucket.
```

| Confirm | Good | Red flag |
|---|---|---|
| `S3_BUCKET` in configMap | the given bucket name | empty → app would brick; supply at converge (`--creds-file`) |
| `OTEL_DATA_PATH` | `s3://<bucket>/` | `s3:///` → blank bucket was rendered |
| credentials secret | present (keys only) | absent → recreate via converge |
| endpoint reachable from pod | 200 | unreachable → network/endpoint wrong |

---

## 10. Ingress / external routing

```bash
kc get ingress -A -o wide
kc get svc -A | grep -Ei 'otel-navigator|navigator-engine|dask-scheduler|panel-viz'
kc get endpoints -n panel-viz navigator-engine -o wide 2>/dev/null      # empty subsets ⇒ engine has no ready backend
```

> If a Cloudflare tunnel fronts this cluster, app origins must be **control-plane NodePorts**, not
> service-DNS (the bastion can't resolve cluster DNS → 502). Confirm the NodePorts above resolve.

---

## 11. Conservation inventory (what must NOT be deleted)

Record these so no cleanup step touches Layer A:

```bash
echo "== Layer-A node images (CONSERVE — never rmi/prune) =="; cri images | grep -Ei 'cybersec-dask|registry|zarf|agent|injector|local-path|busybox'
echo "== Registry hostPath data (CONSERVE — Retain PV) ==";    du -sh /var/lib/zarf-registry 2>/dev/null
echo "== Transported packages (Layer A) ==";                    ls -lh /var/tmp/zarf-package-*.tar.zst /var/tmp/zarf-init-*.tar.zst 2>/dev/null
```

---

## 12. One-shot snapshot (keep with the run record)

```bash
OUT="/var/tmp/airgap-discovery-$(date +%Y%m%dT%H%M%S).txt"
{
  echo "### nodes";        kc get nodes -o wide
  echo "### storageclass"; kc get storageclass -o wide
  echo "### pv";           kc get pv -o wide
  echo "### pvc -A";       kc get pvc -A
  echo "### zarf ns";      kc get ns zarf -o jsonpath='{.status.phase}'; echo
  echo "### zarf pods";    kc -n zarf get pods
  echo "### all pods";     kc get pods -A -o wide
  echo "### ingress";      kc get ingress -A
  echo "### node images";  cri images
} > "$OUT" 2>&1
echo "snapshot → $OUT"
```

---

## Decision: ready to converge?

You are clear to run `converge-node.sh apply` once you've confirmed: node `Ready`+schedulable, the
zarf-init package is staged in `/var/tmp`, you understand the **SC/PVC capture state** (§3+§5), and
you have the **S3 bucket + creds** ready to pass via a creds-file (§9). The engine handles every
red flag above automatically; jump to [AIRGAP-REMEDIATION-COMMANDS.md](AIRGAP-REMEDIATION-COMMANDS.md)
only for surgical, by-hand control or when `zarf`/the package isn't on the host.
