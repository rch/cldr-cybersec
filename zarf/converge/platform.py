"""Node/platform anticipatory edges for air-gap RKE2 convergence.

Covers residual edges that live *outside* pure K8s object state but determine
whether a closed-world deploy can complete or stay complete:

  * kubelet image-GC policy (default 85% silently deletes Layer-A images)
  * RKE2 / system-plane health (read-only discovery + limited safe remediations)
  * IngressClass selection (nginx on RKE2 vs traefik on K3s)
  * Layer-A package uniqueness (multiple deploy tarballs → wrong mtime pick)
  * HostPath writability for registry (shared with catalog hostPath prep)

CONSERVATION: never prunes container images; never deletes package tarballs
except when the operator explicitly stages a single version. Kubelet policy
remediation only *raises* GC thresholds (more conservative), never lowers them.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from .kube import Ctx
from .model import Fix, Probe

# --------------------------------------------------------------------------- #
# Paths / constants
# --------------------------------------------------------------------------- #

RKE2_CONFIG = Path("/etc/rancher/rke2/config.yaml")
REGISTRY_HOSTPATH = Path("/var/lib/zarf-registry")
DEFAULT_PKG_DIRS = (Path("/var/tmp"), Path("/opt/zarf"), Path.cwd())

# Kubelet args that keep transported images alive on small disks.
_KUBELET_GC_ARGS = (
    'eviction-hard=imagefs.available<2%,nodefs.available<2%,nodefs.inodesFree<2%,memory.available<100Mi',
    'eviction-minimum-reclaim=imagefs.available=1%,nodefs.available=1%',
    'image-gc-high-threshold=100',
    'image-gc-low-threshold=99',
)

# System namespaces we observe but never mutate (except never).
SYSTEM_NS_OBSERVE = (
    "kube-system",
    "kube-public",
    "kube-node-lease",
    "ingress-nginx",
    "cattle-system",
)


# --------------------------------------------------------------------------- #
# Kubelet image-GC policy
# --------------------------------------------------------------------------- #

def _read_rke2_config() -> str:
    try:
        return RKE2_CONFIG.read_text() if RKE2_CONFIG.is_file() else ""
    except OSError:
        return ""


def kubelet_gc_policy_ok(text: Optional[str] = None) -> bool:
    """True if image-gc-high-threshold is raised to disable aggressive GC."""
    raw = text if text is not None else _read_rke2_config()
    if not raw:
        return False
    # Accept high-threshold=100 (or >=99) as our target policy.
    m = re.search(r"image-gc-high-threshold[=:](\d+)", raw)
    if not m:
        return False
    try:
        return int(m.group(1)) >= 99
    except ValueError:
        return False


def det_kubelet_gc_policy(_ctx: Ctx) -> Probe:
    """Layer-B node policy: default kubelet GC at 85% deletes Layer-A images."""
    if not RKE2_CONFIG.parent.is_dir() and not Path("/etc/rancher").is_dir():
        # Not an RKE2 node (e.g. operator laptop dry-run) — not applicable.
        return Probe(True, "not an RKE2 host path layout — kubelet GC N/A")
    raw = _read_rke2_config()
    if kubelet_gc_policy_ok(raw):
        return Probe(True, f"image-gc-high-threshold raised in {RKE2_CONFIG}")
    if not raw:
        return Probe(False,
                     f"{RKE2_CONFIG} missing/empty — default kubelet GC at ~85% will "
                     "prune transported images under disk pressure")
    return Probe(False,
                 f"{RKE2_CONFIG} lacks image-gc-high-threshold=100 — Layer-A images "
                 "at risk of silent GC under disk pressure")


def rem_kubelet_gc_policy(_ctx: Ctx) -> Fix:
    """Merge lenient kubelet-arg into RKE2 config. Restarts rke2-server when root
    (does not disrupt running pods — containerd keeps them). MANUAL if not root."""
    if os.geteuid() != 0:
        return Fix(False,
                   f"MANUAL: as root, append kubelet-arg GC block to {RKE2_CONFIG} "
                   "and `systemctl restart rke2-server` (see AIRGAP-CHEATSHEET §1)")
    try:
        RKE2_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        raw = _read_rke2_config()
        if kubelet_gc_policy_ok(raw):
            return Fix(False, "kubelet GC policy already applied")
        # If kubelet-arg already exists, append missing entries carefully.
        block_lines = ["kubelet-arg:"]
        for a in _KUBELET_GC_ARGS:
            block_lines.append(f'  - "{a}"')
        block = "\n".join(block_lines) + "\n"
        if "kubelet-arg:" in raw:
            # Merge: ensure each arg line is present under existing list.
            missing = [a for a in _KUBELET_GC_ARGS if a.split("=")[0] not in raw]
            if not missing:
                return Fix(False, "kubelet-arg present; GC keys already covered")
            add = "".join(f'  - "{a}"\n' for a in missing)
            # Insert after first kubelet-arg: line
            new = re.sub(
                r"(kubelet-arg:\s*\n)",
                r"\1" + add,
                raw,
                count=1,
            )
            if new == raw:
                # Fallback append full block (last-key-wins risk documented)
                new = raw.rstrip() + "\n" + block
            RKE2_CONFIG.write_text(new)
            detail = f"merged {len(missing)} kubelet-arg GC entries into {RKE2_CONFIG}"
        else:
            with RKE2_CONFIG.open("a") as f:
                if raw and not raw.endswith("\n"):
                    f.write("\n")
                f.write(block)
            detail = f"appended kubelet-arg GC block to {RKE2_CONFIG}"
        # Restart rke2-server so kubelet picks up args (pods keep running).
        r = subprocess.run(
            ["systemctl", "restart", "rke2-server"],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            return Fix(True, f"{detail}; rke2-server restart rc={r.returncode} "
                             f"(policy on disk — restart manually if needed)")
        return Fix(True, f"{detail}; restarted rke2-server")
    except OSError as e:
        return Fix(False, f"MANUAL: cannot write {RKE2_CONFIG}: {e}")
    except subprocess.TimeoutExpired:
        return Fix(True, f"wrote GC policy; rke2-server restart still running")


# --------------------------------------------------------------------------- #
# IngressClass selection
# --------------------------------------------------------------------------- #

def detect_ingress_class(ctx: Ctx) -> str:
    """Prefer nginx (RKE2), then any non-empty IngressClass name, else nginx default."""
    classes = []
    for ic in ctx.items("ingressclass"):
        name = (ic.get("metadata") or {}).get("name")
        if name:
            classes.append(name)
    if "nginx" in classes:
        return "nginx"
    if "rke2-ingress-nginx" in classes:
        return "rke2-ingress-nginx"
    # Some installs use controller without IngressClass object — probe controller pods
    for ns, sel in (
        ("kube-system", "app.kubernetes.io/name=rke2-ingress-nginx"),
        ("kube-system", "app=rke2-ingress-nginx"),
        ("ingress-nginx", "app.kubernetes.io/name=ingress-nginx"),
        ("kube-system", "app.kubernetes.io/name=traefik"),
    ):
        ready, total = ctx.pods_ready(ns, sel)
        if total > 0:
            if "traefik" in sel:
                return "traefik"
            return "nginx"
    if classes:
        return classes[0]
    return "nginx"  # RKE2 resilient default even if class object not listed yet


def det_ingress_class_aligned(ctx: Ctx) -> Probe:
    """Managed Ingress objects should use a class the cluster actually serves."""
    want = detect_ingress_class(ctx)
    ings = ctx.items("ingress")
    if not ings:
        return Probe(True, f"no Ingress objects yet; preferred class={want}")
    bad = []
    for ing in ings:
        ns = (ing.get("metadata") or {}).get("namespace", "")
        name = (ing.get("metadata") or {}).get("name", "")
        if ns not in ("dask", "jupyterhub", "panel-viz"):
            continue
        cls = (ing.get("spec") or {}).get("ingressClassName") or ""
        if cls and cls != want and want == "nginx" and cls == "traefik":
            bad.append(f"{ns}/{name} class={cls!r}")
        elif cls and cls not in (want, "") and want != cls:
            # only flag traefik-on-rke2 classic misconfig strongly
            if cls == "traefik" and want == "nginx":
                bad.append(f"{ns}/{name} class={cls!r}")
    if bad:
        return Probe(False,
                     f"Ingress bound to wrong class (want {want}): {bad} — "
                     "RKE2 ships nginx; traefik default silently unbound all routes")
    return Probe(True, f"ingress class aligned (preferred={want})")


def rem_ingress_class_aligned(ctx: Ctx) -> Fix:
    """Redeploy ingress component with auto-detected INGRESS_CLASS."""
    cls = detect_ingress_class(ctx)
    ctx.s3["INGRESS_CLASS"] = cls
    from .catalog import _zarf_deploy_components
    fix = _zarf_deploy_components(ctx, "ingress")
    return Fix(fix.changed, f"INGRESS_CLASS={cls}; {fix.detail}")


# --------------------------------------------------------------------------- #
# Layer-A package uniqueness
# --------------------------------------------------------------------------- #

def find_deploy_packages() -> List[Path]:
    found: List[Path] = []
    for d in DEFAULT_PKG_DIRS:
        try:
            found.extend(sorted(d.glob("zarf-package-cybersec-dask-amd64-*.tar.zst")))
        except OSError:
            continue
    # de-dupe
    seen = set()
    out = []
    for p in found:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


def det_package_uniqueness(ctx: Ctx) -> Probe:
    """Multiple deploy packages → mtime discovery may pick the wrong version."""
    pkgs = find_deploy_packages()
    if ctx.package_path:
        # Explicit package wins; still warn if siblings exist
        others = [p for p in pkgs if p.resolve() != Path(ctx.package_path).resolve()]
        if others:
            return Probe(False,
                         f"multiple deploy packages staged ({len(pkgs)}): "
                         f"using {Path(ctx.package_path).name}; also present: "
                         f"{[p.name for p in others[:5]]} — remove extras to avoid "
                         "mtime races on next transport")
        return Probe(True, f"explicit package {Path(ctx.package_path).name}")
    if len(pkgs) > 1:
        return Probe(False,
                     f"{len(pkgs)} deploy packages in search path "
                     f"({[p.name for p in pkgs[:6]]}) — keep only ONE version")
    if len(pkgs) == 1:
        return Probe(True, f"single package {pkgs[0].name}")
    return Probe(True, "no package tarballs in default paths (engine may use --package)")


def rem_package_uniqueness(ctx: Ctx) -> Fix:
    """Cannot auto-delete packages (Layer-A). Report MANUAL."""
    pkgs = find_deploy_packages()
    names = ", ".join(p.name for p in pkgs)
    return Fix(False,
               f"MANUAL: leave only the intended package tarball on disk "
               f"(found: {names}). Engine will not delete Layer-A artifacts.")


# --------------------------------------------------------------------------- #
# System / RKE2 health (mostly discover + limited remediations)
# --------------------------------------------------------------------------- #

def discover_system_plane(ctx: Ctx) -> List[str]:
    """Read-only lines for discovery report about system plane."""
    lines: List[str] = []
    # API healthz
    r = ctx.k(["get", "--raw", "/healthz"])
    if r.returncode == 0 and (r.stdout or "").strip() == "ok":
        lines.append("API /healthz=ok")
    else:
        lines.append(f"API /healthz FAILED rc={r.returncode}")
    # Critical kube-system pods
    for sel, label in (
        ("component=kube-apiserver", "apiserver"),
        ("k8s-app=kube-dns", "coredns"),
        ("app.kubernetes.io/name=rke2-ingress-nginx", "ingress-nginx"),
        ("app.kubernetes.io/name=rke2-canal", "canal"),
        ("k8s-app=canal", "canal"),
    ):
        ready, total = ctx.pods_ready("kube-system", sel)
        if total == 0:
            continue
        lines.append(f"kube-system {label} ready {ready}/{total}")
        if ready < total:
            lines.append(f"WEDGE: {label} not fully Ready")
    # IngressClass inventory
    ics = [(ic.get("metadata") or {}).get("name") for ic in ctx.items("ingressclass")]
    ics = [n for n in ics if n]
    lines.append(f"IngressClass: {ics or '(none)'}; preferred={detect_ingress_class(ctx)}")
    # rke2-server unit (node-local)
    if Path("/etc/rancher/rke2").is_dir():
        try:
            r = subprocess.run(
                ["systemctl", "is-active", "rke2-server"],
                capture_output=True, text=True, timeout=10,
            )
            lines.append(f"rke2-server: {(r.stdout or r.stderr or '').strip() or r.returncode}")
        except (OSError, subprocess.TimeoutExpired):
            lines.append("rke2-server: status unavailable")
    return lines


def det_system_plane(ctx: Ctx) -> Probe:
    """Soft gate: API healthy; warn on degraded coredns/ingress but only FAIL API."""
    r = ctx.k(["get", "--raw", "/healthz"])
    if r.returncode != 0 or (r.stdout or "").strip() not in ("ok", "ok\n"):
        # some clusters return more than "ok"
        if r.returncode != 0:
            return Probe(False, f"API /healthz unreachable rc={r.returncode}")
    bits = discover_system_plane(ctx)
    wedges = [b for b in bits if b.startswith("WEDGE")]
    if wedges:
        # Degraded data plane / DNS — surface as not-ok so remediate can try uncordon etc.
        return Probe(False, "; ".join(bits))
    return Probe(True, "; ".join(bits[:4]))


def rem_system_plane(ctx: Ctx) -> Fix:
    """Safe remediations only: uncordon nodes; never restart etcd from here unless
    API is up. CoreDNS/ingress image issues are CLOSURE (Layer-A) MANUAL."""
    actions: List[str] = []
    # Uncordon any SchedulingDisabled nodes
    for n in ctx.items("nodes"):
        name = (n.get("metadata") or {}).get("name")
        spec = n.get("spec") or {}
        if spec.get("unschedulable") and name:
            if ctx.k(["uncordon", name]).returncode == 0:
                actions.append(f"uncordoned {name}")
    # If coredns ImagePullBackOff — cannot pull air-gap
    miss = ctx.pod_image_missing("kube-system", "k8s-app=kube-dns")
    if miss is True:
        return Fix(False,
                   "MANUAL: coredns ImagePullBackOff — re-import RKE2 bundled images "
                   "(Layer-A); engine will not pull"
                   + (f"  [also: {'; '.join(actions)}]" if actions else ""))
    if actions:
        return Fix(True, "; ".join(actions))
    return Fix(False,
               "system plane degraded — inspect kube-system pods; "
               "engine does not restart etcd/control-plane automatically"
               + (f"  [{'; '.join(discover_system_plane(ctx)[:3])}]"))


# --------------------------------------------------------------------------- #
# HostPath registry (shared prep)
# --------------------------------------------------------------------------- #

def ensure_registry_hostpath() -> List[str]:
    actions: List[str] = []
    try:
        REGISTRY_HOSTPATH.mkdir(parents=True, exist_ok=True)
        if os.geteuid() == 0:
            os.chmod(REGISTRY_HOSTPATH, 0o777)
            actions.append(f"chmod 0777 {REGISTRY_HOSTPATH}")
        probe = REGISTRY_HOSTPATH / ".converge-write-probe"
        probe.write_text("ok")
        try:
            probe.unlink(missing_ok=True)  # type: ignore[call-arg]
        except TypeError:
            probe.unlink()
        if not actions:
            actions.append(f"hostPath {REGISTRY_HOSTPATH} ready (writable)")
    except PermissionError:
        actions.append(
            f"MANUAL: mkdir -p {REGISTRY_HOSTPATH} && chmod 0777 {REGISTRY_HOSTPATH} (need root)")
    except OSError as e:
        actions.append(f"hostPath prepare: {e}")
    return actions

def ensure_ingress_class_in_ctx(ctx: Ctx) -> str:
    """Stamp detected class into ctx.s3 so all zarf deploys pick it up."""
    if ctx.s3.get("INGRESS_CLASS"):
        return ctx.s3["INGRESS_CLASS"]
    cls = detect_ingress_class(ctx)
    ctx.s3["INGRESS_CLASS"] = cls
    return cls
