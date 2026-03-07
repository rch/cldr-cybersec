# verify-zarf-deployment.sh Rewrite for v1.2.0

## What Changed

Full rewrite of `zarf/scripts/verify-zarf-deployment.sh` (914 → 1133 lines, but net-simpler
due to removal of workarounds and addition of comprehensive verification).

### Removed
- `fix_image_tags()` (82 lines) — v1.0.0 workaround, not needed for v1.2.0
- `apply_disk_light_patches()` — replaced by `post_deploy_spill_patch()`
- All `sudo $KUBECTL --kubeconfig=$KUBECONFIG` patterns — replaced by plain `kubectl`
- Hardcoded version `1.0.0` in package filename — auto-detected via glob
- `/tmp/kubeconfig-zarf.yaml` copy dance — KUBECONFIG resolved once at startup

### Added
- `_resolve_kubeconfig()` — 5-tier fallback ported from devenv.nix:2574-2624
- `detect_node_ip()` — IPv4-only extraction with `awk '{print $1}'`
- `check_baseline()` — disk, nodes, DiskPressure, existing pods
- `check_eviction_config()` — warns if >85% disk + default thresholds
- `--verify-only` flag — jump straight to verification
- `deploy_package()` — passes S3/Dask vars from env (mirrors devenv.nix:2847-2854)
- `post_deploy_spill_patch()` — emptyDir fallback from devenv.nix:2861-2880
- `deploy_dashboard()` — auto-detect dashboard package, non-fatal
- Comprehensive `verify_deployment()` — namespaces, pods, HTTP endpoints, S3 bucket,
  Dask connectivity, DiskPressure, summary table

### Environment Variable Contract
All env differences resolved through `${VAR:-default}`. Script sources `$PROJECT_ROOT/.env`
if present. Same CLI flags work on tinybox and air-gap.

## Bug Fix During Implementation

`grep -c` under `set -o pipefail` with `|| echo "0"` causes double-output:
- `grep -c` outputs "0" and exits 1 (no matches)
- `pipefail` propagates exit 1
- `|| echo "0"` fires, adding a second "0"
- Command substitution captures "0\n0", causing `[[ "0\n0" -gt 0 ]]` syntax error

Fix: use `|| true` instead of `|| echo "0"` since `grep -c` always outputs the count.

## Test Results
- `--dry-run`: all auto-detection works, S3 vars shown correctly
- `--verify-only`: all 7 components Running, 4/4 workers, HTTP endpoints OK, S3 bucket OK, Dask connectivity OK
