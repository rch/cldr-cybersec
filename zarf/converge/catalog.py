"""The invariant catalog — the single source of truth for both remediation
(``converge --apply``) and verification (``converge --verify``).

Each invariant is one target-state fact with a read-only ``detect`` and, for
Layer-B only, an idempotent ``remediate``. Ordered T0 (node) → T6 (ingress) with
explicit ``depends_on`` edges. Layer-A invariants are detect-only (CONSERVATION).

Remediations reuse the bundle's own logic where it's proven: component (re)deploys
shell to ``zarf package deploy --components=…`` (idempotent); surgical repairs
(force-finalize, claimRef PV, default-SC annotation, worker cap) are inline kubectl.
If ``zarf``/the package isn't on the host (e.g. an in-cluster Job), zarf-backed
remediations degrade to a precise manual hint instead of failing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from .kube import Ctx
from .model import Cost, Fix, Invariant, Layer, Probe

# --------------------------------------------------------------------------- #
# Layer-B remediation primitives (kubectl-only, idempotent, NEVER touch Layer A)
# --------------------------------------------------------------------------- #

REGISTRY_PV_YAML = """\
apiVersion: v1
kind: PersistentVolume
metadata:
  name: zarf-registry-pv
spec:
  capacity:
    storage: {size}
  accessModes: [ReadWriteOnce]
  persistentVolumeReclaimPolicy: Retain
  storageClassName: ""
  hostPath:
    path: /var/lib/zarf-registry
    type: DirectoryOrCreate
  claimRef:
    namespace: zarf
    name: zarf-docker-registry
"""


def _force_finalize_ns(ctx: Ctx, ns: str) -> bool:
    """Clear a namespace's spec.finalizers via the /finalize subresource — the only
    thing that releases a namespace wedged in Terminating. Layer-B only."""
    obj = ctx.get("namespace", ns)
    if not obj:
        return False
    obj["spec"] = {"finalizers": []}
    r = ctx.run(ctx.kubectl + ["replace", "--raw",
                f"/api/v1/namespaces/{ns}/finalize", "-f", "-"],
                input_=json.dumps(obj))
    return r.returncode == 0


def _zarf_deploy_components(ctx: Ctx, components: str) -> Fix:
    if not (ctx.have_zarf() and ctx.package_path):
        return Fix(False, f"MANUAL: zarf package deploy --components={components} "
                          "(zarf/package not available to this engine instance)")
    args = ["package", "deploy", ctx.package_path, "--confirm",
            f"--components={components}", "--retries", "10"]
    if not ctx.registry_pvc_enabled:
        args.append("--set=REGISTRY_PVC_ENABLED=false")
    # Pass package variables (incl. the sensitive S3 creds) via ZARF_VAR_* ENV, never
    # on argv: keeps secrets out of the process table and zarf's command logging. Zarf
    # maps ZARF_VAR_<NAME> → the <NAME> package variable.
    env = {f"ZARF_VAR_{k.upper()}": v for k, v in ctx.s3.items() if v}
    r = ctx.zarf(args, env=env)
    return Fix(r.returncode == 0,
               f"zarf deploy {components}: rc={r.returncode}")


def _ensure_default_sc(ctx: Ctx, sc: str = "local-path") -> bool:
    r = ctx.k(["patch", "sc", sc, "-p",
               '{"metadata":{"annotations":'
               '{"storageclass.kubernetes.io/is-default-class":"true"}}}'])
    return r.returncode == 0


def _apply_bundled_local_path(ctx: Ctx) -> Fix:
    """Apply the BUNDLED local-path-provisioner manifest with kubectl — registry-
    INDEPENDENT (the image is node-preloaded, pulled with IfNotPresent), so it
    bootstraps the default StorageClass with NO zarf registry and NO egress. This is
    what breaks the SC↔registry chicken-egg on a fresh cluster. Degrades to a precise
    MANUAL hint if the manifest wasn't staged next to the engine (--manifests-dir)."""
    path = Path(ctx.manifests_dir) / "local-path-provisioner.yaml" if ctx.manifests_dir else None
    if not path or not path.exists():
        return Fix(False, "MANUAL: kubectl apply local-path-provisioner.yaml "
                          "(bundled manifest not staged — pass --manifests-dir)")
    r = ctx.apply_yaml(path.read_text())
    return Fix(r.returncode == 0,
               f"applied bundled local-path-provisioner.yaml (node image): rc={r.returncode}")


# --------------------------------------------------------------------------- #
# T0 — node / closure
# --------------------------------------------------------------------------- #

def _det_api(ctx: Ctx) -> Probe:
    r = ctx.k(["get", "--raw", "/healthz"])
    return Probe(r.returncode == 0 and "ok" in r.stdout.lower(),
                 r.stdout.strip() or r.stderr.strip())


def _det_node_ready(ctx: Ctx) -> Probe:
    nodes = ctx.items("nodes")
    if not nodes:
        return Probe(False, "no nodes returned")
    bad = []
    for n in nodes:
        name = n["metadata"]["name"]
        conds = {c["type"]: c["status"] for c in n.get("status", {}).get("conditions", [])}
        if conds.get("Ready") != "True":
            bad.append(f"{name}=NotReady")
        elif n.get("spec", {}).get("unschedulable"):
            bad.append(f"{name}=cordoned")
    return Probe(not bad, ", ".join(bad) or f"{len(nodes)} node(s) Ready+schedulable")


def _rem_node_ready(ctx: Ctx) -> Fix:
    changed = False
    for n in ctx.items("nodes"):
        if n.get("spec", {}).get("unschedulable"):
            ctx.k(["uncordon", n["metadata"]["name"]])
            changed = True
    return Fix(changed, "uncordoned cordoned node(s)" if changed else "nothing to uncordon")


def _det_no_disk_pressure(ctx: Ctx) -> Probe:
    tainted = []
    for n in ctx.items("nodes"):
        for t in n.get("spec", {}).get("taints", []) or []:
            if t.get("key") == "node.kubernetes.io/disk-pressure":
                tainted.append(n["metadata"]["name"])
    return Probe(not tainted, f"disk-pressure on {tainted}" if tainted
                 else "no disk-pressure taint")


def _det_layer_a_images(ctx: Ctx) -> Probe:
    """CLOSURE: the bootstrap images must be present in the closed world. Proxy via
    pod health — an ImagePullBackOff means the image isn't there. (Never pulls.)"""
    missing = ctx.pod_image_missing("local-path-storage", "app=local-path-provisioner")
    if missing is True:
        return Probe(False, "local-path-provisioner ImagePullBackOff — bootstrap image absent")
    # If the provisioner isn't deployed yet we can't judge from pods; treat as OK at
    # T0 and let the T0.5 provisioner invariant surface a real pull failure.
    return Probe(True, "no image-pull failure observed for bootstrap images")


# --------------------------------------------------------------------------- #
# T0.5 — storage
# --------------------------------------------------------------------------- #

def _det_sc_default(ctx: Ctx) -> Probe:
    scs = ctx.items("storageclass")
    default = [s["metadata"]["name"] for s in scs
               if (s["metadata"].get("annotations", {}) or {})
               .get("storageclass.kubernetes.io/is-default-class") == "true"]
    return Probe(bool(default), f"default SC: {default}" if default
                 else f"no default StorageClass (have: {[s['metadata']['name'] for s in scs]})")


def _rem_sc_default(ctx: Ctx) -> Fix:
    if ctx.exists("storageclass", "local-path"):
        ok = _ensure_default_sc(ctx)
        return Fix(ok, "marked local-path default" if ok else "patch failed")
    # No StorageClass yet: apply the BUNDLED manifest via kubectl (registry-
    # independent — node-preloaded image), then mark it default. Registry-free, so
    # it can run BEFORE zarf init (T1) — this is the cycle-break.
    fix = _apply_bundled_local_path(ctx)
    if not fix.changed:
        return fix  # MANUAL / failed — propagate the hint
    _ensure_default_sc(ctx)
    return Fix(True, f"{fix.detail}; marked local-path default")


def _det_provisioner(ctx: Ctx) -> Probe:
    if ctx.pod_image_missing("local-path-storage", "app=local-path-provisioner") is True:
        return Probe(False, "provisioner ImagePullBackOff — bootstrap image missing (CLOSURE)")
    ready, total = ctx.pods_ready("local-path-storage", "app=local-path-provisioner")
    return Probe(ready >= 1, f"provisioner ready {ready}/{total}")


def _rem_provisioner(ctx: Ctx) -> Fix:
    # Apply the bundled manifest via kubectl (node-preloaded image), NOT a zarf
    # component deploy — the provisioner must come up before the registry exists,
    # and re-applying the same manifest the SC bootstrap used is idempotent.
    return _apply_bundled_local_path(ctx)


def _det_registry_pv(ctx: Ctx) -> Probe:
    """RESILIENT default: the Zarf registry binds storage WITHOUT a default
    StorageClass — a claimRef-prebound hostPath PV satisfies its PVC directly, so the
    whole SC↔registry chicken-egg (and the provisioner + its bootstrap images) simply
    don't exist. OK if the registry PVC is already Bound (e.g. a resourced cluster's
    pre-existing default SC handled it) OR the prebound PV is present so a fresh
    ``zarf init``'s PVC binds on creation."""
    if any(p.get("status", {}).get("phase") == "Bound"
           for p in ctx.items("pvc", ns="zarf")):
        return Probe(True, "zarf registry PVC already Bound")
    if ctx.exists("pv", "zarf-registry-pv"):
        return Probe(True, "claimRef registry PV present (PVC will bind on init)")
    return Probe(False, "no Bound registry PVC and no prebound registry PV")


def _rem_registry_pv(ctx: Ctx) -> Fix:
    # Apply the claimRef-prebound hostPath PV so the registry PVC binds with NO default
    # StorageClass. Idempotent; Layer-B (a disposable PV, not a transported artifact).
    # This is the single move that lets the resilient path skip the provisioner entirely.
    r = ctx.apply_yaml(REGISTRY_PV_YAML.format(size=ctx.registry_pv_size))
    return Fix(r.returncode == 0,
               f"applied claimRef registry PV ({ctx.registry_pv_size}): rc={r.returncode}")


# --------------------------------------------------------------------------- #
# T1 — zarf init / registry
# --------------------------------------------------------------------------- #

def _det_registry_running(ctx: Ctx) -> Probe:
    if not ctx.exists("namespace", "zarf"):
        return Probe(False, "zarf namespace absent (not initialized)")
    ready, total = ctx.pods_ready("zarf", "app=docker-registry")
    if ready < 1 and total == 0:
        ready, total = ctx.pods_ready("zarf", "app.kubernetes.io/name=zarf-docker-registry")
    return Probe(ready >= 1, f"zarf-docker-registry ready {ready}/{total}")


def _rem_registry_running(ctx: Ctx) -> Fix:
    # If a registry PVC is stuck Pending (nothing to bind to), provide a
    # claimRef-prebound PV (the zarf-init-recovery pattern) and clear the wedged
    # zarf ns, then init. If the PVC instead binds via the default StorageClass,
    # this is a harmless no-op. Layer-B throughout; never touches images.
    pvcs = ctx.items("pvc", ns="zarf")
    if any(p.get("status", {}).get("phase") == "Pending" for p in pvcs):
        # Gentle first: ensure a claimRef-prebound PV exists for the Pending PVC to bind
        # to (the resilient path; the T0.5.registry-pv invariant normally did this
        # already — this is defensive). Skip in dynamic-provisioning mode, where the
        # default StorageClass binds the PVC and a competing claimRef PV would be wrong.
        if ctx.registry_pvc_enabled and not ctx.dynamic_provisioning:
            ctx.apply_yaml(REGISTRY_PV_YAML.format(size=ctx.registry_pv_size))
        _force_finalize_ns(ctx, "zarf")
        # Escalate: a still-Pending PVC means the ns is wedged from a prior failed
        # init — delete it for a clean re-init (Phase 2d parity). `zarf init`
        # recreates everything, so even a racing delete is harmless. The delete can
        # itself hang on a finalizer, so force-finalize after it.
        if any(p.get("status", {}).get("phase") == "Pending"
               for p in ctx.items("pvc", ns="zarf")):
            ctx.k(["delete", "namespace", "zarf", "--wait=false"])
            _force_finalize_ns(ctx, "zarf")
    if not ctx.have_zarf():
        return Fix(False, "MANUAL: zarf init --confirm (zarf binary not available here)")
    args = ["init", "--confirm", f"--set=REGISTRY_PVC_SIZE={ctx.registry_pv_size}"]
    if not ctx.registry_pvc_enabled:
        args.append("--set=REGISTRY_PVC_ENABLED=false")
    r = ctx.zarf(args)
    return Fix(r.returncode == 0, f"zarf init: rc={r.returncode}")


# --------------------------------------------------------------------------- #
# T2 — images pushed to the internal registry (expensive)
# --------------------------------------------------------------------------- #

def _det_images_pushed(ctx: Ctx) -> Probe:
    # Proxy: if the operator / app pods are NOT in ImagePullBackOff and exist, the
    # images are in the registry. Definitive when those components are deployed.
    for ns, sel in (("dask-operator", "app.kubernetes.io/name=dask-kubernetes-operator"),
                    ("dask", "dask.org/component=scheduler")):
        miss = ctx.pod_image_missing(ns, sel)
        if miss is True:
            return Probe(False, f"{ns} pods in ImagePullBackOff — images not in registry")
    if ctx.have_zarf():
        r = ctx.zarf(["tools", "registry", "catalog"], timeout=60)
        if r.returncode == 0 and "cybersec-dask" in r.stdout:
            return Probe(True, "registry catalog contains cybersec-dask")
    # No evidence either way pre-deploy: defer to the component invariants.
    return Probe(True, "no image-pull failures observed")


def _rem_images_pushed(ctx: Ctx) -> Fix:
    return _zarf_deploy_components(ctx, "cybersec-images")


# --------------------------------------------------------------------------- #
# T3-T6 — components
# --------------------------------------------------------------------------- #

def _det_operator(ctx: Ctx) -> Probe:
    crd = ctx.exists("crd", "daskclusters.kubernetes.dask.org")
    ready, total = ctx.pods_ready("dask-operator", "app.kubernetes.io/name=dask-kubernetes-operator")
    return Probe(crd and ready >= 1, f"operator {ready}/{total}, CRD={'yes' if crd else 'no'}")


def _rem_operator(ctx: Ctx) -> Fix:
    return _zarf_deploy_components(ctx, "dask-operator")


def _det_scheduler(ctx: Ctx) -> Probe:
    ready, total = ctx.pods_ready("dask", "dask.org/component=scheduler")
    return Probe(ready >= 1, f"scheduler ready {ready}/{total}")


def _rem_scheduler(ctx: Ctx) -> Fix:
    return _zarf_deploy_components(ctx, "dask-cluster")


def _det_workers_capacity(ctx: Ctx) -> Probe:
    """No perpetually-Pending workers: requested replicas must fit schedulable
    capacity. This is the oversubscription failure that strands otel-navigator."""
    pods = ctx.items("pods", ns="dask", selector="dask.org/component=worker")
    pending = [p["metadata"]["name"] for p in pods
               if p.get("status", {}).get("phase") == "Pending"]
    return Probe(not pending, f"{len(pending)} worker(s) Pending (oversubscribed)"
                 if pending else f"{len(pods)} worker(s), none Pending")


def _rem_workers_capacity(ctx: Ctx) -> Fix:
    """Make the actual worker pods fit schedulable capacity so the viz pod gets a
    node. Two parts — because the operator may leave ORPHANED worker Deployments it
    never reaps (the "spec says 4 but 16 pods, 5 Pending" state we hit):
      1. set DaskCluster spec.worker.replicas = target (the source of truth);
      2. reap the EXCESS worker Deployments down to target, least-ready (Pending)
         first — don't trust the spec, count the real Deployments.
    target leaves one node's headroom for panel-viz/engine/jupyter. Layer-B; never
    touches images."""
    cap = ctx.node_capacity()
    target = max(1, cap["schedulable_nodes"] - 1)
    ctx.k(["patch", "daskcluster", "cybersec-dask", "-n", "dask", "--type", "merge",
           "-p", json.dumps({"spec": {"worker": {"replicas": target}}})])
    deps = ctx.items("deployments", ns="dask", selector="dask.org/component=worker")
    excess = len(deps) - target
    if excess <= 0:
        return Fix(False, f"{len(deps)} worker deployment(s) ≤ target {target}; nothing to reap")
    deps.sort(key=lambda d: d.get("status", {}).get("readyReplicas", 0))  # Pending first
    reaped = 0
    for d in deps[:excess]:
        if ctx.k(["delete", "deployment", d["metadata"]["name"], "-n", "dask",
                  "--wait=false"]).returncode == 0:
            reaped += 1
    return Fix(reaped > 0, f"capped to {target} (schedulable={cap['schedulable_nodes']}); "
                           f"reaped {reaped} orphaned/excess worker deployment(s)")


def _det_otel_navigator(ctx: Ctx) -> Probe:
    pods = ctx.items("pods", ns="panel-viz", selector="app=otel-navigator")
    if not pods:
        return Probe(False, "otel-navigator not deployed")
    p = pods[0]
    phase = p.get("status", {}).get("phase")
    ready, total = ctx.pods_ready("panel-viz", "app=otel-navigator")
    if phase == "Pending":
        return Probe(False, "otel-navigator Pending (capacity?) — needs a node with its memory request free")
    return Probe(ready >= 1, f"otel-navigator ready {ready}/{total}")


def _rem_otel_navigator(ctx: Ctx) -> Fix:
    return _zarf_deploy_components(ctx, "panel-viz")


def _det_engine(ctx: Ctx) -> Probe:
    ready, total = ctx.pods_ready("panel-viz", "app=navigator-engine")
    return Probe(ready >= 1, f"navigator-engine ready {ready}/{total}")


def _rem_engine(ctx: Ctx) -> Fix:
    return _zarf_deploy_components(ctx, "navigator-engine")


def _det_jupyterhub(ctx: Ctx) -> Probe:
    ready, total = ctx.pods_ready("jupyterhub", "component=hub")
    return Probe(ready >= 1, f"jupyterhub hub ready {ready}/{total}")


def _rem_jupyterhub(ctx: Ctx) -> Fix:
    return _zarf_deploy_components(ctx, "jupyterhub")


def _det_sample_notebooks(ctx: Ctx) -> Probe:
    ok = ctx.exists("configmap", "sample-notebooks", ns="jupyterhub")
    return Probe(ok, "sample-notebooks ConfigMap present" if ok
                 else "sample-notebooks ConfigMap absent")


def _rem_sample_notebooks(ctx: Ctx) -> Fix:
    return _zarf_deploy_components(ctx, "sample-notebooks")


def _det_ingress(ctx: Ctx) -> Probe:
    ings = ctx.items("ingress")
    names = [i["metadata"]["name"] for i in ings]
    want = {"dask-dashboard", "panel-viz"}
    return Probe(want.issubset(set(names)), f"ingress: {names}")


def _rem_ingress(ctx: Ctx) -> Fix:
    return _zarf_deploy_components(ctx, "ingress")


# --------------------------------------------------------------------------- #
# Clean-slate teardown targets (the disposable Layer-B APP STACK)
# --------------------------------------------------------------------------- #
# `converge --teardown` removes these and nothing else. The foundational tier
# (zarf registry, local-path-storage / StorageClass) and ALL Layer-A node images
# are CONSERVED — teardown is the clean slate of the WORKLOADS, not the platform,
# so a subsequent `--apply` redeploys fast from the still-present registry.
APP_NAMESPACES = ["dask", "dask-operator", "jupyterhub", "panel-viz"]
DASK_CRD_KINDS = ["daskclusters", "daskworkergroups", "daskautoscalers", "daskjobs"]


# --------------------------------------------------------------------------- #
# The catalog
# --------------------------------------------------------------------------- #

def build_catalog(dynamic_provisioning: bool = False,
                  registry_pvc_enabled: bool = True) -> List[Invariant]:
    """The invariant catalog for the active storage modality.

    DEFAULT — RESILIENT AIR-GAP (the only modality public releases target): the single
    disk-limited node is the baseline. The Zarf registry binds a claimRef hostPath PV
    (``T0.5.registry-pv``), so NO default StorageClass, NO local-path-provisioner and
    NO bootstrap images (local-path-provisioner/busybox) are needed — the SC↔registry
    chicken-egg simply doesn't exist. Worker count is sized to capacity, but behavior
    never forks on node count.

    ``dynamic_provisioning=True`` — OPT-IN, for a resourced multi-node cluster running
    workloads that need dynamic PVCs: restores the default-StorageClass tier (the
    node-preloaded local-path-provisioner + its Layer-A bootstrap-image CLOSURE check)
    and routes the registry PVC through it.

    The T1→T6 tier (init → registry → images → components) is identical across
    modalities; only the T0.5 storage tier and ``T1.registry-running``'s dependency
    differ.
    """
    inv: List[Invariant] = [
        Invariant("T0.api", "T0", "Kubernetes API reachable", Layer.B, _det_api,
                  manual_hint="RKE2 down — `systemctl status rke2-server` on the control plane"),
        Invariant("T0.node-ready", "T0", "Nodes Ready and schedulable", Layer.B,
                  _det_node_ready, _rem_node_ready, depends_on=("T0.api",)),
        Invariant("T0.no-disk-pressure", "T0", "No disk-pressure taint (lenient eviction persisted)",
                  Layer.A, _det_no_disk_pressure, depends_on=("T0.api",),
                  manual_hint="free disk SAFELY (never `crictl rmi --prune`; remove only the package "
                              "tarball / journald / Failed pods) and ensure the RKE2 lenient-eviction "
                              "config is applied (infra/aws/ansible/roles/rke2-*/templates)"),
    ]

    # T0.5 — storage. Resilient (default): the registry binds a claimRef hostPath PV,
    # no StorageClass. Dynamic (opt-in): the node-preloaded local-path-provisioner
    # supplies a default StorageClass and the registry PVC binds through it.
    if dynamic_provisioning:
        inv += [
            Invariant("T0.layer-a-images", "T0", "Bootstrap images present (CLOSURE)", Layer.A,
                      _det_layer_a_images, depends_on=("T0.api",),
                      manual_hint="a bootstrap image (local-path-provisioner/busybox) is missing from the "
                                  "node's containerd. RE-IMPORT it from the package OCI layout or RKE2's "
                                  "bundled-images dir — NEVER pull/prune (it cannot be re-fetched in air-gap)"),
            Invariant("T0.5.sc-default", "T0.5", "Default StorageClass exists", Layer.B,
                      _det_sc_default, _rem_sc_default,
                      depends_on=("T0.node-ready", "T0.layer-a-images")),
            Invariant("T0.5.provisioner", "T0.5", "local-path-provisioner Running", Layer.B,
                      _det_provisioner, _rem_provisioner, depends_on=("T0.5.sc-default",)),
        ]
        registry_dep = ("T0.5.sc-default",)
    elif registry_pvc_enabled:
        inv += [
            Invariant("T0.5.registry-pv", "T0.5",
                      "Registry storage prebound (claimRef hostPath PV — no default SC needed)",
                      Layer.B, _det_registry_pv, _rem_registry_pv, depends_on=("T0.node-ready",)),
        ]
        registry_dep = ("T0.5.registry-pv",)
    else:
        # --no-registry-pvc: the registry runs on emptyDir — nothing to prebind.
        registry_dep = ("T0.node-ready",)

    inv += [
        Invariant("T1.registry-running", "T1", "Zarf internal registry initialized + Running",
                  Layer.B, _det_registry_running, _rem_registry_running, cost=Cost.EXPENSIVE,
                  depends_on=registry_dep),

        Invariant("T2.images-pushed", "T2", "App images pushed to internal registry",
                  Layer.B, _det_images_pushed, _rem_images_pushed, cost=Cost.EXPENSIVE,
                  depends_on=("T1.registry-running",)),

        Invariant("T3.dask-operator", "T3", "Dask operator + CRDs", Layer.B,
                  _det_operator, _rem_operator, depends_on=("T2.images-pushed",)),

        Invariant("T4.scheduler", "T4", "Dask scheduler Ready", Layer.B,
                  _det_scheduler, _rem_scheduler, depends_on=("T3.dask-operator",)),
        Invariant("T4.workers-capacity", "T4", "Workers fit schedulable capacity (no oversubscription)",
                  Layer.B, _det_workers_capacity, _rem_workers_capacity,
                  depends_on=("T4.scheduler",)),

        Invariant("T5.otel-navigator", "T5", "otel-navigator Ready (2/2, fits memory)", Layer.B,
                  _det_otel_navigator, _rem_otel_navigator,
                  depends_on=("T4.scheduler", "T4.workers-capacity")),
        Invariant("T5.navigator-engine", "T5", "navigator-engine Ready", Layer.B,
                  _det_engine, _rem_engine, depends_on=("T4.scheduler",)),
        Invariant("T5.jupyterhub", "T5", "JupyterHub hub Ready", Layer.B,
                  _det_jupyterhub, _rem_jupyterhub, depends_on=("T2.images-pushed",)),
        Invariant("T5.sample-notebooks", "T5", "Sample-notebooks ConfigMap present", Layer.B,
                  _det_sample_notebooks, _rem_sample_notebooks, depends_on=("T2.images-pushed",)),

        Invariant("T6.ingress", "T6", "Ingress resources present", Layer.B,
                  _det_ingress, _rem_ingress, depends_on=("T5.otel-navigator",)),
    ]
    return inv


# The RESILIENT air-gap modality is the DEFAULT (and the only one public releases
# target). This module-level catalog is what importers (by_id/layer_a_ids/tests) see;
# __main__ rebuilds per-run with the active flags (dynamic_provisioning / pvc).
CATALOG: List[Invariant] = build_catalog()


def by_id() -> dict:
    return {inv.id: inv for inv in CATALOG}


def layer_a_ids() -> list:
    return [inv.id for inv in CATALOG if inv.layer is Layer.A]
