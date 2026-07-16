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
# Field: package often staged beside the unpacked engine, not only /var/tmp.
DEFAULT_PKG_DIRS = (
    Path("/var/tmp"),
    Path("/opt/zarf"),
    Path.cwd(),
    Path(__file__).resolve().parent.parent,  # …/zarf when in-repo
    Path(__file__).resolve().parent,         # engine unpack root (converge/)
)

# Kubelet args for air-gap / large-disk workstations:
#  - Absolute free-space thresholds (Gi) so a 900G disk at ~90% full still schedules
#    (percent-based soft/hard eviction would fire with tens of GiB free).
#  - Image GC raised so kubelet does not prune transported Layer-A images.
_KUBELET_GC_ARGS = (
    'eviction-hard=nodefs.available<5Gi,imagefs.available<5Gi,nodefs.inodesFree<1%,memory.available<100Mi',
    'eviction-soft=nodefs.available<10Gi,imagefs.available<10Gi,memory.available<200Mi',
    'eviction-soft-grace-period=nodefs.available=5m,imagefs.available=5m,memory.available=2m',
    'eviction-minimum-reclaim=nodefs.available=1Gi,imagefs.available=1Gi',
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


def _canonical_kubelet_block() -> str:
    lines = [
        "# Dev / air-gap workstation: absolute free-space thresholds (not %).",
        "# Percent-based eviction on large disks triggers DiskPressure with tens",
        "# of GiB still free. Image GC raised so Layer-A images are not pruned.",
        "kubelet-arg:",
    ]
    for a in _KUBELET_GC_ARGS:
        lines.append(f'  - "{a}"')
    return "\n".join(lines) + "\n"


def kubelet_gc_policy_ok(text: Optional[str] = None) -> bool:
    """True if absolute eviction thresholds + raised image-GC are present."""
    raw = text if text is not None else _read_rke2_config()
    if not raw:
        return False
    m = re.search(r"image-gc-high-threshold[=:](\d+)", raw)
    if not m:
        return False
    try:
        if int(m.group(1)) < 99:
            return False
    except ValueError:
        return False
    # Prefer absolute Gi thresholds (dev large-disk posture).
    if "nodefs.available<5Gi" in raw:
        return True
    # Percent-based hard eviction: force upgrade to absolute Gi form.
    if "eviction-hard=" in raw and "Gi" not in raw:
        return False
    return False


def det_kubelet_gc_policy(_ctx: Ctx) -> Probe:
    """Layer-B node policy: default % eviction + 85% image-GC break air-gap stacks."""
    if not RKE2_CONFIG.parent.is_dir() and not Path("/etc/rancher").is_dir():
        return Probe(True, "not an RKE2 host path layout — kubelet GC N/A")
    raw = _read_rke2_config()
    if kubelet_gc_policy_ok(raw):
        return Probe(True, f"absolute eviction + image-GC raised in {RKE2_CONFIG}")
    if not raw:
        return Probe(False,
                     f"{RKE2_CONFIG} missing/empty — default kubelet eviction/GC will "
                     "DiskPressure / prune images under ordinary disk use")
    return Probe(False,
                 f"{RKE2_CONFIG} needs absolute free-space eviction (<5Gi hard) and "
                 "image-gc-high-threshold=100 — percent thresholds fire too early on large disks")


def rem_kubelet_gc_policy(_ctx: Ctx) -> Fix:
    """Write canonical lenient kubelet-arg block. Restarts rke2-server when root
    (does not disrupt running pods — containerd keeps them). MANUAL if not root."""
    if os.geteuid() != 0:
        return Fix(False,
                   f"MANUAL: as root, install absolute-threshold kubelet-arg block in "
                   f"{RKE2_CONFIG} and `systemctl restart rke2-server` "
                   "(see AIRGAP-CHEATSHEET §1)")
    try:
        RKE2_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        raw = _read_rke2_config()
        if kubelet_gc_policy_ok(raw):
            return Fix(False, "kubelet absolute-eviction + GC policy already applied")
        block = _canonical_kubelet_block()
        # Replace entire kubelet-arg: list(s) with the canonical block so we do not
        # leave duplicate keys (YAML last-wins) or stale percent thresholds.
        if re.search(r"(?m)^kubelet-arg:\s*$", raw):
            # Drop all kubelet-arg sections and any immediately following list items.
            cleaned = re.sub(
                r"(?ms)^kubelet-arg:\s*\n(?:[ \t]+-.*\n)*",
                "",
                raw,
            )
            new = cleaned.rstrip() + "\n\n" + block if cleaned.strip() else block
        else:
            new = (raw.rstrip() + "\n\n" + block) if raw.strip() else block
        RKE2_CONFIG.write_text(new)
        detail = f"wrote absolute-eviction kubelet policy to {RKE2_CONFIG}"
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
