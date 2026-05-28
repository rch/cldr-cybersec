# Zarf-on-AWS deploy session — fixes, hardening, and the air-gap gap

End-to-end deploy of the cybersec-dask zarf package to a fresh RKE2 cluster
in account `050330818249` (us-east-1), driven from a macOS Apple-Silicon laptop.
Two endpoints validated against WARP: `viz.dev.aws.zndx.org` (OTEL Span Explorer,
HoloViews + Datashader + Bokeh + Panel rendering with HoloViews 1.x via the
`cybersec-dask:2025.2.0` image) and `dask.dev.aws.zndx.org` (scheduler dashboard).

## Root causes fixed during the session

### 1. Cross-account deploy (provider profile foot-gun)

`infra/aws/tofu/provider.tf` hardcoded `profile = "default"`, which on a
developer machine routed every apply through `[default]` in `~/.aws/credentials`
— a static IAM user key in account `268282262010`, a completely different
team. An entire 79-resource deploy landed in the wrong account before we
caught it via `aws sts get-caller-identity` against the tofu provider's
implicit profile.

OPA policies (Owner-tag, prefix, region-from-tfstate) could not catch this
class of bug because they validate the plan's resource attributes, not the
account the provider authenticates against.

Fix: `provider.tf` switched to `profile = var.aws_profile` with `default = null`
so the SDK chain honours `AWS_PROFILE`. `aws:provision`, `aws:destroy`, and
`aws:teardown` in devenv.nix now export `TF_VAR_aws_profile=$PROFILE` next
to the existing `TF_VAR_aws_region`. Misrouted resources were surgically
destroyed via tfstate-driven `aws:destroy`. See [[project_misrouted_deploy_268282262010]]
and [[feedback_provider_profile_audit]] in memory.

### 2. `aws configure get` does not work for SSO profiles

`aws:provision`, `aws:destroy`, `aws:teardown`, `aws:deploy`, `aws:deploy:zarf`,
and `aws:generate-dataset` all used:

```bash
AWS_ACCESS_KEY_ID=$(aws configure get aws_access_key_id --profile $AWS_PROFILE)
```

This returns an empty string with exit 1 for SSO profiles (which only have
a temporary session token, not static keys). Under `set -e` the task aborts
within ~2 seconds. Replaced with `aws configure export-credentials --profile $AWS_PROFILE`
plus jq extraction of `AccessKeyId`, `SecretAccessKey`, and `SessionToken`.
All five sites in devenv.nix patched.

### 3. `aws:deploy:zarf` was not air-gap aware

The task assumed the control plane already had zarf, the zarf init package,
SSH credentials for the workers, and a default StorageClass. None of these
are guaranteed on a fresh RKE2 cluster. Several new phases added:

- **Phase 1a — stage SSH key on bastion**: previously the bastion-side scp
  hop to the control plane tried to use the laptop's local path
  (`/Users/<you>/.ssh/...`), which doesn't exist on the bastion. Now the
  key is scp'd to `/home/ec2-user/.ssh/` on the bastion first.
- **Phase 1/2 — `/var/tmp/` instead of `/tmp/`**: bastion's `/tmp` is a
  tmpfs with a 955 MiB cap. The 1.3 GiB zarf package overran it and the
  upload silently failed mid-stream. `/var/tmp/` lives on the root FS
  (29 GiB free).
- **Phase 2b — install zarf binary + init package on CP**: idempotent
  `curl` from CP via the VPC NAT gateway, pinned to `v0.70.1` (the same
  version used to build the package; format-version skew rejects
  cross-version packages). The init package is the often-missed piece —
  `zarf init` needs `/var/tmp/zarf-init-amd64-v0.70.1.tar.zst` next to the
  cybersec-dask package, or it errors with "this command requires a zarf-init
  package".
- **Phase 2c — bootstrap StorageClass if zarf is not yet initialized**:
  the chicken-and-egg ordering bug. The cybersec-dask package vendors
  local-path-provisioner as a Helm chart, but that runs in Phase 4 (after
  zarf init succeeds). zarf init creates a PVC for its in-cluster registry,
  which hangs Pending forever without a default StorageClass. Phase 2c
  applies upstream `rancher/local-path-provisioner` v0.0.32 from GitHub
  via NAT and marks it default. See [[feedback_storageclass_before_zarf_init]].
- **Phase 2d — clear stuck zarf namespace**: if a previous `zarf init`
  was killed mid-run, the registry PVC is left Pending without a StorageClass
  and a fresh init will not recreate it. Only triggers when a Pending PVC
  is actually detected.
- **Phase 3b — hand off StorageClass ownership to Helm**: after `zarf init`
  succeeds, delete the bootstrap local-path namespace and cluster-scoped
  resources so the cybersec-dask package's Helm chart can own them on a
  clean install. The zarf registry PVC stays bound to its already-provisioned
  PV.

### 4. `eval $SSH_CP "<multi-line cmd>"` runs commands locally

The existing `eval $SSH_BASTION "..."` / `eval $SSH_CP "..."` pattern works
for single-line commands but breaks badly for multi-line scripts. eval joins
its args with spaces and re-parses, so newlines (and semicolons) inside the
quoted block become top-level command separators _after_ the ssh invocation.
The result: any command on a continuation line runs LOCALLY instead of on
the remote host. Discovered when `/usr/local/bin/zarf version` was somehow
being executed on the laptop.

Phases 2b, 2c, 2d, 3, 3b, and 4 in `aws:deploy:zarf` rewritten to use
direct `ssh` invocations (with the ProxyCommand spelled out, no eval). For
multi-line bootstrap scripts (Phase 2b, 2c, 3b) the script is piped via
`bash -s <<REMOTE_HEREDOC`. Single-line commands (Phase 3, 4) just use
`ssh ... "<one-liner>"`.

### 5. STS temporary credentials require the session token

`pyarrow.fs.S3FileSystem` (datagen) and `s3fs.S3FileSystem` (panel-viz)
both accept `access_key` / `secret_key` but require an explicit `session_token`
argument when the key is an STS temporary credential (`ASIA...` prefix).
Without it, the SDK sends only the key+secret and S3 rejects with
`InvalidAccessKeyId: The AWS Access Key Id you provided does not exist
in our records`. boto3's default `Session()` reads `AWS_SESSION_TOKEN`
from env automatically; the explicit-arg style in these templates does not.

Both Ansible templates fixed to:
- Pass `session_token` to the S3 client when `AWS_SESSION_TOKEN` is set.
- Fall through to the default credential chain (i.e. EC2 instance metadata
  → instance role) when access_key / secret_key are empty.

### 6. AWS credentials should come from the EC2 instance role, not static keys

The Ansible role defaults previously read access keys via:

```yaml
panel_aws_access_key_id: "{{ lookup('env', 'AWS_ACCESS_KEY_ID') |
    default(lookup('ini', 'aws_access_key_id', section='default',
    file='~/.aws/credentials', errors='ignore'), true) }}"
```

— meaning if `AWS_ACCESS_KEY_ID` was unset in the shell, Ansible would
silently fall back to the developer's `~/.aws/credentials [default]` section,
which on many machines points to a long-lived IAM user key in a different
account. That's how `AKIAT45W6OH5OUKNBJTQ` (the `268282262010/user/rhill`
key from §1) ended up baked into pods.

`cybersec-dask-rke2-node` (the EC2 instance role on every worker) has
`s3:GetObject/PutObject/DeleteObject/ListBucket` on `arn:aws:s3:::cybersec-dask-*`
via the `s3_access` inline policy from `infra/aws/tofu/ec2.tf`. IMDS at
`169.254.169.254` is reachable from pods. So with no static keys in env,
boto3's default chain finds the instance role and Just Works. No STS
expiry headaches.

Both `panel-viz` and `datagen` role defaults now read only from `AWS_*`
env vars and default to empty strings — no INI fallback. The Python
templates handle empty keys by passing no key/secret kwargs at all, which
triggers the default chain.

### 7. Dask worker Deployments needed the same treatment

`zarf package deploy` rendered the `cybersec-dask` DaskCluster manifest
with `###ZARF_VAR_S3_ACCESS_KEY###` substituted to STS temp keys (whatever
was in the operator's shell at the time). The dask-kubernetes operator then
created 14 individual Deployments (one per worker name) each baked with
those static keys at creation time. Patching the DaskCluster CR doesn't
propagate; the DaskWorkerGroup CR's `worker.spec.containers[0].env` is the
right spec, but the operator also baked the env into each Deployment.

Required scaling the operator to 0, then patching every worker Deployment
+ scheduler Deployment via `kubectl patch --type=json` to `remove` the
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` env entries by index, then
restarting pods, then scaling the operator back up.

The proper fix is upstream: `zarf/manifests/dask-cluster.yaml` should not
include the AWS env vars at all on AWS deploys (let workers use the
instance role).

### 8. Panel/Bokeh redirects `/` to an internal cluster name

`panel serve /tmp/app.py` defaults to serving the app at `/<filename>`
(`/app`). A GET on `/` returns a 302 with `Location:
http://otel-navigator.panel-viz.svc.cluster.local:5006/app` — i.e. the
in-cluster DNS name. The browser can't follow that and Cloudflare's edge
ends up returning 503 to the user. `--index app` makes `/` serve the app
directly. Fixed in the panel-viz role's deployment template (and the
runtime deployment was kubectl-patched as well).

### 9. Cloudflare tunnel route points at a service name that did not exist

The remote tunnel config (managed in the Cloudflare dashboard, not the
local ConfigMap) routes `viz.dev.aws.zndx.org` to
`otel-navigator.panel-viz.svc.cluster.local:5006`. The Ansible playbook
creates the Service as `panel-viz` (not `otel-navigator`). Created an
`otel-navigator` Service in the `panel-viz` namespace selecting the same
pods. Long-term, the Cloudflare tunnel config (or the Service name)
should be aligned.

### 10. `zarf.dev/agent=ignore` on `dask` / `panel-viz` namespaces

The Ansible-deployed clusters predate the zarf deploy, and the namespaces
created by Ansible were labelled `zarf.dev/agent=ignore` so the zarf agent
would not mutate pods in them. When zarf later deployed the cybersec-dask
DaskCluster into the same `dask` namespace, the agent skipped the image
rewriting — pods kept the literal `localhost:5555/cybersec-dask:2025.2.0`
reference, which kubelet tried to pull from the literal `localhost:5555`
on each node (nothing there → `ErrImagePull`). Removed the labels manually
+ deleted the pods so the operator recreated them through the now-active
agent.

Long-term: either the Ansible playbook should not set this label, or the
zarf package's manifest should pre-empty the label.

## True air-gap readiness — what's still required

The current `aws:deploy:zarf` task assumes the control plane has internet
egress via the VPC NAT gateway. Specifically:

| Phase | Pulls from internet | True air-gap fix |
|---|---|---|
| 2b | zarf binary + init package from `github.com/zarf-dev/zarf/releases` | SCP both from laptop (where they were downloaded into the local nix store / `zarf:image` workspace) up to the CP. Add the init pkg to the zarf init/deploy assets transport tarball. |
| 2c | local-path-provisioner from `raw.githubusercontent.com/rancher/local-path-provisioner` | Either pre-stage the manifest on the CP via cloud-init (bastion userdata or a fresh `aws:provision` step), or vendor it into the zarf init component (it currently lives in the cybersec-dask component, which deploys post-init). |

Both can be solved by switching from `curl` to SCP-from-laptop in the
phases. The laptop already has the artifacts after `zarf:image` + `zarf:package`.
Followup work needed:

- **`zarf:package` should also export the init package.** Today `zarf:package`
  builds `zarf-package-cybersec-dask-amd64-1.3.0.tar.zst` but doesn't
  bundle the matching `zarf-init-amd64-v0.70.1.tar.zst`. The deploy task
  has to fetch the init pkg from GitHub separately.
- **`zarf/manifests/dask-cluster.yaml` should drop the static AWS env
  blocks** (§7). Make the env opt-in via Zarf variables that default to
  empty when targeting AWS with an instance role.
- **The Ansible `panel-viz` role should not deploy panel-viz at all on
  zarf deploys.** Right now both Ansible and zarf deploy the same
  workload — Ansible wins (because its deploy is in-progress when zarf
  attempts the upgrade), so the zarf-provided image with HoloViews et al.
  never makes it into the panel-viz pod. Either remove the panel-viz role
  from the Ansible playbook entirely (it's the zarf package's job), or
  gate it on a `panel_viz_provider != "zarf"` variable.
- **Cloudflare tunnel route name (§9)**: align Service name with the
  tunnel config. Either rename the Ansible Service to `otel-navigator`
  or update the Cloudflare tunnel config to point at `panel-viz`.

## Final cluster state (us-east-1, account 050330818249)

- 1 bastion + 1 control plane + **5 of 7** worker nodes (workers 2 and 8
  were silently skipped by the Ansible RKE2 bootstrap; both are in
  us-east-1b subnet — needs investigation; treat as 5-worker cluster).
- DaskCluster `cybersec-dask`: scheduler + 14 worker pods running
  `cybersec-dask:2025.2.0` (image rewritten from `localhost:5555` to the
  in-cluster zarf-docker-registry NodePort `127.0.0.1:31999`). All pods
  use the EC2 instance role for S3.
- DaskCluster `simple` (from Ansible, vanilla `ghcr.io/dask/dask:latest`):
  **deleted** to free memory for the panel-viz pod rollout.
- JupyterHub (from Ansible): hub/proxy services running, vanilla image
  (no HoloViews). The zarf package's JupyterHub deploy didn't take over
  because the `jupyterhub` namespace also has `zarf.dev/agent=ignore` —
  same root cause as §10. Followup.
- panel-viz (the OTEL Span Explorer): running the Ansible-deployed pod,
  but patched to (a) `--index app` so `/` serves content, (b) read
  AWS_SESSION_TOKEN for s3fs, (c) fall through to instance role when env
  is empty, (d) point at `cybersec-dask-scheduler` (was `simple-scheduler`
  which we then deleted).
- Datagen Job: 8 indexed pods writing to `s3://cybersec-dask-97dd85a8-data/otel-1t/spans/`
  via instance role. As of session end: ~5 GiB written, growing.
- Cloudflare tunnel: 2 cloudflared replicas, routes 5 hostnames. Tunnel
  ID `afa11fa7-819a-429f-8b2e-b8c3ea48e7da`.

## Bookkeeping

- Temporary SG ingress rule on the bastion: `98.97.106.202/32` (laptop
  IP at deploy time). Should be revoked after `viz` is stable since the
  WARP CGNAT range covers the intended access path.
- SSH key `cybersec-dask-97dd85a8` is imported in `us-east-1` of both
  the intended account `050330818249` AND the misroute account
  `268282262010` (left behind after the surgical destroy because zarf
  isn't aware of it). Untracked by tofu; safe to leave or `aws ec2
  delete-key-pair` manually.
- Memory entries updated: see `MEMORY.md` for the index. Notable:
  [[project_misrouted_deploy_268282262010]],
  [[feedback_provider_profile_audit]],
  [[feedback_storageclass_before_zarf_init]],
  [[project_aws_deployment_boundaries]] (re-verified).
