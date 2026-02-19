# AWS Zarf Deployment Docs + Ansible Role Update

## Problem

The AWS deployment docs (`infra/aws/README.md`) only documented the Helm-based
`site.yml` path. The actual air-gap deployment uses `airgap-e2e.yml` with the
`zarf-deploy` Ansible role, which was undocumented in the README.

Additionally:
- K8s Dashboard was not in the Zarf pipeline
- No disk-light support in the Ansible role
- Worker count default was 32 in Ansible but 4 in the Zarf package
- ngrok was still prominently documented despite being deprecated

## Changes

### `infra/aws/README.md`

- Added "Deployment Paths" table showing Zarf vs Helm options
- Rewrote "Quick Start" to use `airgap-e2e.yml` as primary path
- Added "Phase 3: Deploy via Zarf (Air-Gap)" with full playbook usage
- Moved Helm deploy to "Phase 3 (Alternative)"
- Updated Task Reference with Zarf playbook options and `--tags`/`-e` examples
- Marked ngrok as deprecated throughout
- Added Zarf-specific troubleshooting (`zarf init` hang, ImagePullBackOff)
- Updated directory structure to show `zarf-deploy` role and `airgap-e2e.yml`

### `infra/aws/ansible/roles/zarf-deploy/defaults/main.yml`

- Added `zarf_dashboard_package` variable for K8s Dashboard
- Added `zarf_disk_light` flag (default: false)
- Fixed `zarf_dask_worker_replicas` from "32" to "4" (matches package default)

### `infra/aws/ansible/roles/zarf-deploy/tasks/stage.yml`

- Added staging for K8s Dashboard package (optional — skipped if not present locally)

### `infra/aws/ansible/roles/zarf-deploy/tasks/init.yml`

- All PV creation tasks gated on `not (zarf_disk_light | bool)`
- Added DiskPressure taint removal step for disk-light mode
- Added `--set REGISTRY_PVC_ENABLED=false` to `zarf init` when disk-light
- Added informational debug message when disk-light skips PVs

### `infra/aws/ansible/roles/zarf-deploy/tasks/deploy.yml`

- Added disk-light spill volume patch (emptyDir 512Mi) after main deploy
- Added K8s Dashboard package deploy (conditional on package being staged)

## Key Decisions

- **Zarf as primary path**: Most AWS deployments target air-gapped environments
  where Helm repos aren't accessible. Zarf bundles everything needed.
- **Disk-light as opt-in for AWS**: AWS instances typically have 100GB+ EBS,
  so disk-light is off by default but available via `-e zarf_disk_light=true`.
- **K8s Dashboard as separate package**: Deployed after the main package since
  it's a separate Zarf package with its own images.
