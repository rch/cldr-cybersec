# Agent instructions (cyberphy / converge)

## Portable paths (air-gap) — do not reintroduce site pollution

When editing **converge** (`zarf/scripts/converge*.sh`, `zarf/converge/`):

| OK | Forbidden |
|----|-----------|
| argv2 / `--package` path the operator passes | Hardcoded `/mnt/…`, site trees, hostnames |
| Package next to `converge-node.sh` or in **CWD** | `/home/<user>/…` for package or creds |
| Optional `/var/tmp` staging | mtime walks across foreign mounts inventing a kit |
| Generic docs examples (`/tmp`, “NFS of your choice”) | Encoding a past field path as default discovery |

Credentials are **local to the operator environment**. Do not assume remote
ownership, home directories, or exfiltrate secrets into chat or commits.

Regression guard: `uv run pytest tests/test_converge_portable_paths.py`

## Held backlog (do not implement mid matrix-rerun)

Until the current air-gap/sandbox validation cycle finishes, **do not land**:

1. **Engine T0 CNI root-cause** — iptables absent + IPAM-exhaustion signature
   (detect/MANUAL; never auto-prune). Details:
   `docs/scratch/2026-07-30/174410_ci_detour_backlog_cni_matrix.md`
2. **Matrix case isolation** — post-case cleanup always runs (even on FAIL)
3. **Matrix stream output** — tee per-case converge logs (no full buffer)

Ship #1 with the next deliberate engine cut after the rerun; #2/#3 with harness
backlog (case 14+).
