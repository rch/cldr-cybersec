# Converge & verification

Converge is the **idempotent reconciler** for the air-gap Zarf stack: discover cluster state, compare to a catalog of invariants, remediate Layer-B only, never prune transported Layer-A images.

## Commands

```bash
# On the node (root; RKE2 kubeconfig)
sudo bash zarf/scripts/converge-node.sh verify     # oracle only
sudo bash zarf/scripts/converge-node.sh apply      # remediate to fixpoint
sudo bash zarf/scripts/converge-node.sh dry-run
sudo bash zarf/scripts/converge-node.sh teardown   # app ns only; registry/images conserved
```

## What verify tells you

- Status table per tier (T0 node → T6 ingress)  
- **LIVE STATE** for panel apps (ConfigMap bucket/path, secret key presence, pod issues)  
- **IN SITU** DISCOVER + FIX blocks (copy-paste on the node)  
- Trailing **S3 datapath** report when `verify-s3-datapath.sh` is staged  

## Panel / OTel UI failures

| Symptom | Likely rem |
|---------|------------|
| HTTP 200, empty UI, `OTEL_DATA_PATH=s3:///` | Config-only CM/Secret patch + rollout (apply with S3_*) |
| Bokeh `Token is expired` | Longer `--session-token-expiration`; new browser session |
| WS opens then **1005** | Pod logs / OOM / tunnel — not S3 alone |
| Auth OK, no parquet | Data generation / marker — `verify-s3-datapath.sh` |

## Package optional?

| Goal | Need full `.tar.zst`? |
|------|------------------------|
| Verify + config-only S3 fix | **No** — stage `converge/` + scripts (~400 KB) |
| New components / image push | **Yes** |

## Source

- Engine: `zarf/converge/` (catalog, manual recipes, platform)  
- Manual command mirror: `zarf/AIRGAP-REMEDIATION-COMMANDS.md`  
