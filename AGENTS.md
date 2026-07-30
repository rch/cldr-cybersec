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
