# Fix: zarf:local:deploy-dashboard Task & Gitignore

## Problem

1. **`zarf:local:deploy-dashboard` task failed with exit code 2** — the `_resolve_kubeconfig()` function was oversimplified compared to the proven version in `zarf:local:deploy`, missing several fallback paths and permission-aware logging.

2. **`set -euo pipefail` gotcha** — the task used `zarf package deploy ...; if [ $? -ne 0 ]` which is unreachable under `set -e` (the script exits immediately on non-zero). Fixed to use `if ! cmd; then` pattern.

3. **Built Zarf package (`.tar.zst`) not gitignored** — the ~100MB+ package file in `zarf/kubernetes-dashboard/` would exceed GitHub's file size limit.

## Changes

### `devenv.nix` (lines 2952-2986)
Replaced the 3-fallback `_resolve_kubeconfig()` with the full 7-fallback version from `zarf:local:deploy`:
- `$KUBECONFIG` env var (with readability check)
- `~/.kube/rke2.yaml`
- k3d target-aware (`$DEVENV_STATE/kubeconfig`)
- `/etc/rancher/rke2/rke2.yaml` (with permission error message)
- `~/.kube/config` fallback

### `devenv.nix` (line 3008)
Changed `zarf package deploy ... ; if [ $? -ne 0 ]` to `if ! zarf package deploy ...; then` to work correctly under `set -euo pipefail`.

### `zarf/kubernetes-dashboard/.gitignore` (new)
```
zarf-package-*.tar.zst
```

## Verified
- `git check-ignore` confirms the `.tar.zst` is excluded
- `_resolve_kubeconfig()` now matches the working pattern exactly
