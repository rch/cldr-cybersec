# Air-Gap Walkthrough Cheatsheet (v1.6.3)

One page on deck. Depth lives in the runbook (Parts II–III), DISCOVERY, and
REMEDIATION-COMMANDS. Everything below is copy-paste, ordered, and assumes root on the node.

```bash
export KUBECONFIG=/etc/rancher/rke2/rke2.yaml
kc() { /var/lib/rancher/rke2/bin/kubectl --kubeconfig "$KUBECONFIG" "$@"; }
cri() { /var/lib/rancher/rke2/bin/crictl --runtime-endpoint unix:///run/k3s/containerd/containerd.sock "$@"; }
PKG=$(ls -t /var/tmp/zarf-package-cybersec-dask-amd64-*.tar.zst 2>/dev/null | head -1); echo "PKG=$PKG"
```

## 0. Ground rules (the remediation cycle)

- **Observe → act once → re-observe.** Never run step 2 off step 1's observation.
  The oracle after every manual action: `sudo bash ~/cybersec-converge/converge-node.sh verify`
- **Relationships before actions**: ownerReferences (fix the OWNER — CR, not Deployment, not
  Pod), finalizers (what blocks deletion), label selectors (webhooks/services bind by label),
  UIDs (same name ≠ same object).
- **Order**: build **ascends** (storage → registry/agent → images → operator → workloads);
  teardown **descends** (pods → objects → namespace finalize LAST, only when empty).
- **Immutable ⇒ delete-then-recreate**, never patch: captured PVC class, kopf CR children.
- **Conservation**: never `crictl rmi`/prune; never delete `/var/lib/zarf-registry` data
  (except runbook corrupt-registry recovery); deleting PVC/PV *objects*, pods, CRs is fine.

## 1. STEP ZERO — disk + kubelet policy (BEFORE transport; 20 GiB ≈ inside default GC range)

Default kubelet **image-GC fires at 85% disk** and will silently delete transported images.

```bash
df -h /var/lib/rancher /var/tmp /var/lib
grep -A6 'kubelet-arg' /etc/rancher/rke2/config.yaml 2>/dev/null || echo "DEFAULTS — apply the block below NOW"
grep -q 'image-gc-high-threshold' /etc/rancher/rke2/config.yaml 2>/dev/null || cat >> /etc/rancher/rke2/config.yaml <<'EOF'
kubelet-arg:
  - "eviction-hard=imagefs.available<2%,nodefs.available<2%,nodefs.inodesFree<2%,memory.available<100Mi"
  - "eviction-minimum-reclaim=imagefs.available=1%,nodefs.available=1%"
  - "image-gc-high-threshold=100"
  - "image-gc-low-threshold=99"
EOF
systemctl restart rke2-server   # does not disrupt running pods; MERGE if kubelet-arg already exists
```

## 2. Sitrep (run once before converge; paste to remote consult)

```bash
kc get nodes; df -h /var/lib/rancher | tail -1
kc get ns --show-labels | grep -E 'dask|panel-viz|jupyterhub|zarf|agent=ignore'
kc get sc -o custom-columns='NAME:.metadata.name,PROV:.provisioner,BIND:.volumeBindingMode,DEFAULT:.metadata.annotations.storageclass\.kubernetes\.io/is-default-class'
kc get pv,pvc -A 2>/dev/null | head -12
kc -n zarf get pods 2>/dev/null || echo "no zarf ns"
kc get secrets -A -l 'owner=helm,status in (pending-install,pending-upgrade,pending-rollback)' --no-headers 2>/dev/null | grep . || echo "no wedged helm"
kc get pods -A | grep -vE 'Running|Completed' | head -10
ls -lh /var/tmp/zarf-*.tar.zst; command -v zarf && zarf version | head -1
```

## 3. Pre-converge gate

| Check | Must be |
|---|---|
| node `Ready`, not cordoned | yes |
| kubelet policy applied (§1) | yes — BEFORE transport |
| deploy pkg + **zarf-init pkg** in `/var/tmp` | both; **only ONE deploy-pkg version** |
| `zarf` binary v0.70.1 | `/usr/local/bin/zarf` |
| free disk | ≥ 12 GiB headroom for the deploy |
| S3 endpoint/bucket/creds in hand | creds-file ready |

## 4. Converge — detached (a dropped session must never kill it) — then verify

```bash
umask 077; cat > /dev/shm/s3-creds <<'EOF'
S3_ENDPOINT=<endpoint>
S3_BUCKET=<bucket>
S3_REGION=<region>
S3_ACCESS_KEY=<key>
S3_SECRET_KEY=<secret>
EOF
sudo setsid bash -c 'env CONVERGE_CREDS_FILE=/dev/shm/s3-creds \
  bash ~/cybersec-converge/converge-node.sh apply; echo $? > /var/tmp/converge.rc' \
  </dev/null >> /var/tmp/converge.log 2>&1 &
tail -f /var/tmp/converge.log        # re-tail freely; rc lands in /var/tmp/converge.rc
sudo bash ~/cybersec-converge/converge-node.sh verify && shred -u /dev/shm/s3-creds
```

**Progress rule:** check every 5–15 min for *visible movement* (new log lines, pod transitions —
`kc get pods -A | grep -v Running`). Silence ≠ stuck during image push/helm waits (10–20 min is
normal for T1/T2); two flat checks with no pod movement ⇒ investigate, don't wait.

## 5. Friction → the engine names the tier; go by tier

Read the FAIL line in the table — it names the unmet condition. Then:

| Tier / diagnosis says | Discover (IDs/relationships) | Remediate (order!) |
|---|---|---|
| **T0** api/node | `systemctl status rke2-server`; `journalctl -u rke2-server -n 50` | restart rke2-server; uncordon |
| **T0** disk-pressure | `df -h`; `kc describe node \| grep -A5 Taints` | free SAFELY: `journalctl --vacuum-size=200M`; delete Failed pods; NEVER rmi |
| **T0.5/T1** registry/PVC | `kc -n zarf get pvc,pv -o wide`; PVC `phase` + `storageClassName`; PV `claimRef.uid` | ① delete captured PVC ② reset PV to `""` ③ `cd /var/tmp && zarf init --confirm --storage-class -` |
| **T1** agent / poison | `kc get ns --show-labels \| grep agent=ignore`; canary: `kc -n dask-operator run zz --image=ghcr.io/x/y:v1 --restart=Never --dry-run=server -o jsonpath='{.spec.containers[0].image}'` (must come back REWRITTEN) | ① strip labels: `for ns in dask dask-operator panel-viz jupyterhub; do kc label ns $ns zarf.dev/agent- --overwrite; done` ② delete ImagePull-stuck pods with upstream (non-`127.0.0.1:`) refs ③ re-verify canary |
| **T1/T3+** helm "operation in progress" | `kc get secrets -A -l 'owner=helm,status in (pending-install,pending-upgrade,pending-rollback)'` — note ns + highest `version` | delete that ONE latest pending secret → redeploy component |
| **T2** images | `zarf tools registry catalog`; pod events: which HOST failed the pull? (`ghcr.io` = rewrite problem → T1 poison row; `127.0.0.1` = registry content → re-push) | `zarf package deploy "$PKG" --confirm --components=cybersec-images --retries 10` (+ S3 setup below) |
| **T4/T5** dask stale/stranded | CR vs pod env lengths: `kc -n dask exec deploy/cybersec-dask-scheduler -- sh -c 'echo ${#AWS_ACCESS_KEY_ID}'` vs the CR's env; scheduler Deployment missing under live CR? | **CR-level only**: ① `kc -n dask delete daskcluster cybersec-dask --wait=false` ② clear finalizer if it lingers ③ redeploy `dask-cluster` |
| **T5** ns Terminating / husks | `kc get ns` phase; **husk signature**: Deployments present + ZERO pods; object ages OLDER than the ns = resurrection | Terminating: drain contents → pods → finalize ONLY when empty. Husk: `kc delete ns <ns>` (normal delete works) → redeploy |
| any manual `zarf package deploy` | — | secrets via `ZARF_CONFIG` tmpfs file, NON-secrets via `--set-variables` — **never bare `ZARF_VAR_*` env** (renders empty). Block: runbook Part II §6 |

**Golden rule for T1-class trouble:** just re-run `converge-node.sh apply` — it unwinds all of the
above itself; the manual rows are for surgical control or when the engine can't run.

## 6. After CONVERGED — data + app + terminal

```bash
# data (if bucket empty): JupyterHub → OTEL_Data_Generator.ipynb → run all; then prove it:
kc -n dask exec deploy/cybersec-dask-scheduler -- python -c "import os,s3fs; fs=s3fs.S3FileSystem(key=os.environ.get('AWS_ACCESS_KEY_ID') or None, secret=os.environ.get('AWS_SECRET_ACCESS_KEY') or None, client_kwargs={'endpoint_url': os.environ.get('S3_ENDPOINT') or None}); print('marker:', fs.exists(os.environ.get('S3_BUCKET','')+'/_active_dataset.json'))"
# app + terminal wires (on the node):
curl -s -o /dev/null -w 'app: %{http_code}\n' -H 'Host: panel.cybersec.local' http://localhost/otel-navigator
curl -s -i -N --max-time 6 -H 'Host: panel.cybersec.local' -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' -H 'Sec-WebSocket-Version: 13' http://localhost/ws | head -1   # expect 101
# NodePort access instead of ingress? re-apply with: PTY_PROXY_WS=ws://<node>:30765
```

## 7. Escalation ladder (evidence first — "converge didn't fix it" is not evidence)

1. Re-read the engine's diagnosis line (it names the unmet condition).
2. Re-run `apply` once (multi-pass fixpoint may need settling).
3. Surgical row from §5 → `verify` after each single action.
4. `converge-node.sh teardown` → `apply` (clean-slate the app stack; registry/images conserved).
5. Only for below-Layer-B evidence (rke2/etcd/kubelet errors in journalctl, API down):
   `zarf destroy --confirm` → re-init → `apply`.

## 8. When consulting remotely, paste me

① the §2 sitrep · ② the converge status table (from `/var/tmp/converge.log`, the block after
`TIER STATUS INVARIANT`) · ③ for a stuck pod: `kc -n <ns> describe pod <pod> | sed -n '/Events:/,$p'`
· ④ for a helm wedge: the §5 helm-secrets line · ⑤ `df -h /var/lib/rancher | tail -1` with every paste.
Never paste creds — lengths only (`echo ${#S3_SECRET_KEY}`).
