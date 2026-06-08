#!/usr/bin/env python3
"""Closure CI gate for the cybersec-dask Zarf package.

Enforces the CLOSURE law: every Layer-A artifact declared in
``zarf/artifacts.manifest.json`` MUST be present in the built Zarf package, and
the package MUST stay under the GitHub release-asset size budget. Run by
``devenv tasks run zarf:package`` right after ``zarf package create`` — a missing
image or an oversize bundle fails the build, so we never discover at *deploy*
time (in the air gap) that something was never transported.

Also emits a realized manifest (the images actually found + the package size) for
provenance.

Usage:  python3 scripts/check-closure.py <package.tar.zst>
Deps:   Python 3 stdlib only + the `zarf` binary on PATH.
Exit:   0 = closure satisfied; 1 = missing artifact or over budget.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE.parent / "artifacts.manifest.json"


def basename_tag(ref: str) -> str:
    """Reduce an image ref to '<name>:<tag>' so matching survives registry-prefix
    normalization (docker.io/library/busybox:1.37 -> busybox:1.37)."""
    # strip digest
    ref = ref.split("@", 1)[0]
    # split tag (only on the last ':' that isn't part of a host:port before a '/')
    if ":" in ref.rsplit("/", 1)[-1]:
        path, tag = ref.rsplit(":", 1)
    else:
        path, tag = ref, "latest"
    name = path.rsplit("/", 1)[-1]
    return f"{name}:{tag}"


def declared_images(manifest: dict) -> list[str]:
    out = []
    for group in ("bootstrap_images", "package_images"):
        for img in manifest.get(group, {}).get("images", []):
            out.append(img["ref"])
    return out


def declared_transport_files(manifest: dict) -> list[dict]:
    """Layer-A files carried ALONGSIDE the package (e.g. the zarf-init package) — declared
    in the `binaries` group with a `file`. They are NOT inside the .tar.zst, so the gate
    verifies them by PRESENCE in the transport staging area, not by package inspection."""
    return [b for b in manifest.get("binaries", {}).get("items", []) if b.get("file")]


def find_transport_file(name: str, pkg_dir: str) -> "str | None":
    """Search the likely staging locations for a transport artifact (beside the package,
    the release build dir, $RELEASE_DIR, the zarf cache, /tmp)."""
    candidates = [
        pkg_dir,
        os.path.join(pkg_dir, "..", "build", "release"),
        os.environ.get("RELEASE_DIR", ""),
        os.path.expanduser("~/.zarf-cache"),
        "/tmp",
    ]
    for d in candidates:
        if d and os.path.isfile(os.path.join(d, name)):
            return os.path.normpath(os.path.join(d, name))
    return None


def package_images(pkg: str) -> str:
    """Return the package's image listing as text (best-effort across zarf CLI
    forms). We substring-match against it rather than parse a fixed schema."""
    for args in (
        ["zarf", "package", "inspect", "images", pkg],
        ["zarf", "package", "inspect", pkg, "--list-images"],
        ["zarf", "package", "inspect", pkg],
    ):
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=120)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
    return ""


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check-closure.py <package.tar.zst>", file=sys.stderr)
        return 2
    pkg = sys.argv[1]
    if not os.path.isfile(pkg):
        print(f"FAIL: package not found: {pkg}", file=sys.stderr)
        return 1

    manifest = json.loads(MANIFEST.read_text())
    budget = manifest["package"]["size_budget_bytes"]
    warn = manifest["package"].get("size_warn_bytes", budget)

    print("=== Closure gate: cybersec-dask Zarf package ===")
    failures: list[str] = []

    # --- 1. size budget (GitHub release-asset ceiling) ---
    size = os.path.getsize(pkg)
    gib = size / 2**30
    if size > budget:
        failures.append(f"package {gib:.2f} GiB EXCEEDS budget {budget/2**30:.2f} GiB (GitHub release limit)")
    elif size > warn:
        print(f"  [WARN] package {gib:.2f} GiB is near the {warn/2**30:.2f} GiB threshold — slim soon")
    else:
        print(f"  [ OK ] size {gib:.2f} GiB within budget ({budget/2**30:.2f} GiB)")

    # --- 2. every declared Layer-A image is in the package ---
    listing = package_images(pkg)
    if not listing:
        failures.append("could not read image list from package (zarf package inspect failed)")
    else:
        for ref in declared_images(manifest):
            needle = basename_tag(ref)
            if needle in listing or ref in listing:
                print(f"  [ OK ] image present: {ref}")
            else:
                failures.append(f"declared image MISSING from package: {ref}")

    # --- 3. transport artifacts carried ALONGSIDE the package (the zarf-init package) ---
    # Layer A, but NOT inside the .tar.zst — verify they're staged for transport. Advisory by
    # default (they're fetched/staged separately from `zarf package create`); set
    # CLOSURE_REQUIRE_TRANSPORT=1 for a RELEASE build that must bundle every Layer-A artifact.
    require_transport = os.environ.get("CLOSURE_REQUIRE_TRANSPORT", "").lower() in ("1", "true", "yes")
    pkg_dir = os.path.dirname(os.path.abspath(pkg))
    transport: list[dict] = []
    for b in declared_transport_files(manifest):
        name = b["file"]
        found = find_transport_file(name, pkg_dir)
        if found:
            print(f"  [ OK ] transport artifact staged: {name}")
            transport.append({"file": name, "present": True})
        else:
            hint = " — fetch + stage it beside the package (Layer A)" + (
                f"; source: {b['source']}" if b.get("source") else "")
            if b.get("critical") and require_transport:
                failures.append(f"transport artifact NOT staged: {name}{hint}")
            else:
                print(f"  [WARN] transport artifact not staged: {name}{hint}")
            transport.append({"file": name, "present": False, "critical": bool(b.get("critical"))})

    # --- 4. emit realized manifest (provenance) ---
    realized = {
        "package": os.path.basename(pkg),
        "size_bytes": size,
        "image_listing_present": bool(listing),
        "transport_artifacts": transport,
    }
    Path(pkg + ".closure.json").write_text(json.dumps(realized, indent=2))

    if failures:
        print("\n=== CLOSURE GATE FAILED ===")
        for f in failures:
            print(f"  [FAIL] {f}")
        print("\nFix: ensure every declared image is in the build (or slim the bundle), "
              "and every Layer-A transport artifact is staged beside the package.")
        return 1
    print("\n=== CLOSURE GATE PASSED — bundle is complete and within budget ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
