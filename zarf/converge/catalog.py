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
import os
import stat
from pathlib import Path
from typing import List, Optional

from .discovery import APP_NAMESPACES, DASK_CRD_KINDS
from .kube import Ctx
from .model import Cost, Fix, Invariant, Layer, Probe
from . import platform as _platform

# Registry hostPath — non-root registry container; fsGroup does NOT chown hostPath.
REGISTRY_HOSTPATH = "/var/lib/zarf-registry"
REGISTRY_PVC_NAME = "zarf-docker-registry"
REGISTRY_PV_NAME = "zarf-registry-pv"
ZARF_NS = "zarf"

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
  storageClassName: "{sc}"
  hostPath:
    path: /var/lib/zarf-registry
    type: DirectoryOrCreate
  claimRef:
    namespace: zarf
    name: zarf-docker-registry
"""


def _registry_pv_yaml(ctx: Ctx, storage_class: str = "") -> str:
    """The static claimRef hostPath registry PV, with storageClassName set to match the
    class the registry PVC requests ("" by default — the resilient no-default-SC path).
    Setting it to the PVC's actual class lets the PV bind statically with NO provisioner."""
    return REGISTRY_PV_YAML.format(size=ctx.registry_pv_size, sc=storage_class)


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


def _ensure_namespace(ctx: Ctx, ns: str) -> bool:
    """Create ``ns`` if missing. Required for split-brain: force-finalize removed the
    Namespace object while PVC/Service etcd keys survive; namespaced writes then fail
    with ``namespaces \"X\" not found`` until the ns is recreated (husks re-surface —
    expected; caller must drain). Returns True if it created the ns."""
    if ctx.exists("namespace", ns):
        return False
    r = ctx.k(["create", "namespace", ns])
    return r.returncode == 0


def _release_pvc_mounts(ctx: Ctx, ns: str, pvc_name: str) -> List[str]:
    """Stop controllers/pods that keep ``kubernetes.io/pvc-protection`` alive.
    Field: deleting the PVC while the registry pod still mounts it → Terminating forever."""
    actions: List[str] = []
    # Controllers first so they stop recreating mount pods.
    for kind in ("deploy", "sts", "ds", "job"):
        r = ctx.k(["delete", kind, "--all", "-n", ns, "--wait=false", "--ignore-not-found"])
        if r.returncode == 0 and (r.stdout or "").strip():
            actions.append(f"deleted {kind} in {ns} (release PVC mounts)")
    holders = []
    for p in ctx.items("pods", ns=ns):
        for v in p.get("spec", {}).get("volumes") or []:
            claim = (v.get("persistentVolumeClaim") or {}).get("claimName")
            if claim == pvc_name:
                holders.append(p["metadata"]["name"])
                break
    for pod in holders:
        ctx.k(["delete", "pod", pod, "-n", ns,
               "--force", "--grace-period=0", "--wait=false", "--ignore-not-found"])
        actions.append(f"force-deleted pod {ns}/{pod} holding PVC {pvc_name}")
    if not holders:
        # Belt: nuke all pods in ns if any still running (partial init)
        pods = ctx.items("pods", ns=ns)
        if pods:
            ctx.k(["delete", "pods", "--all", "-n", ns,
                   "--force", "--grace-period=0", "--wait=false", "--ignore-not-found"])
            actions.append(f"force-deleted all pods in {ns} (PVC release)")
    return actions


def _force_delete_pvc(ctx: Ctx, ns: str, name: str) -> bool:
    """Delete a PVC and, if it lingers on the kubernetes.io/pvc-protection finalizer,
    clear the finalizer so it actually goes. A PVC's storageClassName is IMMUTABLE, so a
    registry PVC the cluster-default SC captured onto a dead provisioner can never be
    salvaged in place — it must be removed so `zarf init` recreates it clean. Layer-B:
    the registry's hostPath DATA is conserved by the Retain PV, so images survive.

    Anticipates: (1) mounts holding the protection finalizer, (2) ns absent (split-brain)
    → re-create ns so the patch can address the object, (3) merge+json finalizer strip."""
    if not ctx.exists("namespace", ns):
        _ensure_namespace(ctx, ns)
    _release_pvc_mounts(ctx, ns, name)
    ctx.k(["delete", "pvc", name, "-n", ns, "--ignore-not-found", "--wait=false"])
    pvc = ctx.get("pvc", name, ns=ns)
    if pvc is not None:
        # merge null, then JSON remove if still present
        ctx.k(["patch", "pvc", name, "-n", ns, "--type=merge",
               "-p", '{"metadata":{"finalizers":null}}'])
        if ctx.get("pvc", name, ns=ns):
            ctx.k(["patch", "pvc", name, "-n", ns, "--type=json",
                   "-p", '[{"op":"remove","path":"/metadata/finalizers"}]'])
        # last resort: replace object with finalizers cleared (namespaced API; ns must exist)
        if ctx.get("pvc", name, ns=ns):
            obj = ctx.get("pvc", name, ns=ns)
            if obj:
                obj.setdefault("metadata", {})["finalizers"] = []
                ctx.run(ctx.kubectl + ["replace", "-f", "-"], input_=json.dumps(obj))
    return ctx.get("pvc", name, ns=ns) is None


def _drain_namespace(ctx: Ctx, ns: str, *, strip_finalizers: bool = True) -> List[str]:
    """Empty a namespace's contents (workloads, services, config, PVCs) without
    deleting the Namespace object. Used for: Terminating ns (must drain before
    finalize), Active husk ns (34d zarf-injector Service with no Ready registry),
    and pre-init cleanup of partial seed-registry installs.

    Order: controllers → pods (force) → services/cm/secret → PVC (with mount release).
    NEVER touches hostPath registry data. Layer-B only."""
    if not ctx.exists("namespace", ns):
        return []
    actions: List[str] = []
    for kind in ("deployments", "replicasets", "statefulsets", "daemonsets",
                 "jobs", "cronjobs"):
        ctx.k(["delete", kind, "--all", "-n", ns, "--wait=false", "--ignore-not-found"])
    ctx.k(["delete", "pods", "--all", "-n", ns,
           "--force", "--grace-period=0", "--wait=false", "--ignore-not-found"])
    for kind in ("services", "endpoints", "configmaps", "secrets", "roles",
                 "rolebindings", "serviceaccounts"):
        # keep default SA; deleting all SAs is fine — k8s recreates default
        ctx.k(["delete", kind, "--all", "-n", ns, "--wait=false", "--ignore-not-found"])
    # PVCs last among namespaced objects
    for pvc in list(ctx.items("pvc", ns=ns)):
        name = pvc.get("metadata", {}).get("name")
        if not name:
            continue
        _release_pvc_mounts(ctx, ns, name)
        if _force_delete_pvc(ctx, ns, name):
            actions.append(f"drained PVC {ns}/{name}")
        else:
            actions.append(f"PVC {ns}/{name} still present after force-delete attempt")
    if strip_finalizers:
        for kind in _NS_CONTENT_KINDS + ("pods",):
            for it in ctx.items(kind, ns=ns):
                if (it.get("metadata", {}) or {}).get("finalizers"):
                    n = it["metadata"]["name"]
                    ctx.k(["patch", kind, n, "-n", ns, "--type=merge",
                           "-p", '{"metadata":{"finalizers":null}}'])
    leftovers = []
    for kind in ("deployments", "pods", "services", "pvc"):
        n = len(ctx.items(kind, ns=ns))
        if n:
            leftovers.append(f"{kind}={n}")
    if leftovers:
        actions.append(f"drain {ns} incomplete: {','.join(leftovers)}")
    else:
        actions.append(f"drained ns {ns} (empty)")
    return actions


def _ensure_registry_hostpath() -> List[str]:
    """Make registry hostPath exist and world-writable (platform helper)."""
    return _platform.ensure_registry_hostpath()

def _registry_ready_count(ctx: Ctx) -> tuple:
    ready, total = ctx.pods_ready(ZARF_NS, "app=docker-registry")
    if ready < 1 and total == 0:
        ready, total = ctx.pods_ready(
            ZARF_NS, "app.kubernetes.io/name=zarf-docker-registry")
    return ready, total


def _zarf_ns_husk_detail(ctx: Ctx) -> Optional[str]:
    """When the ``zarf`` ns should be drained before re-init.

    Returns a detail string if: Terminating; or Active with no Ready registry AND
    (bad/missing PVC or only husk leftovers). Returns None if healthy or if storage
    is already correct (Bound PVC on sc \"\") — pod not Ready yet is *not* a husk
    (would destroy an in-progress init).
    """
    ns = ctx.get("namespace", ZARF_NS)
    if not ns:
        return None
    if ns.get("status", {}).get("phase") == "Terminating":
        return "zarf ns Terminating (drain+finalize required)"
    ready, total = _registry_ready_count(ctx)
    if ready >= 1:
        return None
    pvc = ctx.get("pvc", REGISTRY_PVC_NAME, ns=ZARF_NS)
    if pvc is not None:
        phase = pvc.get("status", {}).get("phase")
        sc = (pvc.get("spec", {}) or {}).get("storageClassName")
        sc = "" if sc is None else sc
        del_ts = (pvc.get("metadata", {}) or {}).get("deletionTimestamp")
        if del_ts or phase == "Terminating":
            return (f"registry PVC Terminating (deleting={bool(del_ts)}) — "
                    "mount/finalizer wedge")
        if phase == "Bound" and sc == "":
            # Correct resilient bind; wait for registry pods — do NOT drain.
            return None
        if phase == "Pending" or sc != "":
            return (f"registry PVC phase={phase} sc={sc!r} (capture or unbound) — "
                    "delete before re-init")
    bits = []
    for kind in ("services", "deployments", "statefulsets", "secrets", "pvc", "pods"):
        items = ctx.items(kind, ns=ZARF_NS)
        if not items:
            continue
        created = (items[0].get("metadata", {}) or {}).get("creationTimestamp", "")
        bits.append(f"{kind}={len(items)}" + (f"@{created[:10]}" if created else ""))
    if bits:
        return (f"zarf husk/partial: registry ready {ready}/{total}; leftovers "
                + ", ".join(bits))
    return None

def _unwedge_failed_seed_registry(ctx: Ctx) -> List[str]:
    """After a failed ``zarf init`` Helm install of zarf-seed-registry (context
    deadline exceeded), clear pending helm secrets and partial chart objects so the
    next init is a clean install, not an upgrade of a broken release."""
    actions: List[str] = []
    actions += _unwedge_pending_helm(ctx)
    if not ctx.exists("namespace", ZARF_NS):
        return actions
    # seed chart objects without a Ready registry
    ready, _ = _registry_ready_count(ctx)
    if ready >= 1:
        return actions
    for kind in ("deploy", "sts", "job"):
        ctx.k(["delete", kind, "--all", "-n", ZARF_NS,
               "--wait=false", "--ignore-not-found"])
    ctx.k(["delete", "pods", "--all", "-n", ZARF_NS,
           "--force", "--grace-period=0", "--wait=false", "--ignore-not-found"])
    actions.append("cleared partial seed-registry workloads in zarf ns")
    # Best-effort: zarf package remove for init seed component (syntax varies by version)
    if ctx.have_zarf():
        for args in (
            ["package", "remove", "init", "--confirm",
             "--components=zarf-seed-registry"],
            ["package", "remove", "zarf-seed-registry", "--confirm"],
        ):
            r = ctx.zarf(args, timeout=120)
            if r.returncode == 0:
                actions.append(f"zarf {' '.join(args[:3])} ok")
                break
    return actions


_HELM_PENDING = ("pending-install", "pending-upgrade", "pending-rollback")

# A ref the zarf agent MUST rewrite at admission. Never pulled — server dry-run only.
_CANARY_REF = "ghcr.io/zarf-canary/agent-check:v1"
_IMAGE_WAIT_BAD = ("ImagePullBackOff", "ErrImagePull", "ErrImageNeverPull")


def _poisoned_app_ns(ctx: Ctx) -> List[str]:
    """App namespaces carrying ``zarf.dev/agent=ignore`` — the SILENT killer of image
    rewriting. Zarf's agent mutates pod image refs at admission (the helm manifests
    keep upstream refs like ghcr.io by design), and its webhook EXCLUDES namespaces
    labeled ignore/skip. ``zarf init`` labels every PRE-EXISTING namespace ignore (so
    it won't disturb prior workloads) — correct on first init, but a RE-RUN of init
    over an existing deployment finds the app namespaces already present and poisons
    them all. Latent until any pod churn: the recreated pod keeps its upstream ref and
    ImagePullBackOffs forever in the closed world. Field-proven on the sandbox."""
    out = []
    for ns in APP_NAMESPACES:
        obj = ctx.get("namespace", ns)
        if not obj:
            continue
        labels = (obj.get("metadata", {}) or {}).get("labels", {}) or {}
        if labels.get("zarf.dev/agent") in ("ignore", "skip"):
            out.append(ns)
    return out


def _strip_agent_ignore(ctx: Ctx) -> List[str]:
    """Remove the ``zarf.dev/agent=ignore`` label from OUR app namespaces so the agent
    mutates their pods again. Only the package's own namespaces — never cluster/system
    namespaces, where the ignore label is correct and deliberate. Idempotent."""
    actions: List[str] = []
    for ns in _poisoned_app_ns(ctx):
        if ctx.k(["label", "namespace", ns, "zarf.dev/agent-", "--overwrite"]).returncode == 0:
            actions.append(f"stripped zarf.dev/agent=ignore from ns {ns} "
                           "(re-init had disabled image rewriting there)")
    return actions


def _webhook_mutating(ctx: Ctx) -> "bool | None":
    """Does the zarf agent ACTUALLY rewrite an upstream image ref in an APP namespace
    right now? A SERVER-side dry-run exercises the full admission chain (webhook
    selectors included), persists nothing and pulls nothing. Runs against the first
    EXISTING app namespace — zarf deliberately ignores pre-init namespaces like
    ``default``, so probing there would be a permanent false negative.
    True=mutating, False=bypassed, None=no verdict (no app ns yet / probe failed)."""
    ns = next((n for n in APP_NAMESPACES if ctx.exists("namespace", n)), None)
    if ns is None:
        return None
    r = ctx.k(["-n", ns, "run", "zarf-agent-canary",
               f"--image={_CANARY_REF}", "--restart=Never", "--dry-run=server",
               "-o", "jsonpath={.spec.containers[0].image}"])
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return _CANARY_REF not in r.stdout


_DASK_ENV_KEYS = ("S3_ENDPOINT", "AWS_REGION", "AWS_ACCESS_KEY_ID",
                  "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")


def _unwedge_broken_dask_cluster(ctx: Ctx) -> List[str]:
    """The dask operator (kopf) creates the scheduler Deployment only on the CR's
    CREATION event — it neither propagates CR env changes to existing children nor
    recreates a deleted child (both field-proven). So two wedge states can only be
    fixed at the CR level: (a) scheduler/worker Deployments carrying S3/AWS env that
    DRIFTED from the CR (a redeploy fixed the CR's creds; pods keep the old — possibly
    empty — env forever, and the bucket-ensure action execs the scheduler using ITS
    env), and (b) the scheduler Deployment MISSING under a live CR (zarf re-applying
    the unchanged CR is a server-side no-op → no create event → it never returns).
    Remediation: delete the DaskCluster CR (clearing kopf's finalizer if it lingers) —
    the imminent component deploy re-applies it as a fresh CREATE and the operator
    builds scheduler+workers from it. Layer-B; no data lives in these pods."""
    cr = ctx.get("daskcluster", "cybersec-dask", ns="dask")
    if not cr:
        return []

    def env_map(spec: dict) -> dict:
        m = {}
        for c in (spec or {}).get("containers", []) or []:
            for e in c.get("env", []) or []:
                m[e.get("name")] = e.get("value", "") or ""
        return m

    want = {
        "scheduler": env_map(cr.get("spec", {}).get("scheduler", {}).get("spec", {})),
        "worker": env_map(cr.get("spec", {}).get("worker", {}).get("spec", {})),
    }
    scheds = ctx.items("deployments", ns="dask", selector="dask.org/component=scheduler")
    drifted = []
    for d in ctx.items("deployments", ns="dask"):
        role = (d.get("metadata", {}).get("labels", {}) or {}).get("dask.org/component", "")
        if role not in want:
            continue
        have = env_map(d.get("spec", {}).get("template", {}).get("spec", {}))
        keys = [k for k in _DASK_ENV_KEYS if k in want[role]]
        if any(have.get(k, "") != want[role].get(k, "") for k in keys):
            drifted.append(d["metadata"]["name"])
    if scheds and not drifted:
        return []
    reason = ("scheduler Deployment missing under a live CR" if not scheds
              else f"S3 env drifted from the CR on {drifted}")
    ctx.k(["delete", "daskcluster", "cybersec-dask", "-n", "dask",
           "--ignore-not-found", "--wait=false"])
    if ctx.get("daskcluster", "cybersec-dask", ns="dask"):   # kopf finalizer lingering
        ctx.k(["patch", "daskcluster", "cybersec-dask", "-n", "dask", "--type=merge",
               "-p", '{"metadata":{"finalizers":null}}'])
    return [f"deleted DaskCluster CR ({reason}) — the deploy re-creates it fresh "
            "(the operator only builds children on CR creation)"]


def _unwedge_unmutated_pods(ctx: Ctx) -> List[str]:
    """Pods admitted while the webhook was bypassed carry UPSTREAM image refs and
    ImagePullBackOff forever in the closed world — and a no-diff helm upgrade will NOT
    recreate them, so the deploy's --wait times out again and again. Delete them; their
    controllers re-create them through the (by now effective) webhook. Precise: only
    ImagePull-stuck pods whose ref is NOT the internal registry — an internal-ref pod
    stuck pulling is a T2 registry-content problem, not an admission one. Layer-B."""
    actions: List[str] = []
    for ns in APP_NAMESPACES:
        for p in ctx.items("pods", ns=ns):
            name = p.get("metadata", {}).get("name", "")
            stuck = any(
                ((cs.get("state", {}) or {}).get("waiting") or {}).get("reason") in _IMAGE_WAIT_BAD
                for cs in (p.get("status", {}).get("containerStatuses", []) or []))
            if not stuck:
                continue
            imgs = [c.get("image", "") for c in p.get("spec", {}).get("containers", [])]
            if any(i and not i.startswith("127.0.0.1:") for i in imgs):
                if ctx.k(["delete", "pod", name, "-n", ns, "--wait=false"]).returncode == 0:
                    actions.append(f"deleted unmutated ImagePull-stuck pod {ns}/{name} "
                                   "(upstream ref — re-admits via the webhook)")
    return actions


def _unwedge_pending_helm(ctx: Ctx) -> List[str]:
    """A converge/zarf killed mid-deploy leaves its Helm release pending-install/
    pending-upgrade — after which EVERY retry of that chart fails with 'another
    operation (install/upgrade/rollback) is in progress'. Deleting the LATEST
    (pending) release secret reverts Helm's view to the previous deployed revision so
    the next deploy proceeds. Only the newest revision per release matters (Helm reads
    release status from it); older secrets are history and are left alone. Layer-B —
    pure Helm bookkeeping, no workload/image is touched."""
    obj = ctx.kjson(["get", "secrets", "-A", "-l", "owner=helm"]) or {}
    latest: dict = {}   # (ns, release) -> (version, secret_name, status)
    for s in obj.get("items", []):
        md = s.get("metadata", {}) or {}
        lab = md.get("labels", {}) or {}
        try:
            ver = int(lab.get("version", 0))
        except (TypeError, ValueError):
            continue
        key = (md.get("namespace"), lab.get("name"))
        if key not in latest or ver > latest[key][0]:
            latest[key] = (ver, md.get("name"), lab.get("status"))
    actions: List[str] = []
    for (ns, rel), (ver, name, status) in latest.items():
        if status in _HELM_PENDING and ns and name:
            if ctx.k(["delete", "secret", name, "-n", ns]).returncode == 0:
                actions.append(f"unwedged pending Helm release {ns}/{rel} "
                               f"(deleted stuck rev {ver}: {status})")
    return actions


_NS_CONTENT_KINDS = ("deployments", "replicasets", "statefulsets", "daemonsets",
                     "services", "configmaps", "secrets", "pvc", "jobs",
                     "ingresses", "networkpolicies", "endpointslices")


def _unwedge_terminating_app_ns(ctx: Ctx) -> List[str]:
    """App-namespace wedges (also covered by discovery.sweep_vestiges each pass).

    TERMINATING — drain contents (incl. ingress/finalizers) then finalize when empty.
    ACTIVE HUSKS — controllers/services with zero pods, or all-junk pods: recycle ns.
    Layer-B only; package redeploy recreates everything.
    """
    from .discovery import _drain_ns, _is_app_husk, _force_finalize_ns as _ff

    actions: List[str] = []
    for ns in APP_NAMESPACES:
        obj = ctx.get("namespace", ns)
        if not obj:
            continue
        phase = obj.get("status", {}).get("phase")
        if phase == "Terminating":
            actions.extend(_drain_ns(ctx, ns))
            leftovers = any(ctx.items(k, ns=ns) for k in ("deployments", "pods", "pvc"))
            if not leftovers:
                if _ff(ctx, ns) or _force_finalize_ns(ctx, ns):
                    actions.append(f"force-finalized Terminating ns {ns} (contents cleared first)")
            else:
                actions.append(f"clearing contents of Terminating ns {ns} (finalize next pass)")
        elif phase == "Active":
            reason = _is_app_husk(ctx, ns)
            if reason:
                actions.extend(_drain_ns(ctx, ns))
                ctx.k(["delete", "namespace", ns, "--wait=false"])
                actions.append(f"deleted husk ns {ns} ({reason})")
    return actions


# S3 secrets → ZARF_CONFIG tmpfs [package.deploy.set]. Non-secrets → --set-variables.
# Bare ZARF_VAR_* env does NOT template in zarf v0.70.1 (field-proven empty renders).
_S3_SECRET_KEYS = {"S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_SESSION_TOKEN"}
# Components whose manifests template a non-empty S3_BUCKET into a configMap. Deploying
# them with a blank bucket silently bricks the app at runtime, so we refuse instead.
_S3_DEPENDENT_COMPONENTS = ("panel-viz", "navigator-engine")


def _zarf_deploy_components(ctx: Ctx, components: str) -> Fix:
    if not (ctx.have_zarf() and ctx.package_path):
        return Fix(False, f"MANUAL: zarf package deploy --components={components} "
                          "(zarf/package not available to this engine instance)")
    # Fail LOUD rather than render an empty S3_BUCKET. An S3-dependent component
    # deployed with a blank bucket renders OTEL_DATA_PATH=s3:/// and bricks the app
    # ("Invalid bucket name 's3:'"), so refuse instead of silently breaking it.
    if any(c in components for c in _S3_DEPENDENT_COMPONENTS) and not ctx.s3.get("S3_BUCKET"):
        return Fix(False,
                   f"MANUAL: refusing to deploy {components} — S3_BUCKET not provided to "
                   "converge (would render OTEL_DATA_PATH=s3:/// → runtime 'Invalid bucket "
                   "name s3:'). Re-run with S3_BUCKET set (export it before converge-aws.sh, "
                   "or pass --set-variables S3_BUCKET=… / --creds-file).")
    # Unwind deploy-blocking wedges BEFORE zarf. Stamp detected INGRESS_CLASS so
    # redeploys never re-introduce traefik-on-RKE2 silent 404s.
    _platform.ensure_ingress_class_in_ctx(ctx)
    # Default resilient worker count if unset (package default may be multi-node).
    if not ctx.s3.get("DASK_WORKER_REPLICAS"):
        ctx.s3["DASK_WORKER_REPLICAS"] = "1"
    unwound = (_unwedge_pending_helm(ctx) + _unwedge_terminating_app_ns(ctx)
               + _strip_agent_ignore(ctx) + _unwedge_unmutated_pods(ctx)
               + _unwedge_broken_dask_cluster(ctx))
    pre = f"  [unwound: {'; '.join(unwound)}]" if unwound else ""
    args = ["package", "deploy", ctx.package_path, "--confirm",
            f"--components={components}", "--retries", "10"]
    if not ctx.registry_pvc_enabled:
        args.append("--set-variables=REGISTRY_PVC_ENABLED=false")
    # Non-sensitive vars → --set-variables; secrets → ZARF_CONFIG tmpfs.
    env = {}
    secrets = {}
    for k, v in ctx.s3.items():
        if not v:
            continue
        if k.upper() in _S3_SECRET_KEYS:
            secrets[k.upper()] = v
        else:
            args.append(f"--set-variables={k.upper()}={v}")
    cfg_path = None
    if secrets:
        import tempfile
        d = "/dev/shm" if Path("/dev/shm").is_dir() else None
        fd, cfg_path = tempfile.mkstemp(prefix=".zarf-cfg-", suffix=".toml", dir=d)
        with open(fd, "w") as f:   # mkstemp: 0600
            f.write("[package.deploy.set]\n")
            for k, v in secrets.items():
                esc = v.replace("\\", "\\\\").replace('"', '\\"')
                f.write(f'{k} = "{esc}"\n')
        env["ZARF_CONFIG"] = cfg_path
    try:
        r = ctx.zarf(args, env=env)
    finally:
        if cfg_path:
            try:
                Path(cfg_path).write_bytes(b"\0" * 256)   # best-effort scrub (tmpfs)
                Path(cfg_path).unlink()
            except OSError:
                pass
    if r.returncode == 0:
        return Fix(True, f"zarf deploy {components}: rc=0{pre}")
    # Surface the actual zarf/Helm error tail, not a bare rc=1 — the deploy failures
    # (dask-operator/jupyterhub) only showed "rc=1" all afternoon. Last lines tend to
    # carry the cause (chart timeout, image pull, CRD hook); S3 secrets ride env, not
    # stdout, so this stays clean.
    tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
    suffix = f" — {' / '.join(s.strip() for s in tail)}" if tail else ""
    return Fix(False, f"zarf deploy {components}: rc={r.returncode}{suffix}{pre}")


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

def _det_layer_a_zarf_tools(ctx: Ctx) -> Probe:
    """Layer-A CLOSURE: zarf binary executable + zarf-init package discoverable.
    Field: ``zarf init rc=127`` and 'requires a zarf-init package' both surface here
    before T1 burns an EXPENSIVE remediation cycle."""
    if not ctx.have_zarf():
        return Probe(False, "zarf binary not available on PATH / --zarf")
    r = ctx.zarf(["version"], timeout=30)
    if r.returncode == 127:
        return Probe(False, "zarf binary not executable (rc=127)")
    if r.returncode != 0:
        return Probe(False, f"zarf version failed rc={r.returncode}")
    # init package: beside deploy package, /var/tmp, or cwd
    search: List[Path] = []
    if ctx.package_path:
        search.append(Path(ctx.package_path).resolve().parent)
    search += [Path("/var/tmp"), Path.cwd()]
    found = None
    for d in search:
        try:
            matches = sorted(d.glob("zarf-init-*.tar.zst"))
        except OSError:
            continue
        if matches:
            found = matches[0]
            break
    if not found:
        return Probe(False,
                     "zarf-init-*.tar.zst not found beside package or in /var/tmp "
                     "(Layer A — transport the release init package)")
    return Probe(True, f"zarf ok; init package {found.name}")


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
    r = ctx.apply_yaml(_registry_pv_yaml(ctx))
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
    if ready < 1:
        return Probe(False, f"zarf-docker-registry ready {ready}/{total}")
    # A Running registry is NOT a completed init. The agent-hook mutating webhook
    # rewrites image refs to the internal registry at admission — with it dead or
    # absent (e.g. a partial init, or a force-finalized ns that took the agent with
    # it), every later deploy's pods ImagePullBackOff against ghcr.io/quay.io and the
    # loop stalls WITHOUT naming the cause. Same remediation either way: re-run
    # `zarf init` (idempotent — redeploys agent + webhook). Matched by pod-name
    # prefix, not a label guess.
    agents = [p for p in ctx.items("pods", ns="zarf")
              if p.get("metadata", {}).get("name", "").startswith("agent-hook")]
    a_ready = sum(
        1 for p in agents
        if {c["type"]: c["status"]
            for c in p.get("status", {}).get("conditions", [])}.get("Ready") == "True")
    if a_ready < 1:
        return Probe(False,
                     f"registry Ready but zarf agent-hook {a_ready}/{len(agents)} — init "
                     "incomplete (image refs won't be rewritten); re-init required")
    # Ready agents are NOT enough — the agent must be BEHAVIORALLY effective for OUR
    # namespaces. The field killer: a RE-RUN `zarf init` labels the (now pre-existing)
    # app namespaces zarf.dev/agent=ignore, silently disabling image rewriting; any
    # later pod churn then ImagePullBackOffs on upstream refs in the closed world.
    poisoned = _poisoned_app_ns(ctx)
    if poisoned:
        return Probe(False,
                     f"app namespaces labeled zarf.dev/agent=ignore: {poisoned} — a re-run "
                     "`zarf init` disabled image rewriting there (pods created later keep "
                     "upstream refs → ImagePullBackOff air-gapped); label strip required")
    # End-to-end proof: a canary server dry-run in an app namespace must come back
    # rewritten (exercises the webhook + selectors; persists nothing, pulls nothing).
    mut = _webhook_mutating(ctx)
    if mut is False:
        return Probe(False,
                     "registry+agent Ready, app namespaces unlabeled, but the agent did NOT "
                     "rewrite a canary in an app namespace — admission not reaching the agent; "
                     "re-init required")
    return Probe(True, f"registry ready {ready}/{total}, agent-hook {a_ready}/{len(agents)}"
                       + (", agent rewriting (canary)" if mut else ""))


def _default_storage_classes(ctx: Ctx) -> List[str]:
    """Names of StorageClasses currently marked cluster-default."""
    obj = ctx.kjson(["get", "storageclass"]) or {}
    out = []
    for sc in obj.get("items", []):
        ann = sc.get("metadata", {}).get("annotations") or {}
        if ann.get("storageclass.kubernetes.io/is-default-class") == "true":
            name = sc.get("metadata", {}).get("name")
            if name:
                out.append(name)
    return out


def _undefault_sc(ctx: Ctx, name: str) -> bool:
    """Strip the default-class annotation from a StorageClass — the inverse of
    _ensure_default_sc — so a PVC that omits a class falls through to "" instead of
    waiting on this SC's provisioner."""
    r = ctx.k(["patch", "storageclass", name, "-p",
               '{"metadata":{"annotations":'
               '{"storageclass.kubernetes.io/is-default-class":"false"}}}'])
    return r.returncode == 0


def _pre_init_cleanup(ctx: Ctx) -> List[str]:
    """Exhaustively unwind every registry/storage state that makes ``zarf init`` fail
    or hang (rc=1, rc=124 context deadline, rc=127 missing binary), so a plain init can
    succeed. Idempotent + CONSERVATION-safe: never deletes hostPath registry DATA.

    Generalized from field sessions (anticipatory catalog — every special case is one
    row of this procedure):

      • hostPath /var/lib/zarf-registry not writable     → mkdir+chmod 0777
      • zarf ns Terminating                              → drain contents, then finalize
      • zarf ns absent + orphaned PVC/husk (split-brain) → create ns, drain husks
      • zarf ns Active husk (injector Service, no registry) → full drain
      • partial seed-registry Helm (deadline exceeded)   → pending-helm + partial workloads
      • cluster-default StorageClass                     → un-default (hygiene)
      • registry PVC Terminating / Pending / wrong class → release mounts + force-delete
      • static PV Released/Failed/class-drift            → reset object (data retained)
      • app ns agent=ignore poison                       → strip labels
    Pairs with `zarf init --storage-class -` in `_rem_registry_running`.
    """
    actions: List[str] = []

    # 0. HostPath first — seed-registry Helm waits on a registry that cannot write.
    for a in _ensure_registry_hostpath():
        if not a.startswith("hostPath ") or "ready" not in a:
            actions.append(a)
        elif "MANUAL" in a or "not writable" in a or "failed" in a:
            actions.append(a)

    # A. Namespace topology: Terminating | absent (split-brain) | Active husk | healthy
    ns = ctx.get("namespace", ZARF_NS)
    if ns and ns.get("status", {}).get("phase") == "Terminating":
        actions += _drain_namespace(ctx, ZARF_NS)
        leftovers = any(ctx.items(k, ns=ZARF_NS)
                        for k in ("deployments", "pods", "pvc", "services"))
        if not leftovers:
            if _force_finalize_ns(ctx, ZARF_NS):
                actions.append("force-finalized Terminating zarf ns (contents cleared first)")
        else:
            actions.append("zarf ns Terminating — contents still draining (next pass)")
    elif ns is None:
        # Split-brain path: PVC/Service may reappear when ns is recreated.
        if _ensure_namespace(ctx, ZARF_NS):
            actions.append("recreated absent zarf ns (split-brain re-home for husk objects)")
        husk = _zarf_ns_husk_detail(ctx)
        if husk or ctx.items("pvc", ns=ZARF_NS) or ctx.items("services", ns=ZARF_NS):
            actions += _drain_namespace(ctx, ZARF_NS)
            if husk:
                actions.append(f"drained after recreate: {husk}")
    else:
        husk = _zarf_ns_husk_detail(ctx)
        if husk:
            actions += _drain_namespace(ctx, ZARF_NS)
            actions.append(f"drained zarf husk/partial ({husk})")

    # A2. Failed seed chart / pending helm — before another init races the same release.
    actions += _unwedge_failed_seed_registry(ctx)

    # A3. Agent poison on app namespaces (re-init labels pre-existing ns ignore).
    actions += _strip_agent_ignore(ctx)

    # Storage unwind is RESILIENT-path only (static claimRef PV).
    if not (ctx.registry_pvc_enabled and not ctx.dynamic_provisioning):
        return actions

    # B. Un-default cluster-default StorageClasses (hygiene; may be zero SCs — fine).
    for sc in _default_storage_classes(ctx):
        if _undefault_sc(ctx, sc):
            actions.append(f"un-defaulted StorageClass {sc!r}")

    # C. Registry PVC: Terminating, Pending, wrong class, or deletingTimestamp —
    #    never salvage in place (class immutable). Healthy Bound + sc "" → keep.
    if not ctx.exists("namespace", ZARF_NS):
        _ensure_namespace(ctx, ZARF_NS)
    pvc = ctx.get("pvc", REGISTRY_PVC_NAME, ns=ZARF_NS)
    if pvc is not None:
        phase = pvc.get("status", {}).get("phase")
        cur = (pvc.get("spec", {}) or {}).get("storageClassName")
        cur = "" if cur is None else cur
        del_ts = (pvc.get("metadata", {}) or {}).get("deletionTimestamp")
        finals = (pvc.get("metadata", {}) or {}).get("finalizers") or []
        bad = bool(del_ts) or phase in ("Terminating", "Pending", "Lost") \
            or phase != "Bound" or cur != ""
        if bad:
            if _force_delete_pvc(ctx, ZARF_NS, REGISTRY_PVC_NAME):
                actions.append(
                    f"deleted registry PVC (was phase={phase} sc={cur!r} "
                    f"deleting={bool(del_ts)} finals={finals})")
            else:
                actions.append(
                    f"registry PVC STILL present after force-delete "
                    f"(phase={phase} sc={cur!r}) — will block init")

    # D. Static claimRef hostPath PV on storageClass "" (never adopt local-path).
    pv = ctx.get("pv", REGISTRY_PV_NAME)
    if pv is None:
        ctx.apply_yaml(_registry_pv_yaml(ctx, ""))
        actions.append('created static registry PV (storageClass="")')
    elif pv.get("status", {}).get("phase") != "Bound":
        phase = pv.get("status", {}).get("phase")
        cur_sc = (pv.get("spec", {}) or {}).get("storageClassName") or ""
        claim = (pv.get("spec", {}) or {}).get("claimRef") or {}
        if phase in ("Released", "Failed") or claim.get("uid") or cur_sc != "":
            ctx.k(["delete", "pv", REGISTRY_PV_NAME, "--ignore-not-found"])
            ctx.apply_yaml(_registry_pv_yaml(ctx, ""))
            actions.append(f'reset static registry PV → storageClass="" '
                           f"(was phase={phase} storageClass={cur_sc!r})")
    return actions


def _diagnose_registry(ctx: Ctx) -> str:
    """Inspect live registry/storage so a failed ``zarf init`` names the unmet
    condition (PVC Terminating, SC capture, hostPath, husk, rc=127, …)."""
    bits: List[str] = []
    if not ctx.have_zarf():
        bits.append("zarf binary unavailable (rc=127 class — install Layer-A binary)")
    ns = ctx.get("namespace", ZARF_NS)
    if not ns:
        bits.append("zarf ns absent (init created nothing, rolled back, or split-brain)")
    else:
        bits.append(f"zarf ns phase={ns.get('status', {}).get('phase', '?')}")
    husk = _zarf_ns_husk_detail(ctx)
    if husk:
        bits.append(husk)
    pvc = ctx.get("pvc", REGISTRY_PVC_NAME, ns=ZARF_NS) if ns else None
    if pvc:
        sc = (pvc.get("spec", {}) or {}).get("storageClassName")
        sc = "" if sc is None else sc
        phase = pvc.get("status", {}).get("phase", "?")
        del_ts = (pvc.get("metadata", {}) or {}).get("deletionTimestamp")
        finals = (pvc.get("metadata", {}) or {}).get("finalizers") or []
        vol = (pvc.get("spec", {}) or {}).get("volumeName") or ""
        bits.append(f"registry PVC phase={phase} sc={sc!r} vol={vol!r} "
                    f"deleting={bool(del_ts)} finals={finals}")
        if del_ts or phase == "Terminating":
            bits.append("PVC Terminating — release mounts + strip finalizers "
                        "(ns must exist for patch)")
        if sc != "":
            bits.append(f"registry PVC on {sc!r} not '' — default-SC capture (immutable); "
                        "delete PVC; init with --storage-class -")
        if phase == "Pending":
            pv = ctx.get("pv", REGISTRY_PV_NAME)
            if pv is None:
                bits.append("no static registry PV present to bind it")
            else:
                pvsc = (pv.get("spec", {}) or {}).get("storageClassName") or ""
                pvp = pv.get("status", {}).get("phase", "?")
                bits.append(f"static PV {pvp} sc={pvsc!r}"
                            + ("" if pvsc == sc else f" (≠ PVC sc {sc!r} → won't bind)"))
    elif ns:
        bits.append("no registry PVC")
    pv = ctx.get("pv", REGISTRY_PV_NAME)
    if pv:
        bits.append(
            f"static PV phase={pv.get('status', {}).get('phase')} "
            f"sc={(pv.get('spec') or {}).get('storageClassName')!r}")
    defs = _default_storage_classes(ctx)
    if defs:
        bits.append(f"default StorageClass {defs} present — resilient path wants none")
    if not defs and not ctx.items("storageclass"):
        bits.append("no StorageClasses (OK for resilient path)")
    # hostPath
    hp = Path(REGISTRY_HOSTPATH)
    if not hp.is_dir():
        bits.append(f"hostPath {REGISTRY_HOSTPATH} missing")
    else:
        try:
            mode = stat.S_IMODE(hp.stat().st_mode)
            if mode != 0o777:
                bits.append(f"hostPath mode={oct(mode)} (want 0777; registry is non-root)")
        except OSError as e:
            bits.append(f"hostPath stat failed: {e}")
    if ctx.pod_image_missing(ZARF_NS, "app=docker-registry"):
        bits.append("registry pod cannot pull its image (absent in the closed world)")
    # pod events (FailedMount / deadline clues)
    for p in ctx.items("pods", ns=ZARF_NS)[:3]:
        phase = p.get("status", {}).get("phase")
        name = p.get("metadata", {}).get("name", "?")
        if phase and phase != "Running":
            bits.append(f"pod {name} phase={phase}")
    return "; ".join(bits) or (
        "registry not Ready — inspect `kubectl -n zarf get pvc,pv,pods` + events")


def _rem_registry_running(ctx: Ctx) -> Fix:
    """Unwind platform wedges, then ``zarf init --storage-class -``. On failure,
    diagnose + second-pass cleanup for seed-registry deadline (partial Helm)."""
    actions = _pre_init_cleanup(ctx)
    if not ctx.have_zarf():
        return Fix(False,
                   "MANUAL: zarf binary missing (rc=127 class) — install Layer-A "
                   f"v0.70.x to /usr/local/bin/zarf  [unwound: {'; '.join(actions)}]"
                   if actions else
                   "MANUAL: zarf binary missing — install Layer-A to /usr/local/bin/zarf")

    # Prefer running init from the package directory so zarf-init-*.tar.zst is found.
    pkg_dir = None
    if ctx.package_path:
        pkg_dir = str(Path(ctx.package_path).resolve().parent)

    args = ["init", "--confirm", f"--set=REGISTRY_PVC_SIZE={ctx.registry_pv_size}"]
    if ctx.registry_pvc_enabled:
        # "-" sentinel → storageClassName:"" EXPLICITLY (binds static PV; no SC race).
        args += ["--storage-class=-"]
    else:
        args.append("--set=REGISTRY_PVC_ENABLED=false")

    def _run_init() -> "object":
        # cwd via env is insufficient; use run with explicit chdir in subprocess
        if pkg_dir and Path(pkg_dir).is_dir():
            import subprocess as _sp
            argv = [str(ctx.zarf_bin)] + args
            try:
                return _sp.run(
                    argv, capture_output=True, text=True, timeout=1800,
                    cwd=pkg_dir, env={**os.environ})
            except FileNotFoundError as e:
                return _sp.CompletedProcess(argv, 127, "", str(e))
            except _sp.TimeoutExpired as e:
                return _sp.CompletedProcess(argv, 124, e.stdout or "", "timeout")
        return ctx.zarf(args)

    r = _run_init()
    actions += _strip_agent_ignore(ctx)
    tail = f"  [unwound: {'; '.join(actions)}]" if actions else ""

    if r.returncode == 0:
        return Fix(True, f"zarf init: rc=0{tail}")

    # Second pass: seed-registry deadline / partial install often leaves recoverable state
    if r.returncode in (1, 124) or "deadline" in (r.stderr or "").lower():
        more = _unwedge_failed_seed_registry(ctx)
        more += _pre_init_cleanup(ctx)
        if more:
            actions += more
            r2 = _run_init()
            actions += _strip_agent_ignore(ctx)
            tail = f"  [unwound: {'; '.join(actions)}]"
            if r2.returncode == 0:
                return Fix(True, f"zarf init (retry after seed unwedge): rc=0{tail}")
            r = r2

    if r.returncode == 127:
        return Fix(False, f"zarf init rc=127 (command not found / not executable): "
                          f"{_diagnose_registry(ctx)}{tail}")
    return Fix(False, f"zarf init rc={r.returncode}: {_diagnose_registry(ctx)}{tail}")

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
        if r.returncode == 0:
            if "cybersec-dask" in r.stdout:
                return Probe(True, "registry catalog contains cybersec-dask")
            # Evidence of ABSENCE: catalog reachable and the app repo isn't there.
            # (Previously conservative-True — harmless only because required
            # components re-push on every deploy; being precise keeps the report
            # honest and pushes at T2 where it belongs.)
            return Probe(False, "registry catalog reachable but cybersec-dask absent — push needed")
    # Catalog unreachable / no pods to judge: defer to the component invariants.
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


# --- image-drift detection (so `apply` rolls a content/tag change) ----------
# converge's workload detects are otherwise readiness-based, so they no-op on a
# code change. These compare the RUNNING pod's cybersec-dask tag vs the TARGET tag
# in artifacts.manifest: a mismatch is drift -> re-deploy. Paired with a per-build
# tag (content-derived; the build flow's job) this makes a redeploy a single
# idempotent `converge apply`, retiring the manual forced component deploy.

def _image_tag(ref: str) -> str:
    """Original tag from a (possibly zarf-rewritten) image ref:
    '<reg>/cybersec-dask:2025.2.1-zarf-HASH' -> '2025.2.1'; digest-only -> ''."""
    ref = ref.split("@", 1)[0]
    last = ref.rsplit("/", 1)[-1]
    tag = ref.rsplit(":", 1)[-1] if ":" in last else ""
    return tag.split("-zarf-", 1)[0]


def _target_cybersec_tag(ctx: Ctx) -> str:
    for img in ctx.manifest.get("package_images", {}).get("images", []):
        if "cybersec-dask" in img.get("ref", ""):
            return _image_tag(img["ref"])
    return ""


def _image_drift(ctx: Ctx, ns: str, selector: str) -> "str | None":
    """A drift message if the running cybersec-dask pod isn't on the target tag,
    else None. Conservative: unknown target / no image -> no drift (never blocks)."""
    target = _target_cybersec_tag(ctx)
    if not target:
        return None
    for p in ctx.items("pods", ns=ns, selector=selector):
        for c in p.get("spec", {}).get("containers", []):
            img = c.get("image", "")
            if "cybersec-dask" in img:
                running = _image_tag(img)
                if running and running != target:
                    return f"image drift: running {running}, target {target}"
    return None


def _det_otel_navigator(ctx: Ctx) -> Probe:
    pods = ctx.items("pods", ns="panel-viz", selector="app=otel-navigator")
    if not pods:
        return Probe(False, "otel-navigator not deployed")
    p = pods[0]
    phase = p.get("status", {}).get("phase")
    ready, total = ctx.pods_ready("panel-viz", "app=otel-navigator")
    if phase == "Pending":
        return Probe(False, "otel-navigator Pending (capacity?) — needs a node with its memory request free")
    if ready >= 1:
        drift = _image_drift(ctx, "panel-viz", "app=otel-navigator")
        if drift:
            return Probe(False, f"otel-navigator {drift}")
    return Probe(ready >= 1, f"otel-navigator ready {ready}/{total}")


def _rem_otel_navigator(ctx: Ctx) -> Fix:
    # cybersec-images too, so the target image is pushed before the rollout (the
    # push is idempotent; a tag-drift redeploy needs the new image in the registry).
    return _zarf_deploy_components(ctx, "cybersec-images,panel-viz")


def _det_engine(ctx: Ctx) -> Probe:
    ready, total = ctx.pods_ready("panel-viz", "app=navigator-engine")
    if ready >= 1:
        drift = _image_drift(ctx, "panel-viz", "app=navigator-engine")
        if drift:
            return Probe(False, f"navigator-engine {drift}")
    return Probe(ready >= 1, f"navigator-engine ready {ready}/{total}")


def _rem_engine(ctx: Ctx) -> Fix:
    return _zarf_deploy_components(ctx, "cybersec-images,navigator-engine")


def _det_jupyterhub(ctx: Ctx) -> Probe:
    """Hub must be Ready; package uses sqlite-memory + singleuser storage none
    (no PVC). Any jupyterhub PVC is a vestige from an older chart and a wedge."""
    ready, total = ctx.pods_ready("jupyterhub", "component=hub")
    pvcs = ctx.items("pvc", ns="jupyterhub")
    if pvcs:
        names = [p.get("metadata", {}).get("name") for p in pvcs]
        phases = [p.get("status", {}).get("phase") for p in pvcs]
        return Probe(
            False,
            f"jupyterhub hub ready {ready}/{total}; unexpected PVC(s) {names} "
            f"phases={phases} — resilient package is sqlite-memory/storage none; "
            "delete PVC/PV vestiges then redeploy")
    if ready >= 1:
        return Probe(True, f"jupyterhub hub ready {ready}/{total} (no PVC — resilient)")
    return Probe(False, f"jupyterhub hub ready {ready}/{total}")


def _rem_jupyterhub(ctx: Ctx) -> Fix:
    """SC-less hub path: remove ALL jupyterhub PVCs + hub-db PVs (old sqlite-pvc /
    local-path vestiges), then redeploy chart (sqlite-memory, storage none)."""
    actions: List[str] = []
    for pvc in list(ctx.items("pvc", ns="jupyterhub")):
        name = pvc.get("metadata", {}).get("name", "")
        phase = pvc.get("status", {}).get("phase")
        sc = (pvc.get("spec", {}) or {}).get("storageClassName")
        sc = "" if sc is None else sc
        if name and _force_delete_pvc(ctx, "jupyterhub", name):
            actions.append(f"deleted jupyterhub PVC {name} (phase={phase} sc={sc!r})")
    for pv in list(ctx.items("pv")):
        pname = (pv.get("metadata") or {}).get("name", "")
        claim = (pv.get("spec") or {}).get("claimRef") or {}
        if claim.get("namespace") == "jupyterhub" or "hub-db" in pname:
            phase = pv.get("status", {}).get("phase")
            if ctx.k(["delete", "pv", pname, "--ignore-not-found"]).returncode == 0:
                actions.append(f"deleted hub-related PV {pname} (phase={phase})")
    # Hub Deployment may still reference old volume — recycle hub pods after PVC gone
    ctx.k(["delete", "pod", "-n", "jupyterhub", "-l", "component=hub",
           "--force", "--grace-period=0", "--wait=false", "--ignore-not-found"])
    fix = _zarf_deploy_components(ctx, "jupyterhub")
    if actions:
        return Fix(fix.changed or bool(actions),
                   f"{fix.detail}  [unwound: {'; '.join(actions)}]")
    return fix
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
    if not want.issubset(set(names)):
        return Probe(False, f"ingress missing objects: have {names}, want {want}")
    # Class alignment (traefik-on-RKE2 silent unbound)
    cls_probe = _platform.det_ingress_class_aligned(ctx)
    if not cls_probe.ok:
        return cls_probe
    # Optional: /ws path on panel-viz for terminal
    for ing in ings:
        if (ing.get("metadata") or {}).get("name") != "panel-viz":
            continue
        paths = []
        for rule in (ing.get("spec") or {}).get("rules") or []:
            for p in ((rule.get("http") or {}).get("paths") or []):
                paths.append(p.get("path"))
        if "/ws" not in paths:
            return Probe(False,
                         f"panel-viz ingress missing /ws path (have {paths}) — "
                         "terminal WebSocket will not work through ingress")
    return Probe(True, f"ingress: {names}; {cls_probe.detail}")


def _rem_ingress(ctx: Ctx) -> Fix:
    _platform.ensure_ingress_class_in_ctx(ctx)
    return _zarf_deploy_components(ctx, "ingress")

# APP_NAMESPACES / DASK_CRD_KINDS: imported from discovery (managed-scope SSOT).
# Teardown and remediations mutate only those Layer-B targets; registry hostPath
# data and Layer-A node images are never deleted.


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
        Invariant("T0.system-plane", "T0",
                  "RKE2/system plane healthy (API, DNS, ingress controller observed)",
                  Layer.B, _platform.det_system_plane, _platform.rem_system_plane,
                  depends_on=("T0.api",)),
        Invariant("T0.kubelet-gc", "T0",
                  "Kubelet image-GC policy raised (protects Layer-A images on disk pressure)",
                  Layer.B, _platform.det_kubelet_gc_policy, _platform.rem_kubelet_gc_policy,
                  depends_on=("T0.api",)),
        Invariant("T0.no-disk-pressure", "T0", "No disk-pressure taint (lenient eviction persisted)",
                  Layer.A, _det_no_disk_pressure, depends_on=("T0.api",),
                  manual_hint="free disk SAFELY (never `crictl rmi --prune`; remove only the package "
                              "tarball / journald / Failed pods) and ensure the RKE2 lenient-eviction "
                              "config is applied (infra/aws/ansible/roles/rke2-*/templates)"),
        # Layer-A tools: binary + init package must be on the node (rc=127 / air-gap init).
        Invariant("T0.layer-a-zarf-tools", "T0",
                  "Zarf binary + init package present (Layer A — CLOSURE)",
                  Layer.A, _det_layer_a_zarf_tools, depends_on=("T0.api",),
                  manual_hint="Transport the release's `zarf` binary (v0.70.x) to "
                              "/usr/local/bin and `zarf-init-amd64-v0.70.1.tar.zst` next to "
                              "the deploy package in /var/tmp — never download air-gapped"),
        Invariant("T0.package-uniqueness", "T0",
                  "At most one cybersec-dask deploy package staged (mtime-safe)",
                  Layer.A, _platform.det_package_uniqueness, depends_on=("T0.api",),
                  manual_hint="Remove extra zarf-package-cybersec-dask-amd64-*.tar.zst files; "
                              "keep only the intended version (engine never deletes Layer-A)"),
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
