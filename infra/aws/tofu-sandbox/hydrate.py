#!/usr/bin/env python3
"""Hydrate the `cybersec.sandbox` HOCON config -> build/sandbox/{sandbox.auto.tfvars, sandbox.env}.

Resolves the sandbox config (config/reference.conf + ${?ENV} overrides), auto-detects the
operator's egress /32 when sandbox.aws.ssh_cidr is empty, and writes two files under the
gitignored build_dir:
  - sandbox.auto.tfvars : tofu vars (ssh_cidr, instance_type, root_gb, aws_region, key_path)
  - sandbox.env         : KEY=VALUE consumed by run-sandbox.sh (paths, S3, worker count)

All per-developer artifacts live under build_dir, so nothing secret is ever committed.
Run via `uv run python infra/aws/tofu-sandbox/hydrate.py` (the justfile does this for you).
"""
from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

from cybersec.config.loader import load_config


def _val(cfg, path: str, default: Any = "") -> Any:
    v = cfg.get(path, default)
    return default if v is None else v


def main() -> int:
    cfg = load_config(os.environ.get("CYBERSEC_ENV", "local"))

    root = Path(str(_val(cfg, "cybersec.paths.root") or os.getcwd()))
    build_dir = Path(str(_val(cfg, "cybersec.sandbox.build_dir") or (root / "build/sandbox")))
    build_dir.mkdir(parents=True, exist_ok=True)

    region = _val(cfg, "cybersec.sandbox.aws.region", "us-east-1")
    itype = _val(cfg, "cybersec.sandbox.aws.instance_type", "m7i.2xlarge")
    root_gb = int(_val(cfg, "cybersec.sandbox.aws.root_gb", 80))
    cidr = str(_val(cfg, "cybersec.sandbox.aws.ssh_cidr", "")).strip()
    zarf_ver = _val(cfg, "cybersec.sandbox.zarf_version", "v0.70.1")
    pkg = str(_val(cfg, "cybersec.sandbox.package", "")).strip()
    init_pkg = str(_val(cfg, "cybersec.sandbox.init_package", "")).strip()
    workers = int(_val(cfg, "cybersec.sandbox.dask_worker_replicas", 1))
    s3_ep = _val(cfg, "cybersec.sandbox.s3.endpoint", "")
    s3_bkt = _val(cfg, "cybersec.sandbox.s3.bucket", "")
    s3_ak = _val(cfg, "cybersec.sandbox.s3.access_key", "")
    s3_sk = _val(cfg, "cybersec.sandbox.s3.secret_key", "")

    # Auto-detect the egress /32 only when not pinned (needs internet; runs on the laptop).
    if not cidr:
        ip = urllib.request.urlopen("https://checkip.amazonaws.com", timeout=10).read().decode().strip()
        cidr = f"{ip}/32"

    # Extra operator CIDRs (per-dev; e.g. a CGNAT carrier's ranges when the egress IP
    # rotates mid-session — it severed two live runs). Env overrides HOCON; comma-sep.
    extra_raw = os.environ.get("SANDBOX_EXTRA_SSH_CIDRS",
                               str(_val(cfg, "cybersec.sandbox.aws.extra_ssh_cidrs", "") or ""))
    extra = [c.strip() for c in str(extra_raw).strip("[] ").split(",") if c.strip()]
    extra_tf = "[" + ", ".join(f'"{c}"' for c in extra) + "]"

    if not pkg:
        cands = sorted(
            (root / "zarf").glob("zarf-package-cybersec-dask-amd64-*.tar.zst"),
            key=lambda p: p.stat().st_mtime,
        )
        pkg = str(cands[-1]) if cands else str(root / "zarf" / "zarf-package-cybersec-dask-amd64-1.5.0.tar.zst")
    if not init_pkg:
        init_pkg = str(build_dir / f"zarf-init-amd64-{zarf_ver}.tar.zst")

    key_path = build_dir / "sandbox.pem"

    (build_dir / "sandbox.auto.tfvars").write_text(
        f'ssh_cidr        = "{cidr}"\n'
        f"extra_ssh_cidrs = {extra_tf}\n"
        f'instance_type   = "{itype}"\n'
        f"root_gb         = {root_gb}\n"
        f'aws_region      = "{region}"\n'
        f'key_path        = "{key_path}"\n'
    )
    (build_dir / "sandbox.env").write_text(
        f'BUILD_DIR="{build_dir}"\n'
        f'KEY="{key_path}"\n'
        f'PACKAGE="{pkg}"\n'
        f'INIT_PACKAGE="{init_pkg}"\n'
        f'ZARF_VERSION="{zarf_ver}"\n'
        f'S3_ENDPOINT="{s3_ep}"\n'
        f'S3_BUCKET="{s3_bkt}"\n'
        f'S3_ACCESS_KEY="{s3_ak}"\n'
        f'S3_SECRET_KEY="{s3_sk}"\n'
        f'S3_REGION="{region}"\n'
        f'DASK_WORKER_REPLICAS="{workers}"\n'
    )

    print(f"hydrated -> {build_dir}/sandbox.auto.tfvars")
    print(f"           {build_dir}/sandbox.env")
    print(f"  ssh_cidr={cidr}  instance_type={itype}  root_gb={root_gb}")
    print(f"  package={Path(pkg).name} ({'present' if Path(pkg).exists() else 'MISSING — build it'})")
    print(f"  init_package={'present' if Path(init_pkg).exists() else 'MISSING — fetch it'}: {init_pkg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
