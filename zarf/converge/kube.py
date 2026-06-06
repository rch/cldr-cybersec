"""World interface for the convergence engine.

How the engine talks to the cluster (kubectl) and the bundle (zarf), plus the run
context. kubectl-only at runtime so the engine runs node-side, operator-side, or as
an in-cluster Job. ``zarf`` is only needed for the EXPENSIVE init/push remediations;
if it's absent those degrade to a precise manual hint rather than failing.

Image presence (a Layer-A / CLOSURE concern) is checked via POD HEALTH
(ImagePullBackOff ⇒ image missing) rather than node-local ``crictl`` — this keeps
the check cross-node and runnable from anywhere with a kubeconfig.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

DEFAULT_MANIFEST = Path(__file__).resolve().parent.parent / "artifacts.manifest.json"
# Bundled k8s manifests (local-path-provisioner.yaml) live next to the package source
# locally; converge-aws.sh stages them into <stage>/manifests/ to match this default.
DEFAULT_MANIFESTS_DIR = Path(__file__).resolve().parent.parent / "manifests"


@dataclass
class Ctx:
    kubectl: List[str]                      # base argv, e.g. ["zarf","tools","kubectl"]
    apply: bool = False                     # actually remediate (vs dry-run/verify)
    manifest: dict = field(default_factory=dict)
    zarf_bin: Optional[str] = None
    package_path: Optional[str] = None
    manifests_dir: Optional[str] = None      # dir holding bundled manifests (local-path-provisioner.yaml)
    registry_pv_size: str = "5Gi"
    registry_pvc_enabled: bool = True
    # DEFAULT (resilient air-gap, the only modality public releases target): the Zarf
    # registry binds a claimRef hostPath PV, so NO default StorageClass / local-path-
    # provisioner / bootstrap images are needed. Set True only to opt a resourced
    # multi-node cluster back into dynamic provisioning for workloads that require it.
    dynamic_provisioning: bool = False
    s3: dict = field(default_factory=dict)
    timeout: int = 90
    verbose: bool = False
    _cap_cache: Optional[dict] = None

    # ------------------------------------------------------------------ process
    def run(self, argv: List[str], timeout: Optional[int] = None,
            input_: Optional[str] = None,
            env: Optional[dict] = None) -> subprocess.CompletedProcess:
        if self.verbose:
            print("    $ " + " ".join(argv))
        # ``env`` is MERGED onto the inherited environment — use it to pass secrets
        # (e.g. ZARF_VAR_S3_SECRET_KEY) so they never land on argv / the process table.
        run_env = {**os.environ, **env} if env else None
        try:
            return subprocess.run(
                argv, capture_output=True, text=True,
                timeout=timeout or self.timeout, input=input_, env=run_env,
            )
        except FileNotFoundError as e:
            return subprocess.CompletedProcess(argv, 127, "", str(e))
        except subprocess.TimeoutExpired as e:
            return subprocess.CompletedProcess(argv, 124, e.stdout or "", "timeout")

    def k(self, args: List[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        return self.run(self.kubectl + args, timeout=timeout)

    def kjson(self, args: List[str]) -> Any:
        r = self.k(args + ["-o", "json"])
        if r.returncode != 0 or not r.stdout.strip():
            return None
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            return None

    def get(self, kind: str, name: str = "", ns: Optional[str] = None) -> Optional[dict]:
        args = ["get", kind]
        if name:
            args.append(name)
        if ns:
            args += ["-n", ns]
        elif ns is None and kind not in ("nodes", "node", "ns", "namespace",
                                         "pv", "storageclass", "sc", "crd"):
            args.append("-A")
        obj = self.kjson(args)
        if obj is None:
            return None
        # single-name get returns the object; otherwise a List
        return obj

    def items(self, kind: str, ns: Optional[str] = None,
              selector: Optional[str] = None) -> List[dict]:
        args = ["get", kind]
        if ns:
            args += ["-n", ns]
        else:
            args.append("-A")
        if selector:
            args += ["-l", selector]
        obj = self.kjson(args)
        if not obj:
            return []
        return obj.get("items", []) if isinstance(obj, dict) else []

    def exists(self, kind: str, name: str, ns: Optional[str] = None) -> bool:
        args = ["get", kind, name]
        if ns:
            args += ["-n", ns]
        return self.k(args).returncode == 0

    def apply_yaml(self, yaml_text: str) -> subprocess.CompletedProcess:
        return self.run(self.kubectl + ["apply", "-f", "-"], input_=yaml_text)

    # ------------------------------------------------------------------ zarf
    def have_zarf(self) -> bool:
        return bool(self.zarf_bin) and (
            Path(self.zarf_bin).exists() or shutil.which(self.zarf_bin) is not None
        )

    def zarf(self, args: List[str], timeout: int = 1800,
             env: Optional[dict] = None) -> subprocess.CompletedProcess:
        return self.run([str(self.zarf_bin)] + args, timeout=timeout, env=env)

    # ------------------------------------------------------------------ derived
    def pod_image_missing(self, ns: str, selector: str) -> Optional[bool]:
        """True if any matching pod is stuck pulling its image (image absent in the
        closed world); False if pods exist and none are; None if no pods to judge."""
        pods = self.items("pods", ns=ns, selector=selector)
        if not pods:
            return None
        bad = ("ImagePullBackOff", "ErrImagePull", "ErrImageNeverPull")
        for p in pods:
            for cs in (p.get("status", {}).get("containerStatuses", []) +
                       p.get("status", {}).get("initContainerStatuses", [])):
                waiting = (cs.get("state", {}) or {}).get("waiting") or {}
                if waiting.get("reason") in bad:
                    return True
        return False

    def pods_ready(self, ns: str, selector: str) -> tuple:
        """(ready_count, total) for Ready pods matching selector."""
        pods = self.items("pods", ns=ns, selector=selector)
        ready = 0
        for p in pods:
            conds = {c["type"]: c["status"] for c in p.get("status", {}).get("conditions", [])}
            if conds.get("Ready") == "True":
                ready += 1
        return ready, len(pods)

    def node_capacity(self) -> dict:
        """Rough schedulable capacity across Ready, non-tainted-NoSchedule nodes.
        Used to size workers to capacity (the worker-replica cap) and for the banner.
        There is NO topology branch — the resilient path runs identically on one node
        or many; capacity only sizes the worker count, it never forks behavior."""
        if self._cap_cache is not None:
            return self._cap_cache
        nodes = self.items("nodes")
        ready = 0
        total_mem_gib = 0.0
        worker_like = 0
        for n in nodes:
            conds = {c["type"]: c["status"] for c in n.get("status", {}).get("conditions", [])}
            if conds.get("Ready") != "True":
                continue
            taints = n.get("spec", {}).get("taints", []) or []
            blocked = any(t.get("effect") in ("NoSchedule", "NoExecute") and
                          "control-plane" not in t.get("key", "") for t in taints)
            ready += 1
            mem = n.get("status", {}).get("allocatable", {}).get("memory", "0")
            total_mem_gib += _mem_to_gib(mem)
            if not blocked:
                worker_like += 1
        cap = {"ready_nodes": ready, "schedulable_nodes": max(worker_like, 1),
               "total_mem_gib": round(total_mem_gib, 1)}
        self._cap_cache = cap
        return cap


def _mem_to_gib(v: str) -> float:
    v = v.strip()
    units = {"Ki": 1 / 2**20, "Mi": 1 / 2**10, "Gi": 1.0, "Ti": 1024.0,
             "K": 1e3 / 2**30, "M": 1e6 / 2**30, "G": 1e9 / 2**30}
    for u, mul in units.items():
        if v.endswith(u):
            try:
                return float(v[:-len(u)]) * mul
            except ValueError:
                return 0.0
    try:
        return float(v) / 2**30
    except ValueError:
        return 0.0


def load_manifest(path: Optional[str] = None) -> dict:
    p = Path(path) if path else DEFAULT_MANIFEST
    return json.loads(p.read_text())


def bootstrap_image_refs(manifest: dict) -> List[str]:
    return [i["ref"] for i in manifest.get("bootstrap_images", {}).get("images", [])]
