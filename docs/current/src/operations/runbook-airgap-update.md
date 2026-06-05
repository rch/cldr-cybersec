# Runbook (MOP): Update an Existing Air-Gap Deployment to a New Zarf Package

**Purpose.** Update a **running** air-gapped RKE2 deployment of the `cybersec-dask` stack
(Dask + JupyterHub + Panel/HoloViews **OTEL Navigator**) to a new Zarf package — e.g. to
pick up the HoloViews/viz fix in `panel-viz` / `otel-navigator.py`. Air-gap means **no
internet on the target**; everything ships inside the Zarf package.

> ⚠️ **This is an UPDATE of an EXISTING deployment, not a fresh install.** The target K8s
> already runs the stack. Read **§3 (existing-deployment hazards)** before executing — in
> particular the *same-image-tag stale-image trap*, which will silently prevent the new
> HoloViews/viz code from loading even though every command "succeeds".

---

## 0. Assumptions

- Operator has `kubectl` to the target RKE2 (usually via a bastion/jump host) and the Zarf
  CLI on a control-plane node; the new package has been carried in.
- The stack was originally deployed with Zarf (`zarf init` done; internal registry present),
  or against an external registry via `REGISTRY_URL`.
- External S3-compatible storage (MinIO / Ceph / RustFS / on-prem appliance / AWS) holds the
  OTEL data and is reachable from worker pods. **This update does not touch S3 data.**

## 1. Requirements

**Build side (internet-connected, once per package):**
- `zarf` **v0.70.1** — pin to match the package; version skew corrupts the archive format.
- `podman` or `docker` — image build is `--platform linux/amd64` (force Rosetta on Apple Silicon).
- Repo checked out at the release commit/tag.

**Target side (air-gap):**
- RKE2 cluster, **all nodes `Ready`**; `zarf` + `kubectl` on a control-plane node.
- A **default StorageClass MUST exist before `zarf init`** (RKE2 ships `local-path`; bare
  clusters deploy the `local-path-provisioner` component first) — otherwise the Zarf
  registry PVC hangs forever.
- Registry-node disk headroom ≈ package size × 2 + image layers (~10 GB safe).
- External S3 endpoint + credentials (or instance role) reachable from worker pods.

## 2. Obtain the package

The full air-gap kit **is published** as GitHub release **`zarf-v1.4.0`** on `rch/cldr-cybersec`
(published 2026-06-01), and may already be staged in the air-gap env. The release carries 6
assets — everything needed offline:
`zarf-package-cybersec-dask-amd64-1.4.0.tar.zst` (package), `zarf_v0.70.1_Linux_amd64` (pinned
binary), `zarf-init-amd64-v0.70.1.tar.zst` (init package), `local-path-provisioner.yaml`
(default-StorageClass prereq, §1), `zarf-clean-slate.sh` (reset/teardown helper), and
`BOOTSTRAP_VERSIONS.txt`.

```bash
gh release download zarf-v1.4.0 -R rch/cldr-cybersec   # verify checksums, carry across the boundary
```
If the target already has `zarf-v1.4.0` staged, the carry-in is done — **skip to §4.2.**

> 🚨 **The published `zarf-v1.4.0` PRE-DATES the HoloViews/viz fix.** It was published
> 2026-06-01; the `otel-navigator.py` viz/terminal fix is from 2026-06-04 and is **not yet
> committed or released**. So **re-deploying `zarf-v1.4.0` (or `zarf package remove` + re-deploy
> of it) will NOT fix viz** — same version, same old code (§3.1 same-version trap). **To ship the
> fix you must cut a NEW release `zarf-v1.4.1`.** Until then, no amount of re-deploying 1.4.0
> changes the running viz.

Build the new (fix) package on the internet side:
```bash
git checkout <fix-commit>        # MUST include the otel-navigator.py viz fix
# first bump the cybersec-dask image tag + zarf.yaml version to 1.4.1 (see §3.1)
devenv tasks run zarf:image      # cybersec-dask:<new-tag> (HoloViews/Datashader/Panel/Bokeh + fix)
devenv tasks run zarf:package    # -> zarf-package-cybersec-dask-amd64-1.4.1.tar.zst
sha256sum zarf/zarf-package-cybersec-dask-amd64-1.4.1.tar.zst
```

## 3. ⚠️ Existing-deployment hazards (READ FIRST)

### 3.1 The same-image-tag stale-image trap — THE failure mode for viz updates
The `cybersec-dask` image tag (`2025.2.0`) is **unchanged** across this package, and pods run
`imagePullPolicy: IfNotPresent`. On a **fresh** cluster this is fine (no cached image). On an
**existing** cluster, every node that already cached `cybersec-dask:2025.2.0` **will NOT pull
the rebuilt image** — so `zarf package deploy` re-pushes the new image to the registry, the
pods restart, and they come back running the **OLD** HoloViews/viz code. Everything reports
success; the fix does not load.

**Pick ONE before deploying:**
- **(A) Bump the image tag (recommended for a real release).** e.g. `2025.2.0 → 2025.2.1` in
  `zarf/zarf.yaml`, `zarf/manifests/panel-viz.yaml`, `zarf/manifests/dask-cluster.yaml`, and
  `zarf:image`/`zarf:package`. A new tag makes `IfNotPresent` pull fresh. Clean and auditable.
- **(B) Force a re-pull of the same tag** (if the tag must stay): after the deploy pushes the
  image, on **every** node remove the cached image, then roll the deployments — see §6.3.

### 3.2 What an update replaces vs preserves
- **Replaced (rolling):** Deployments/pods (`otel-navigator`, `navigator-engine`, dask
  scheduler/workers, hub), re-applied CRs, re-pushed registry images.
- **Preserved:** the RKE2 cluster, JupyterHub PVCs, **external S3 data**, the Zarf registry +
  its already-pushed blobs (so re-deploys *resume* image pushes).

### 3.3 Capacity gate
`DASK_WORKER_REPLICAS` over-subscription leaves worker pods `Pending` (e.g. 16 replicas on
8 × 2-vCPU nodes → ~11 fit). Harmless for viz, **but** if you raise replicas, confirm the
`dask-cluster` scheduler-ready gate (§4) isn't blocked. Size replicas ≤ schedulable capacity.

## 4. Procedure

> All cluster-side commands run on a control-plane node (has the admin kubeconfig). Replace
> `$PKG` with the package filename.

**Step 4.0 — Pre-flight snapshot (capture rollback baseline).**
```bash
zarf tools kubectl get pods -A -o wide > preflight-pods.txt
zarf tools kubectl get deploy -A > preflight-deploy.txt
zarf package list                                   # current deployed package version(s)
zarf tools kubectl -n panel-viz get pod -l app=otel-navigator \
  -o jsonpath='{.items[0].spec.containers[0].image}'  # record running image ref
```

**Step 4.1 — Transfer + verify the package into the air-gap.**
Carry `$PKG` (and its `.sha256`) across the boundary; on the target:
```bash
sha256sum -c $PKG.sha256        # MUST match the build-side checksum
```

**Step 4.2 — Confirm prerequisites on target.**
```bash
zarf tools kubectl get nodes                      # all Ready
zarf tools kubectl get sc                         # a (default) StorageClass exists
zarf tools kubectl get deploy -n zarf zarf-docker-registry  # registry present = already initialized
```
If the cluster is *not* yet Zarf-initialized, run `zarf init --confirm` first (needs the
default StorageClass from §1).

**Step 4.3 — Deploy/update the package.** Idempotent; re-applies all components in order.
```bash
zarf package deploy $PKG --confirm --retries 10 \
  --set S3_ENDPOINT="https://s3.onprem.example:9000" \   # external on-prem endpoint (empty = AWS S3)
  --set S3_BUCKET="<bucket>" \
  --set S3_REGION="<region>" \
  --set S3_ACCESS_KEY="<key>" \
  --set S3_SECRET_KEY="<secret>" \
  --set DASK_WORKER_REPLICAS="<= schedulable capacity>" \
  --set INGRESS_DOMAIN="<domain>" --set INGRESS_CLASS="traefik"
```
- `--retries 10`: the `cybersec-images` push is the long pole; raise retries (see §6.1).
- For a true external on-prem object store, also set TLS trust / path-style addressing per §7.
- Component order & readiness gates Zarf enforces:
  `local-path-provisioner → cybersec-images → dask-operator → dask-cluster (wait: scheduler
  Ready) → jupyterhub (wait: hub Ready) → panel-viz (wait: otel-navigator Ready) →
  navigator-engine (wait: ready) → sample-notebooks → ingress`.

**Step 4.4 — If you chose §3.1(B) same-tag:** run the force-repull (§6.3) now.

## 5. Verification

```bash
# 5.1 Components healthy
zarf tools kubectl get pods -n panel-viz -o wide          # otel-navigator 2/2 (app + pty-proxy), navigator-engine 1/1
zarf tools kubectl get pods -n dask                       # scheduler + workers Running
# 5.2 The fix actually loaded — confirm the running image digest changed vs Step 4.0
zarf tools kubectl -n panel-viz get pod -l app=otel-navigator \
  -o jsonpath='{.items[0].status.containerStatuses[0].imageID}'
# 5.3 HoloViews/viz renders: hit the app and confirm panes load (no blank/parse errors)
zarf tools kubectl -n panel-viz logs deploy/otel-navigator -c otel-navigator | grep -iE "error|holoviews|datashader|bokeh|panel"
curl -fsS http://<node-ip>:30506/ | head     # or via ingress hostname
# 5.4 Embedded terminal: from the browser at the viz hostname, devtools Network tab should
#     show the /ws request upgrade to 101 Switching Protocols (wss through the ingress).
```
Pass criteria: `otel-navigator` `2/2 Ready`; image digest **differs** from Step 4.0; viz panes
render; terminal WebSocket upgrades.

## 6. Troubleshooting

**6.1 Image push times out (`error creating error stream … -> 5000: Timeout`).**
The 2 GB image push goes via a port-forward to the Zarf registry pod; if that pod is on a
small worker (cross-node hop), the tunnel stalls. Mitigations: re-run the deploy (it **resumes**
already-pushed blobs); raise `--retries`; schedule the registry on a larger/control-plane node
(note: `local-path` PVC is node-bound — moving it means recreating the PVC, losing resume).
A smaller image (slim the Dockerfile) reduces push time.

**6.2 Registry PVC `Pending` / `zarf init` hangs.** No default StorageClass — deploy
`local-path-provisioner` (or ensure RKE2's `local-path`) **before** init.

**6.3 New code didn't load (same tag).** Force re-pull on every node:
```bash
for n in $(zarf tools kubectl get nodes -o name); do echo "$n"; done   # crictl rmi on each node:
#  ssh <node>: sudo /var/lib/rancher/rke2/bin/crictl rmi <registry>/cybersec-dask:2025.2.0
zarf tools kubectl -n panel-viz rollout restart deploy/otel-navigator deploy/navigator-engine
zarf tools kubectl -n dask delete pod -l dask.org/component=worker   # workers re-pull
```
> ⚠️ Remove **only** the one registry-backed image by exact ref (it re-pulls from the in-cluster
> registry). **NEVER** `crictl rmi --prune` / blanket-delete — see §6.7. Better still: bump the tag
> (§3.1A) and avoid this entirely.

**6.4 Workers stuck `Pending`.** `DASK_WORKER_REPLICAS` > schedulable capacity. Lower it and
re-deploy, or add nodes.

**6.5 Viz loads but no data.** Wrong/unreachable `S3_ENDPOINT`/bucket/creds, or no OTEL data in
the bucket. Verify from a worker pod: `zarf tools kubectl -n dask exec <worker> -- python -c
"import s3fs; print(s3fs.S3FileSystem(client_kwargs={'endpoint_url':'<ep>'}).ls('<bucket>'))"`.

**6.6 "Task shows no output."** If driving via `devenv tasks run`, note it **buffers** task
stdout — quiet ≠ stuck. Check the actual `zarf`/cluster state, not the task log.

**6.7 🚫 NEVER `crictl rmi --prune` (or blanket-delete images) on an air-gap node — Conservation.**
The air-gap env is a closed world: images were *transported once* and **cannot be re-pulled**.
Pruning deletes them; recovering means re-inspecting and re-transporting artifacts across the
boundary — the single most expensive failure we hit (it removed `rancher/local-path-provisioner`,
a *bootstrap* image not in the registry, stranding `zarf init`). Rules:
- The **bootstrap images** (`rancher/local-path-provisioner:v0.0.30`, `busybox:1.37`) live in the
  node's containerd *before* the registry exists and have **no registry to re-pull from** — treat
  them as irreplaceable. They are pre-loaded at node setup and declared in `zarf/artifacts.manifest.json`.
- To reclaim disk safely, only remove **rebuildable, registry-backed** content: the package tarball
  from `/var/tmp` (re-stage for deploy), `journalctl --vacuum-size=…`, and `Failed`/`Evicted` pod
  tombstones. Never touch the image store wholesale.
- If `crictl images` shows a bootstrap image missing, that's a closure failure → re-import the exact
  image (it's in the package's OCI layout or RKE2's bundled-images dir); do **not** try to pull it.

## 7. Air-gap external on-prem S3 notes

`S3_ENDPOINT`/`S3_BUCKET`/`S3_REGION`/`S3_ACCESS_KEY`/`S3_SECRET_KEY`/`S3_SESSION_TOKEN` are all
declared Zarf vars and template into `dask-cluster`, `jupyterhub`, and `panel-viz`. For a real
external object store (not bundled MinIO):
- **TLS:** if the endpoint uses a private CA, the CA bundle must be trusted inside the pods
  (mount/append to the image trust store) or use `verify=false` only for testing.
- **Addressing:** some appliances need **path-style** (not virtual-hosted) S3 addressing.
- **Pre-create the bucket**; the `dask-cluster` onDeploy `cmd` only creates it when S3 is configured.
- The repo docs currently frame `S3_ENDPOINT` as "MinIO"; it is provider-agnostic — any
  S3-compatible endpoint works (this runbook supersedes that framing).

## 8. Rollback

The package is versioned; to revert the viz change:
1. Re-deploy the **previous** package version (same §4 procedure) — components roll back.
2. If you bumped the tag, the previous tag still resolves; if same-tag, re-push the previous
   image and force re-pull (§6.3).
3. External S3 data is untouched by deploy/rollback, so no data restore is needed.
4. Full removal (rare): `zarf package remove cybersec-dask --confirm` (destroys the app
   namespaces; does **not** touch external S3).

---

*Maintainers: keep image-tag, package version, and pinned `zarf` version in sync across
`zarf.yaml`, the manifests, and the build tasks. Supersedes the stale image/zarf versions in
`airgap-deployment.md`.*
