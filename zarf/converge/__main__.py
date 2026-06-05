"""CLI for the convergence engine.

    python3 -m converge --verify                 # is the deployment at target? (oracle)
    python3 -m converge --dry-run                # what's broken + what I'd remediate
    python3 -m converge --apply                  # remediate to a fixpoint
    python3 -m converge --apply --package zarf-package-...tar.zst --set S3_BUCKET=...

Exit: 0 = converged / clean dry-run; 1 = not converged; 2 = CLOSURE violation
(a transported Layer-A artifact is missing — operator must re-import, engine won't).
"""
from __future__ import annotations

import argparse
import shlex
import shutil
import sys
from pathlib import Path

from . import __version__
from .catalog import CATALOG
from .engine import closure_violations, evaluate, reconcile, report
from .kube import Ctx, load_manifest


def _default_zarf() -> str | None:
    for cand in ("zarf", "/usr/local/bin/zarf", "/var/lib/rancher/rke2/bin/zarf"):
        if shutil.which(cand) or Path(cand).exists():
            return cand
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="converge", description="cybersec-dask deployment convergence engine")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="remediate to a fixpoint")
    mode.add_argument("--verify", action="store_true", help="assert target state (oracle); no changes")
    mode.add_argument("--dry-run", action="store_true", help="show what would be remediated (default)")

    ap.add_argument("--kubectl", default="zarf tools kubectl",
                    help='base kubectl command (default: "zarf tools kubectl")')
    ap.add_argument("--kubeconfig", help="append --kubeconfig <path> to kubectl")
    ap.add_argument("--manifest", help="path to artifacts.manifest.json")
    ap.add_argument("--zarf", default=_default_zarf(), help="path to the zarf binary")
    ap.add_argument("--package", help="path to the deploy .tar.zst (for component remediations)")
    ap.add_argument("--topology", choices=["single-tight", "multi-ample", "auto"], default="auto")
    ap.add_argument("--registry-pvc-size", default="5Gi")
    ap.add_argument("--no-registry-pvc", action="store_true",
                    help="run the internal registry on emptyDir (tight-disk single node)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VAL",
                    help="S3/zarf variable to pass to component deploys (repeatable)")
    ap.add_argument("--creds-file", help="file of KEY=VALUE lines (e.g. S3_SECRET_KEY=...) "
                    "merged into the deploy variables — keeps secrets off argv/ps")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=f"converge {__version__}")
    args = ap.parse_args(argv)

    kube = shlex.split(args.kubectl)
    if args.kubeconfig:
        kube += ["--kubeconfig", args.kubeconfig]

    s3 = {}
    if args.creds_file:  # loaded first so explicit --set can override
        try:
            for line in Path(args.creds_file).read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    s3[k.strip()] = v
        except OSError as e:
            print(f"FATAL: --creds-file unreadable: {e}", file=sys.stderr)
            return 2
    for kv in args.set:
        if "=" in kv:
            k, v = kv.split("=", 1)
            s3[k] = v

    ctx = Ctx(
        kubectl=kube,
        apply=args.apply,
        topology=args.topology,
        manifest=load_manifest(args.manifest),
        zarf_bin=args.zarf,
        package_path=args.package,
        registry_pv_size=args.registry_pvc_size,
        registry_pvc_enabled=not args.no_registry_pvc,
        s3=s3,
        verbose=args.verbose,
    )

    # connectivity gate
    probe = ctx.k(["version", "--client=false", "-o", "json"])
    if probe.returncode != 0 and ctx.k(["get", "--raw", "/healthz"]).returncode != 0:
        print(f"FATAL: cannot reach the cluster via `{args.kubectl}`.\n  {probe.stderr.strip()}",
              file=sys.stderr)
        return 2

    print(f"converge {__version__}  |  topology={ctx.topology_profile()}  |  "
          f"mode={'apply' if args.apply else 'verify' if args.verify else 'dry-run'}")

    if args.apply:
        results, order = reconcile(ctx, CATALOG)
    else:
        results, order = evaluate(ctx, CATALOG, apply_preview=not args.verify)

    converged = report(results, order)

    if closure_violations(results):
        return 2
    return 0 if (converged or (not args.apply and not args.verify)) else 1


if __name__ == "__main__":
    sys.exit(main())
