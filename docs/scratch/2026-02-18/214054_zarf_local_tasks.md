# Local Zarf Deployment Task Group (`zarf:local:*`)

## Summary

Implemented the `zarf:local:*` task group for deploying the full Dask+JupyterHub+Panel-Viz
stack to a local cluster (RKE2 or k3d) running alongside the Flink devenv stack.

## Files Created

| File | Purpose |
|------|---------|
| `policy/k8s/local/requirements.rego` | Conftest policy for local Zarf deployment validation |
| `cybersec/zarf/local.py` | Runtime config gathering (node resources, zarf-specific) |

## Files Modified

| File | Change |
|------|--------|
| `config/reference.conf` | Added `kubernetes.local {}` config block |
| `cybersec/config/runtime.py` | Added zarf/zarf_version to `_check_tools()` |
| `cybersec/k8s/prepare.py` | Added node_resources/zarf_local to `_flatten_for_policy()` |
| `cybersec/zarf/__init__.py` | Exported `gather_local_zarf_config` |
| `cybersec/commands/zarf.py` | Added local, local.preflight, local.status commands |
| `devenv.nix` | Added 4 task definitions (preflight, init, deploy, status) |

## New devenv Tasks

- `zarf:local:preflight` - Detect target, gather config, run conftest validation
- `zarf:local:init` - Initialize Zarf with `REGISTRY_PVC_ENABLED=false` (disk-light)
- `zarf:local:deploy` - Deploy package with emptyDir spill patch, wait for Panel-Viz
- `zarf:local:status` - Show pod status and service accessibility

## New MCP Commands

- `/zarf local` - Show local deployment info
- `/zarf local preflight` - Validate local deployment requirements
- `/zarf local status` - Show deployment status

## Key Design Decisions

- **Disk-light defaults**: Registry PVC disabled, emptyDir spill unless `DASK_SPILL_DIR` is set
- **Target-aware kubeconfig**: Auto-detects RKE2 (`/etc/rancher/rke2/rke2.yaml`), k3d (`.devenv/state/kubeconfig`), or explicit `$KUBECONFIG`
- **Memory-aware sizing**: Policy recommends 1-4 Dask workers based on available RAM (total - 7 GB Flink overhead)
- **Post-deploy spill patch**: If no valid spill dir exists, patches daskcluster to use emptyDir{sizeLimit:512Mi}
- **Existing paths unaffected**: All existing `zarf:*` and `k8s:*` tasks remain unchanged

## Verification

- 134 tests pass (pre-existing numpy identity failures excluded)
- conftest validates the rego policy syntax
- `gather_local_zarf_config()` correctly detects 125 GB RAM, disk at 11%, zarf v0.66.0
- MCP commands `/zarf local`, `/zarf local status` return correct data
- Existing `/zarf` command unchanged
