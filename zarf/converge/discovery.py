"""Cluster-wide discovery and Layer-B vestige sweep.

Design intent (CADS — comprehensive anticipatory):
  * Every ``apply`` pass **always** re-walks entry points on the RKE2 instance:
    nodes → namespaces → controllers → pods → PVC/PV/SC → helm bookkeeping →
    webhooks/poison labels → CR finalizers.
  * Relationships are followed to root cause (mount → PVC → PV; ownerRef chain;
    finalizers blocking deletion; helm pending blocking upgrade).
  * Vestigial **Layer-B** husks are eliminated when they block convergence.
  * **Layer-A** is never destroyed: no image prune, no hostPath registry data wipe,
    no deletion of zarf-init package / zarf binary, no crictl rmi.

The sweep is idempotent and safe to run on a healthy converged cluster (no-ops).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .kube import Ctx

# --------------------------------------------------------------------------- #
# Scope: what we own vs what we must not touch
# --------------------------------------------------------------------------- #

# Workload namespaces the package deploys (disposable Layer-B).
APP_NAMESPACES: Tuple[str, ...] = ("dask", "dask-operator", "jupyterhub", "panel-viz")

# Platform namespace from ``zarf init`` — special-cased (registry hostPath conserved).
PLATFORM_NS = "zarf"

# All namespaces we may mutate contents of (never kube-system / rke2 / cattle-*).
MANAGED_NAMESPACES: Tuple[str, ...] = APP_NAMESPACES + (PLATFORM_NS,)

# Cluster-scoped kinds we may clean when they reference managed namespaces only.
DASK_CRD_KINDS: Tuple[str, ...] = (
    "daskclusters", "daskworkergroups", "daskautoscalers", "daskjobs",
)

# Exhaustive namespaced kinds to drain when emptying a managed ns.
DRAIN_KINDS: Tuple[str, ...] = (
    "deployments", "replicasets", "statefulsets", "daemonsets",
    "jobs", "cronjobs", "horizontalpodautoscalers", "poddisruptionbudgets",
    "services", "endpoints", "endpointslices",
    "ingresses", "networkpolicies",
    "configmaps", "secrets", "serviceaccounts",
    "roles", "rolebindings",
    "persistentvolumeclaims",
    "pods",
)

# Layer-A / foundational — NEVER deleted by sweep (document for auditors).
LAYER_A_PROTECTED = (
    "node containerd images (no crictl rmi/prune)",
    "hostPath /var/lib/zarf-registry data (Retain PV object ok to recreate)",
    "zarf binary + zarf-init-*.tar.zst + deploy package on disk",
    "RKE2 system namespaces (kube-system, kube-public, …)",
)

_IMAGE_WAIT_BAD = ("ImagePullBackOff", "ErrImagePull", "ErrImageNeverPull")
_HELM_PENDING = ("pending-install", "pending-upgrade", "pending-rollback")
_POD_JUNK_PHASES = ("Failed", "Evicted", "Unknown")


@dataclass
class Finding:
    """One discovery observation — optional relationship chain to root cause."""

    severity: str          # info | warn | wedge
    entry: str             # entry point (ns, pvc, pod, …)
    summary: str
    root: str = ""         # root condition if walked
    chain: List[str] = field(default_factory=list)


@dataclass
class DiscoveryReport:
    findings: List[Finding] = field(default_factory=list)
    managed_ns: Dict[str, str] = field(default_factory=dict)  # name → phase
    actions_preview: List[str] = field(default_factory=list)

    def add(self, severity: str, entry: str, summary: str,
            root: str = "", chain: Optional[List[str]] = None) -> None:
        self.findings.append(Finding(severity, entry, summary, root, chain or []))

    @property
    def wedges(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "wedge"]

    def format(self) -> str:
        lines = ["  ── DISCOVERY (entry points → relationships → root) ──"]
        if self.managed_ns:
            ns_s = ", ".join(f"{n}={p}" for n, p in sorted(self.managed_ns.items()))
            lines.append(f"  managed namespaces: {ns_s or '(none present)'}")
        if not self.findings:
            lines.append("  (no wedges or notable drift)")
        for f in self.findings:
            mark = {"info": "·", "warn": "!", "wedge": "✖"}.get(f.severity, "·")
            lines.append(f"  {mark} [{f.entry}] {f.summary}")
            if f.root:
                lines.append(f"      root: {f.root}")
            for step in f.chain:
                lines.append(f"      → {step}")
        if self.actions_preview:
            lines.append("  would remediate:")
            for a in self.actions_preview[:20]:
                lines.append(f"    • {a}")
            if len(self.actions_preview) > 20:
                lines.append(f"    … +{len(self.actions_preview) - 20} more")
        lines.append("  ── end discovery ──")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Relationship walkers
# --------------------------------------------------------------------------- #

def _pvc_chain(ctx: Ctx, ns: str, pvc_name: str) -> Tuple[str, List[str]]:
    """Walk PVC → PV → SC → pods mounting → root cause string."""
    chain: List[str] = []
    pvc = ctx.get("pvc", pvc_name, ns=ns)
    if not pvc:
        return "PVC absent", chain
    phase = pvc.get("status", {}).get("phase", "?")
    sc = (pvc.get("spec") or {}).get("storageClassName")
    sc = "" if sc is None else sc
    vol = (pvc.get("spec") or {}).get("volumeName") or ""
    del_ts = (pvc.get("metadata") or {}).get("deletionTimestamp")
    finals = (pvc.get("metadata") or {}).get("finalizers") or []
    chain.append(f"PVC {ns}/{pvc_name} phase={phase} sc={sc!r} vol={vol!r} "
                 f"deleting={bool(del_ts)} finals={finals}")
    if vol:
        pv = ctx.get("pv", vol)
        if pv:
            pvp = pv.get("status", {}).get("phase", "?")
            pvsc = (pv.get("spec") or {}).get("storageClassName") or ""
            chain.append(f"PV {vol} phase={pvp} sc={pvsc!r}")
        else:
            chain.append(f"PV {vol} MISSING (claim points at gone volume)")
    if sc:
        sc_obj = ctx.get("storageclass", sc)
        if not sc_obj:
            chain.append(f"StorageClass {sc!r} ABSENT — dynamic provision impossible air-gap")
            return "missing StorageClass for dynamic PVC", chain
        prov = (sc_obj.get("provisioner") or "")
        chain.append(f"StorageClass {sc} provisioner={prov}")
    holders = []
    for p in ctx.items("pods", ns=ns):
        for v in p.get("spec", {}).get("volumes") or []:
            if (v.get("persistentVolumeClaim") or {}).get("claimName") == pvc_name:
                holders.append(p["metadata"]["name"])
    if holders:
        chain.append(f"mounted by pods: {holders}")
    if del_ts or phase == "Terminating":
        root = "PVC Terminating — release mounts then strip finalizers"
    elif phase == "Pending" and sc not in ("", None):
        root = f"PVC Pending on sc={sc!r} — provisioner dead or SC capture"
    elif phase == "Pending":
        root = "PVC Pending on empty class — need static PV claimRef bind"
    elif phase == "Bound":
        root = "PVC Bound (storage OK at this layer)"
    else:
        root = f"PVC phase={phase}"
    return root, chain


def _pod_chain(ctx: Ctx, ns: str, pod: dict) -> Tuple[str, List[str]]:
    """Walk pod → ownerRef → images/waiting reasons → volumes."""
    chain: List[str] = []
    name = pod.get("metadata", {}).get("name", "?")
    phase = pod.get("status", {}).get("phase", "?")
    chain.append(f"Pod {ns}/{name} phase={phase}")
    owners = (pod.get("metadata") or {}).get("ownerReferences") or []
    for o in owners:
        chain.append(f"owner {o.get('kind')}/{o.get('name')} controller={o.get('controller')}")
    for cs in (pod.get("status", {}).get("containerStatuses") or []):
        waiting = (cs.get("state") or {}).get("waiting") or {}
        if waiting:
            chain.append(f"container {cs.get('name')}: waiting {waiting.get('reason')} "
                         f"{(waiting.get('message') or '')[:80]}")
            if waiting.get("reason") in _IMAGE_WAIT_BAD:
                img = ""
                for c in pod.get("spec", {}).get("containers") or []:
                    if c.get("name") == cs.get("name"):
                        img = c.get("image", "")
                if img.startswith("127.0.0.1:"):
                    return "ImagePull on internal registry — content missing (T2)", chain
                return "ImagePull on upstream ref — agent rewrite/poison (T1)", chain
    for v in pod.get("spec", {}).get("volumes") or []:
        claim = (v.get("persistentVolumeClaim") or {}).get("claimName")
        if claim:
            root, sub = _pvc_chain(ctx, ns, claim)
            chain.extend(sub)
            if "Pending" in root or "Terminating" in root or "missing" in root:
                return root, chain
    if phase == "Pending":
        return "Pod Pending — scheduling/mount/capacity", chain
    if phase in _POD_JUNK_PHASES:
        return f"Pod junk phase={phase} — delete Layer-B", chain
    return f"Pod phase={phase}", chain


# --------------------------------------------------------------------------- #
# Discovery (read-only)
# --------------------------------------------------------------------------- #

def discover(ctx: Ctx) -> DiscoveryReport:
    """Full entry-point walk of managed scope + related cluster objects."""
    rep = DiscoveryReport()

    # --- nodes ---
    for n in ctx.items("nodes"):
        name = n.get("metadata", {}).get("name", "?")
        conds = {c["type"]: c["status"] for c in n.get("status", {}).get("conditions", [])}
        ready = conds.get("Ready") == "True"
        taints = n.get("spec", {}).get("taints") or []
        pressure = [t.get("key") for t in taints
                    if "pressure" in (t.get("key") or "") or t.get("key") == "node.kubernetes.io/disk-pressure"]
        if not ready:
            rep.add("wedge", f"node/{name}", "NotReady", root="node not Ready")
        elif pressure:
            rep.add("wedge", f"node/{name}", f"pressure taints={pressure}",
                    root="disk/memory pressure — free disk SAFELY (no image prune)")
        else:
            rep.add("info", f"node/{name}", "Ready")

    # --- managed namespaces ---
    for ns in MANAGED_NAMESPACES:
        obj = ctx.get("namespace", ns)
        if not obj:
            rep.managed_ns[ns] = "Absent"
            continue
        phase = obj.get("status", {}).get("phase", "?")
        rep.managed_ns[ns] = phase
        labels = (obj.get("metadata") or {}).get("labels") or {}
        if labels.get("zarf.dev/agent") in ("ignore", "skip") and ns in APP_NAMESPACES:
            rep.add("wedge", f"ns/{ns}", "labeled zarf.dev/agent=ignore",
                    root="re-init poison — image rewrite disabled for this ns")
        if phase == "Terminating":
            rep.add("wedge", f"ns/{ns}", "Terminating",
                    root="drain contents then force-finalize when empty")

    # --- husk / controller signatures per managed ns ---
    for ns in MANAGED_NAMESPACES:
        if rep.managed_ns.get(ns) in (None, "Absent"):
            continue
        if rep.managed_ns.get(ns) == "Terminating":
            continue
        deps = ctx.items("deployments", ns=ns)
        sts = ctx.items("statefulsets", ns=ns)
        pods = ctx.items("pods", ns=ns)
        svcs = ctx.items("services", ns=ns)
        ready_pods = sum(
            1 for p in pods
            if {c["type"]: c["status"] for c in p.get("status", {}).get("conditions", [])
                }.get("Ready") == "True")
        # Deployments/STS with replicas desired but no pods
        for d in deps:
            des = d.get("spec", {}).get("replicas")
            if des is None:
                des = 1
            name = d.get("metadata", {}).get("name", "?")
            avail = d.get("status", {}).get("availableReplicas") or 0
            if des and des > 0 and not pods:
                rep.add("wedge", f"deploy/{ns}/{name}",
                        f"desired={des} available={avail} but ZERO pods in ns",
                        root="husk Deployment (force-finalize resurrection) — delete ns or drain")
            elif des and des > 0 and avail == 0 and ready_pods == 0:
                rep.add("warn", f"deploy/{ns}/{name}",
                        f"desired={des} available=0 ready_pods={ready_pods}",
                        root="controller not producing Ready pods — check events/images/PVC")
        for s in sts:
            des = s.get("spec", {}).get("replicas") or 1
            name = s.get("metadata", {}).get("name", "?")
            ready = s.get("status", {}).get("readyReplicas") or 0
            if des and not pods:
                rep.add("wedge", f"sts/{ns}/{name}",
                        f"desired={des} ready={ready} ZERO pods",
                        root="husk StatefulSet — drain/recreate")
        # Service-only leftovers (classic zarf-injector husk)
        if svcs and not deps and not sts and ready_pods == 0:
            names = [x.get("metadata", {}).get("name") for x in svcs]
            rep.add("wedge", f"ns/{ns}",
                    f"Service-only husk (no controllers, no Ready pods): {names}",
                    root="etcd leftovers after partial destroy — drain ns")

    # --- PVCs in managed ns ---
    for ns in MANAGED_NAMESPACES:
        if rep.managed_ns.get(ns) == "Absent":
            continue
        for pvc in ctx.items("pvc", ns=ns):
            name = pvc.get("metadata", {}).get("name", "?")
            phase = pvc.get("status", {}).get("phase", "?")
            del_ts = (pvc.get("metadata") or {}).get("deletionTimestamp")
            if del_ts or phase in ("Pending", "Terminating", "Lost"):
                root, chain = _pvc_chain(ctx, ns, name)
                rep.add("wedge", f"pvc/{ns}/{name}", f"phase={phase}", root=root, chain=chain)

    # --- orphan / Released PVs claiming managed namespaces ---
    for pv in ctx.items("pv"):
        pname = (pv.get("metadata") or {}).get("name", "?")
        phase = pv.get("status", {}).get("phase", "?")
        claim = (pv.get("spec") or {}).get("claimRef") or {}
        cns = claim.get("namespace") or ""
        # Protect registry hostPath PV object reset is handled in catalog; here only flag.
        if cns in MANAGED_NAMESPACES or "hub-db" in pname or "zarf-registry" in pname:
            if phase in ("Released", "Failed"):
                rep.add("wedge", f"pv/{pname}", f"phase={phase} claim={cns}/{claim.get('name')}",
                        root="Released/Failed PV — reset claimRef or delete object "
                             "(hostPath data conserved if Retain)")
            elif phase == "Available" and cns in APP_NAMESPACES:
                rep.add("warn", f"pv/{pname}", f"Available with stale claimRef to {cns}",
                        root="may block rebinding — clear or re-apply claimRef")

    # --- helm pending ---
    obj = ctx.kjson(["get", "secrets", "-A", "-l", "owner=helm"]) or {}
    latest: dict = {}
    for s in obj.get("items", []):
        md = s.get("metadata", {}) or {}
        lab = md.get("labels", {}) or {}
        try:
            ver = int(lab.get("version", 0))
        except (TypeError, ValueError):
            continue
        key = (md.get("namespace"), lab.get("name"))
        if key not in latest or ver > latest[key][0]:
            latest[key] = (ver, md.get("name"), lab.get("status"), md.get("namespace"))
    for (ns, rel), (ver, name, status, _) in latest.items():
        if status in _HELM_PENDING:
            rep.add("wedge", f"helm/{ns}/{rel}",
                    f"latest rev {ver} status={status}",
                    root="delete pending secret so next deploy can proceed")

    # --- junk / ImagePull pods (relationship walk sample) ---
    for ns in MANAGED_NAMESPACES:
        if rep.managed_ns.get(ns) == "Absent":
            continue
        for p in ctx.items("pods", ns=ns):
            phase = p.get("status", {}).get("phase", "")
            name = p.get("metadata", {}).get("name", "?")
            stuck_pull = any(
                ((cs.get("state") or {}).get("waiting") or {}).get("reason") in _IMAGE_WAIT_BAD
                for cs in (p.get("status", {}).get("containerStatuses") or []))
            if phase in _POD_JUNK_PHASES or stuck_pull or phase == "Pending":
                root, chain = _pod_chain(ctx, ns, p)
                sev = "wedge" if stuck_pull or phase in _POD_JUNK_PHASES else "warn"
                rep.add(sev, f"pod/{ns}/{name}", f"phase={phase}", root=root, chain=chain)

    # --- VolumeAttachments (node-level mounts left after pod death) ---
    for va in ctx.items("volumeattachments"):
        md = va.get("metadata") or {}
        if md.get("deletionTimestamp"):
            rep.add("wedge", f"volumeattachment/{md.get('name')}",
                    "Terminating",
                    root="stuck VolumeAttachment — may hold PVC protection finalizer")

    # --- Dask CRs with finalizers when operator may be dead ---
    for kind in DASK_CRD_KINDS:
        for it in ctx.items(kind):
            md = it.get("metadata") or {}
            if md.get("deletionTimestamp") or md.get("finalizers"):
                if md.get("deletionTimestamp"):
                    rep.add("wedge", f"{kind}/{md.get('namespace')}/{md.get('name')}",
                            f"finalizers={md.get('finalizers')}",
                            root="CR stuck deleting — strip finalizers (operator may be gone)")

    return rep


# --------------------------------------------------------------------------- #
# Sweep (Layer-B only, idempotent)
# --------------------------------------------------------------------------- #

def _strip_finalizers_obj(ctx: Ctx, kind: str, name: str, ns: Optional[str] = None) -> bool:
    args = ["patch", kind, name, "--type=merge", "-p", '{"metadata":{"finalizers":null}}']
    if ns:
        args[1:1] = []  # no-op keep structure
        args = ["patch", kind, name, "-n", ns, "--type=merge",
                "-p", '{"metadata":{"finalizers":null}}']
    return ctx.k(args).returncode == 0


def _force_delete_pvc_local(ctx: Ctx, ns: str, name: str) -> bool:
    """Mount-aware PVC delete (mirrors catalog; kept local to avoid import cycles)."""
    # release mounts
    for kind in ("deploy", "sts", "ds", "job"):
        ctx.k(["delete", kind, "--all", "-n", ns, "--wait=false", "--ignore-not-found"])
    for p in ctx.items("pods", ns=ns):
        for v in p.get("spec", {}).get("volumes") or []:
            if (v.get("persistentVolumeClaim") or {}).get("claimName") == name:
                ctx.k(["delete", "pod", p["metadata"]["name"], "-n", ns,
                       "--force", "--grace-period=0", "--wait=false", "--ignore-not-found"])
    ctx.k(["delete", "pvc", name, "-n", ns, "--ignore-not-found", "--wait=false"])
    if ctx.get("pvc", name, ns=ns):
        ctx.k(["patch", "pvc", name, "-n", ns, "--type=merge",
               "-p", '{"metadata":{"finalizers":null}}'])
    if ctx.get("pvc", name, ns=ns):
        ctx.k(["patch", "pvc", name, "-n", ns, "--type=json",
               "-p", '[{"op":"remove","path":"/metadata/finalizers"}]'])
    return ctx.get("pvc", name, ns=ns) is None


def _force_finalize_ns(ctx: Ctx, ns: str) -> bool:
    obj = ctx.get("namespace", ns)
    if not obj:
        return False
    obj["spec"] = {"finalizers": []}
    r = ctx.run(ctx.kubectl + ["replace", "--raw",
                f"/api/v1/namespaces/{ns}/finalize", "-f", "-"],
                input_=json.dumps(obj))
    return r.returncode == 0


def _drain_ns(ctx: Ctx, ns: str) -> List[str]:
    """Empty managed namespace contents (not the ns object). Layer-B only."""
    if not ctx.exists("namespace", ns):
        return []
    actions: List[str] = []
    # Controllers first
    for kind in ("deployments", "replicasets", "statefulsets", "daemonsets",
                 "jobs", "cronjobs", "horizontalpodautoscalers"):
        ctx.k(["delete", kind, "--all", "-n", ns, "--wait=false", "--ignore-not-found"])
    ctx.k(["delete", "pods", "--all", "-n", ns,
           "--force", "--grace-period=0", "--wait=false", "--ignore-not-found"])
    for kind in ("services", "endpoints", "endpointslices", "ingresses", "networkpolicies",
                 "configmaps", "secrets", "roles", "rolebindings", "serviceaccounts",
                 "poddisruptionbudgets"):
        ctx.k(["delete", kind, "--all", "-n", ns, "--wait=false", "--ignore-not-found"])
    for pvc in list(ctx.items("pvc", ns=ns)):
        name = pvc.get("metadata", {}).get("name")
        if name and _force_delete_pvc_local(ctx, ns, name):
            actions.append(f"drained PVC {ns}/{name}")
    # finalizer strip on stragglers
    for kind in DRAIN_KINDS:
        for it in ctx.items(kind, ns=ns):
            if (it.get("metadata") or {}).get("finalizers"):
                n = it["metadata"]["name"]
                if _strip_finalizers_obj(ctx, kind, n, ns=ns):
                    actions.append(f"stripped finalizers {kind}/{ns}/{n}")
    actions.append(f"drained ns {ns}")
    return actions


def _registry_ready(ctx: Ctx) -> bool:
    for sel in ("app=docker-registry", "app.kubernetes.io/name=zarf-docker-registry"):
        ready, _ = ctx.pods_ready(PLATFORM_NS, sel)
        if ready >= 1:
            return True
    return False


def _is_app_husk(ctx: Ctx, ns: str) -> Optional[str]:
    """Return reason if Active app ns should be torn down as a husk."""
    deps = ctx.items("deployments", ns=ns)
    sts = ctx.items("statefulsets", ns=ns)
    pods = ctx.items("pods", ns=ns)
    svcs = ctx.items("services", ns=ns)
    if (deps or sts) and not pods:
        return "controllers present, zero pods (resurrection husk)"
    if svcs and not deps and not sts and not pods:
        return "Service-only husk (no controllers/pods)"
    # Deployments desired>0, zero available, all pods non-Ready for extended junk
    for d in deps:
        des = d.get("spec", {}).get("replicas")
        if des is None:
            des = 1
        avail = d.get("status", {}).get("availableReplicas") or 0
        if des > 0 and avail == 0 and pods:
            # only husk if every pod is Failed/Evicted or ImagePull with upstream
            all_junk = True
            for p in pods:
                phase = p.get("status", {}).get("phase")
                stuck = any(
                    ((cs.get("state") or {}).get("waiting") or {}).get("reason") in _IMAGE_WAIT_BAD
                    for cs in (p.get("status", {}).get("containerStatuses") or []))
                imgs = [c.get("image", "") for c in p.get("spec", {}).get("containers") or []]
                upstream = any(i and not i.startswith("127.0.0.1:") for i in imgs)
                if phase not in _POD_JUNK_PHASES and not (stuck and upstream):
                    all_junk = False
                    break
            if all_junk and pods:
                return "all pods junk/unmutated ImagePull — recycle ns"
    return None


def _is_zarf_husk(ctx: Ctx) -> Optional[str]:
    if not ctx.exists("namespace", PLATFORM_NS):
        return None
    ns = ctx.get("namespace", PLATFORM_NS)
    if ns and ns.get("status", {}).get("phase") == "Terminating":
        return "zarf ns Terminating"
    if _registry_ready(ctx):
        return None
    pvc = ctx.get("pvc", "zarf-docker-registry", ns=PLATFORM_NS)
    if pvc:
        phase = pvc.get("status", {}).get("phase")
        sc = (pvc.get("spec") or {}).get("storageClassName")
        sc = "" if sc is None else sc
        del_ts = (pvc.get("metadata") or {}).get("deletionTimestamp")
        if del_ts or phase == "Terminating":
            return "registry PVC Terminating"
        if phase == "Bound" and sc == "":
            return None  # in-progress healthy bind
        if phase == "Pending" or sc != "":
            return f"registry PVC phase={phase} sc={sc!r}"
    svcs = ctx.items("services", ns=PLATFORM_NS)
    deps = ctx.items("deployments", ns=PLATFORM_NS)
    pods = ctx.items("pods", ns=PLATFORM_NS)
    if svcs or deps or pods or pvc:
        return "zarf partial/husk without Ready registry"
    return None


def sweep_vestiges(ctx: Ctx, *, dry_run: bool = False) -> List[str]:
    """Eliminate Layer-B vestiges that block convergence. Never touches Layer-A.

    Safe on a healthy cluster: if registry Ready and app pods Ready, actions ≈ [].
    """
    actions: List[str] = []

    def act(msg: str, fn=None) -> None:
        if dry_run:
            actions.append(f"(dry-run) {msg}")
            return
        if fn:
            fn()
        actions.append(msg)

    # 1. Helm pending (all namespaces — bookkeeping only)
    obj = ctx.kjson(["get", "secrets", "-A", "-l", "owner=helm"]) or {}
    latest: dict = {}
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
    for (ns, rel), (ver, name, status) in latest.items():
        if status in _HELM_PENDING and ns and name:
            if dry_run:
                actions.append(f"(dry-run) delete pending helm {ns}/{name} ({rel} v{ver})")
            elif ctx.k(["delete", "secret", name, "-n", ns]).returncode == 0:
                actions.append(f"unwedged pending Helm {ns}/{rel} v{ver} ({status})")

    # 2. Agent poison on app namespaces
    for ns in APP_NAMESPACES:
        obj = ctx.get("namespace", ns)
        if not obj:
            continue
        labels = (obj.get("metadata") or {}).get("labels") or {}
        if labels.get("zarf.dev/agent") in ("ignore", "skip"):
            if dry_run:
                actions.append(f"(dry-run) strip zarf.dev/agent=ignore from {ns}")
            elif ctx.k(["label", "namespace", ns, "zarf.dev/agent-", "--overwrite"]).returncode == 0:
                actions.append(f"stripped zarf.dev/agent=ignore from {ns}")

    # 3. Per managed namespace
    for ns in MANAGED_NAMESPACES:
        nobj = ctx.get("namespace", ns)
        if not nobj:
            continue
        phase = nobj.get("status", {}).get("phase")

        if phase == "Terminating":
            if dry_run:
                actions.append(f"(dry-run) drain+finalize Terminating ns {ns}")
                continue
            actions.extend(_drain_ns(ctx, ns))
            leftovers = any(ctx.items(k, ns=ns) for k in ("deployments", "pods", "pvc"))
            if not leftovers and _force_finalize_ns(ctx, ns):
                actions.append(f"force-finalized Terminating ns {ns}")
            continue

        # Terminating PVCs always
        for pvc in list(ctx.items("pvc", ns=ns)):
            name = pvc.get("metadata", {}).get("name")
            pphase = pvc.get("status", {}).get("phase")
            del_ts = (pvc.get("metadata") or {}).get("deletionTimestamp")
            sc = (pvc.get("spec") or {}).get("storageClassName")
            sc = "" if sc is None else sc
            # Do not delete healthy Bound registry PVC while registry Ready
            if (ns == PLATFORM_NS and name == "zarf-docker-registry"
                    and pphase == "Bound" and sc == "" and _registry_ready(ctx)):
                continue
            bad = bool(del_ts) or pphase in ("Terminating", "Lost")
            # Pending dynamic class in app ns — orphan path
            if ns in APP_NAMESPACES and pphase == "Pending" and sc not in ("",):
                bad = True
            if ns == PLATFORM_NS and (pphase == "Pending" or (sc != "" and pphase != "Bound")):
                bad = True
            if bad and name:
                if dry_run:
                    actions.append(f"(dry-run) force-delete PVC {ns}/{name} phase={pphase}")
                elif _force_delete_pvc_local(ctx, ns, name):
                    actions.append(f"force-deleted PVC {ns}/{name} (phase={pphase} sc={sc!r})")

        # Junk pods
        for p in list(ctx.items("pods", ns=ns)):
            name = p.get("metadata", {}).get("name")
            pphase = p.get("status", {}).get("phase")
            stuck = any(
                ((cs.get("state") or {}).get("waiting") or {}).get("reason") in _IMAGE_WAIT_BAD
                for cs in (p.get("status", {}).get("containerStatuses") or []))
            imgs = [c.get("image", "") for c in p.get("spec", {}).get("containers") or []]
            upstream_stuck = stuck and any(i and not i.startswith("127.0.0.1:") for i in imgs)
            if pphase in _POD_JUNK_PHASES or upstream_stuck:
                if dry_run:
                    actions.append(f"(dry-run) delete junk pod {ns}/{name} phase={pphase}")
                elif name and ctx.k(["delete", "pod", name, "-n", ns,
                                     "--force", "--grace-period=0",
                                     "--wait=false"]).returncode == 0:
                    actions.append(f"deleted junk pod {ns}/{name} phase={pphase}")

        # Husk namespace recycle (app)
        if ns in APP_NAMESPACES:
            reason = _is_app_husk(ctx, ns)
            if reason:
                if dry_run:
                    actions.append(f"(dry-run) recycle husk ns {ns}: {reason}")
                else:
                    actions.extend(_drain_ns(ctx, ns))
                    ctx.k(["delete", "namespace", ns, "--wait=false"])
                    actions.append(f"deleted husk ns {ns} ({reason})")

        # Zarf husk (platform) — drain but do NOT delete hostPath data
        if ns == PLATFORM_NS:
            reason = _is_zarf_husk(ctx)
            if reason and "Bound" not in (reason or ""):
                if dry_run:
                    actions.append(f"(dry-run) drain zarf husk: {reason}")
                else:
                    actions.extend(_drain_ns(ctx, ns))
                    actions.append(f"drained zarf husk ({reason})")

    # 4. Orphan PVs for app claims (never wipe zarf-registry hostPath *data*;
    #    deleting the PV *object* with Retain is OK and recreated by catalog)
    for pv in list(ctx.items("pv")):
        pname = (pv.get("metadata") or {}).get("name", "")
        phase = pv.get("status", {}).get("phase", "")
        claim = (pv.get("spec") or {}).get("claimRef") or {}
        cns = claim.get("namespace") or ""
        if phase not in ("Released", "Failed"):
            continue
        if cns in APP_NAMESPACES or "hub-db" in pname:
            if dry_run:
                actions.append(f"(dry-run) delete orphan PV {pname} phase={phase}")
            elif ctx.k(["delete", "pv", pname, "--ignore-not-found"]).returncode == 0:
                actions.append(f"deleted orphan PV {pname} (phase={phase})")
        # zarf-registry-pv Released → leave for catalog reset (re-apply claimRef)

    # 5. Stuck VolumeAttachments
    for va in list(ctx.items("volumeattachments")):
        md = va.get("metadata") or {}
        if not md.get("deletionTimestamp"):
            continue
        name = md.get("name")
        if dry_run:
            actions.append(f"(dry-run) strip finalizers volumeattachment/{name}")
        elif name and _strip_finalizers_obj(ctx, "volumeattachment", name):
            actions.append(f"stripped finalizers volumeattachment/{name}")

    # 6. Dask CRs stuck deleting
    for kind in DASK_CRD_KINDS:
        for it in list(ctx.items(kind)):
            md = it.get("metadata") or {}
            if not md.get("deletionTimestamp"):
                continue
            name, ns = md.get("name"), md.get("namespace")
            if not name:
                continue
            if dry_run:
                actions.append(f"(dry-run) strip finalizers {kind}/{ns}/{name}")
                continue
            if ns:
                rc = ctx.k(["patch", kind, name, "-n", ns, "--type=merge",
                            "-p", '{"metadata":{"finalizers":null}}']).returncode
            else:
                rc = ctx.k(["patch", kind, name, "--type=merge",
                            "-p", '{"metadata":{"finalizers":null}}']).returncode
            if rc == 0:
                actions.append(f"stripped finalizers {kind}/{ns}/{name}")

    return actions


def print_discovery(ctx: Ctx, *, preview_sweep: bool = False) -> DiscoveryReport:
    """Run discovery, optionally attach dry-run sweep preview, print, return report."""
    rep = discover(ctx)
    if preview_sweep:
        rep.actions_preview = sweep_vestiges(ctx, dry_run=True)
    print(rep.format())
    return rep
