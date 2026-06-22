# Air-Gap Convergent Deploy — Operator Runbook (v1.6.1)

**Audience.** You have a single-node, air-gapped Kubernetes (RKE2) and need to stand up the
cybersec-dask / OTEL Navigator stack with **no internet**. The node may already be carrying a
**partial or stuck Zarf procedure** (a half-finished `zarf init`, a registry up but the
zarf-agent missing, app pods in `ImagePullBackOff` against `ghcr.io`/`quay.io`, a namespace
wedged in `Terminating`) **or an interfering cluster storage config** (a default StorageClass
whose provisioner can't run air-gapped, capturing the registry's PVC). The linear
`zarf init && zarf package deploy` path does not recover from these states.

**This runbook drives the deployment to target state from *any* partial-failure state — one
command, on the node, no internet — using the convergence engine.**

---

## What it is

A deterministic finite-state machine — **detect → remediate → re-detect → fixpoint** — over a
tiered catalog of invariants (node → storage → registry → images → operator → scheduler →
workloads → ingress). It is **idempotent** (`verify` is a clean no-op once at target) and
**never destroys a transported (Layer-A) artifact** — that guard is structural in the engine.
It **picks up wherever your procedure stalled**: already-good tiers detect `[ok]` and are
skipped; only the broken ones are remediated.

**New in v1.6.1 — the registry/storage tier (T1) deterministically defeats the StorageClass
capture that wedged `zarf init`,** plus the states that previously needed manual
`kubectl`/`zarf destroy`: an interfering default StorageClass, a dead provisioner, a stranded
or mis-classed registry PV, a **vestigial captured registry PVC**, a wedged namespace. And when
it *can't* self-heal, it prints a **precise diagnosis** of which storage condition is unmet —
not a bare `rc=1`. (See "Why `zarf init` was capturing the registry PVC" below for the root
cause and the one-flag fix.)

## The two layers

- **Layer A** — artifacts you transport IN: the deploy package, the **zarf-init package**, the
  zarf binary, the engine bundle. Never re-fetched; the engine verifies, never pulls.
- **Layer B** — the deployment (K8s resources, registry content). Disposable; rebuilt from Layer A.

## Resilient by default (no StorageClass required)

The Zarf registry binds a **claimRef hostPath PV**, and the engine pre-makes that path writable
(the registry runs non-root). So a single, disk-limited node with a **bare RKE2** — no default
StorageClass, no provisioner, no bootstrap images — is the baseline.

**If the node *does* ship a default StorageClass** (e.g. RKE2's built-in `local-path`) whose
provisioner isn't running in the closed world, it would otherwise hijack the registry's PVC and
hang it `Pending` forever. The engine **defeats that deterministically** (see the root-cause
section below) — it inits the registry with an explicit empty storageClass that binds the static
hostPath PV directly, regardless of any default. Resourced multi-node clusters that genuinely want
dynamic PVCs can opt back in with `CONVERGE_DYNAMIC_PROVISIONING=1`.

---

## Why `zarf init` was capturing the registry PVC (root cause + the fix)

The single most persistent air-gap failure — `zarf init` exits `rc=1`, `kubectl -n zarf get pvc`
shows `zarf-docker-registry  Pending  storageClass=local-path`, and the `zarf` namespace rolls
back — is a **Helm storageClass gotcha**, now fixed deterministically:

- The init package's `docker-registry` chart **omits** `storageClassName` from the PVC when its
  value is empty (`{{- if .Values.persistence.storageClass }}`). Zarf's default for that value is
  empty, so the field is omitted, and **Kubernetes admission stamps the cluster-default
  StorageClass** — RKE2 `local-path`, which is `WaitForFirstConsumer` with a provisioner that
  isn't running air-gapped. The PVC waits on that provisioner forever; the registry pod can't bind
  its volume; `zarf init` rolls back.
- A PVC's `storageClassName` is **immutable**, and `zarf init` **reuses an existing PVC by name** —
  so once a captured `local-path` PVC exists, re-running init (or merely un-defaulting the SC)
  cannot fix it. This is the "vestigial artifact" that survived every retry.

**The fix (engine does this for you):** init the registry with `zarf init --storage-class -`. The
`-` is the chart's sentinel for *explicit empty* (`storageClassName: ""`), which **disables dynamic
provisioning and binds our static claimRef hostPath PV directly** — no provisioner, no default-SC
race. The engine also **deletes any vestigial captured PVC** first so init recreates it clean.

If you are ever recovering this **by hand**, the equivalent is:

```bash
kubectl -n zarf delete pvc zarf-docker-registry --ignore-not-found    # drop the captured PVC
cd /var/tmp && zarf init --confirm --storage-class -                  # re-init on ""
```

---

## 1. Prerequisites (on the node)

- An existing single-node RKE2 — `kubectl get nodes` shows it `Ready`.
- `python3` (standard library only — the engine needs no extra packages).
- root (RKE2's kubeconfig is root-only).
- The release assets (next section) transported into the closed world.

## 2. Transport the release assets into the closed world

Verify integrity first (`sha256sum -c SHA256SUMS`), then place each asset:

| Asset | Destination |
|-------|-------------|
| `zarf-package-cybersec-dask-amd64-1.6.1.tar.zst` | `/var/tmp/` |
| `zarf-init-amd64-v0.70.1.tar.zst` *(Layer A — the piece partial procedures most often lack)* | `/var/tmp/` (beside the deploy package) |
| `zarf` *(the v0.70.1 binary)* | `/usr/local/bin/zarf` (`chmod +x`) |
| `cybersec-converge-1.6.1.tar.gz` *(the convergence engine)* | unpack to a working dir of your choice |

```bash
sha256sum -c SHA256SUMS
install -m0755 zarf /usr/local/bin/zarf
mv zarf-package-cybersec-dask-amd64-1.6.1.tar.zst zarf-init-amd64-v0.70.1.tar.zst /var/tmp/

mkdir -p ~/cybersec-converge && tar xzf cybersec-converge-1.6.1.tar.gz -C ~/cybersec-converge
```

> The engine bundle is **self-locating** — `converge-node.sh` resolves the engine, the manifests,
> the deploy package (`/var/tmp` or its 2nd arg) and the zarf-init package (beside the deploy
> package) relative to itself. Unpack it **anywhere** writable; `~/cybersec-converge` is just a
> convention.

## 3. Converge — one command

S3 is **provided** in your environment (an endpoint + a given bucket). Keep the secret off the
process table by passing a creds-file (the engine reads it and forwards values to Zarf as
variables — never on `argv`):

```bash
umask 077; cat > /dev/shm/s3-creds <<'EOF'
S3_ENDPOINT=<provided-endpoint>
S3_BUCKET=<given-bucket-name>
S3_REGION=<region>
S3_ACCESS_KEY=<key>
S3_SECRET_KEY=<secret>
EOF

sudo env CONVERGE_CREDS_FILE=/dev/shm/s3-creds bash ~/cybersec-converge/converge-node.sh apply
shred -u /dev/shm/s3-creds
```

The engine discovers both packages in `/var/tmp`, preps the registry hostPath, **clears any
storage/registry residue blocking `zarf init`** (see the table below), runs `zarf init`
(registry + agent + webhook), pushes the app images from the package, deploys the components, and
stops at a fixpoint. Watch the tiers climb:

```
registry hostPath … prepared  →  [un-defaulted StorageClass 'local-path']  →
zarf init: rc=0  →  T2.images-pushed  →  T3 dask-operator  →  T4 scheduler  →
T5 otel-navigator / navigator-engine / jupyterhub  →  T6 ingress  →  ✔ CONVERGED
```

> **`S3_BUCKET` is required** for the panel-viz / navigator-engine components — the engine
> **refuses** to deploy them with an empty bucket (it would render `OTEL_DATA_PATH=s3:///` and
> brick the app) and tells you so, rather than failing silently. Make sure it's in the creds-file.

## 4. Verify

```bash
sudo bash ~/cybersec-converge/converge-node.sh verify     # every invariant [ok] = at target
```

---

## What it resolves (partial-procedure + storage realities)

| Symptom | The engine's action |
|---------|---------------------|
| `zarf init`: "requires a zarf-init package" | runs from the directory holding the transported init package so init finds it |
| image push `500 … permission denied` | preps the registry hostPath writable (the registry runs as a non-root UID; `fsGroup` does not chown hostPath) |
| pods `ImagePullBackOff` on `ghcr.io`/`quay.io` | a fully-completed `zarf init` deploys the **agent + webhook**, which rewrite images to the internal registry |
| **registry PVC `Pending storageClass=local-path`** (the cluster-default SC captured it; provisioner dead air-gapped) | inits with **`--storage-class -`** → registry PVC `storageClassName:""` → binds the static hostPath PV directly, no provisioner, no default-SC race; also **un-defaults** the SC as hygiene |
| **vestigial `zarf-docker-registry` PVC** on a non-`""` class that init keeps reusing (immutable class) | **deletes the captured PVC** (clearing the `pvc-protection` finalizer if it lingers) so init recreates it clean on `""` — Layer-B; the Retain PV conserves the images |
| **registry PV stranded `Released`/`Failed`, or its `storageClassName` drifted off `""`** | **resets the PV object to `""`** (binds with no provisioner) — the hostPath images are **conserved** (Retain), so the registry comes back on the same storage with no re-push |
| **`zarf` namespace wedged `Terminating`** | **force-finalizes** it, then re-initializes the registry |
| workers oversubscribed (`Pending`) | reaps excess worker Deployments to fit the node's schedulable capacity |
| re-run of an already-good cluster | fast no-op (every tier detects `[ok]`) |

## Precise diagnosis when it *can't* self-heal

A failed `zarf init` no longer reports a bare `rc=1`. The engine inspects the live state and tells
you exactly what's unmet, e.g.:

```
T1  [FAIL]  T1.registry-running
      zarf init rc=1: registry PVC Pending storageClass='local-path';
      registry PVC is on 'local-path', not '' — the cluster-default SC captured it
      (immutable); delete it so init recreates it on '' (init must pass --storage-class -);
      static PV Available storageClass='' (≠ PVC's 'local-path' → won't bind);
      default StorageClass ['local-path'] present — its PVCs wait on a provisioner
      that may be absent in the air-gap; the resilient path wants none
```

(Once on v1.6.1 the engine resolves this case itself — the diagnosis above is what you'd
see only if a deeper problem keeps the captured PVC from being deleted.)

Component deploys (dask-operator / jupyterhub) likewise surface the underlying zarf/Helm error
tail. (S3 secrets ride the environment, never `stdout`, so these diagnoses stay clean.)

## Recovery — the engine self-heals; reserve `zarf destroy` for true corruption

Earlier guidance was "do **not** iterate a wedged `zarf init` — run `zarf destroy`." With v1.6.1 the
engine **unwinds** the storage/registry wedge states itself (Terminating ns, stranded/mis-classed
PV, default-SC capture, **a vestigial captured registry PVC**), so a plain re-run of
`converge-node.sh apply` is the right move — it makes
forward progress each pass to a fixpoint. **Only** if the engine's diagnosis points at damage below
Layer B that it structurally won't touch (etcd/kubelet/CNI failure, a corrupt control plane) should
you `zarf destroy --confirm` (or reprovision the node) for a clean base, then re-run converge.

## Diagnose / other modes

- `converge-node.sh dry-run` — show what *would* be remediated; changes nothing.
- `converge-node.sh verify` — read-only target oracle (exit 0 = at target).
- `converge-node.sh teardown` — clean-slate the app stack (registry + node images **CONSERVED**)
  for a fast redeploy.
- `CONVERGE_DYNAMIC_PROVISIONING=1` — opt in to the default-StorageClass path (a resourced
  multi-node cluster with a working provisioner). The engine then leaves default SCs alone.

## Exit codes

`0` = converged / clean verify. `1` = not converged (see the diagnosis). `2` = a **CLOSURE**
violation — a transported Layer-A artifact is missing; re-transport it (the engine will not pull).

---

## Provenance / validation

The T1 registry/storage FSM is regression-tested end-to-end against a throwaway air-gapped RKE2
node — `just sandbox-test-fsm` provisions a node, **induces** each wedged state, converges, and
asserts the registry recovers, then tears the node down. The v1.6.1 matrix runs **5/5 green** and
its inducers are faithful to the field: the default StorageClass is `WaitForFirstConsumer` (as
RKE2 `local-path` is) and one case plants a **vestigial `zarf-docker-registry` PVC already on the
default class** — the exact live-node wedge. Each passing case additionally asserts the bound
registry PVC lands on `storageClassName=""` (proving the static-PV bind, not merely that the tier
went green). Every permutation in the table above is exercised on real Kubernetes PVC↔PV binding
before a release is cut.
