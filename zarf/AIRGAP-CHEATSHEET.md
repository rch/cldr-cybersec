# Air-Gap Walkthrough Cheatsheet (v1.6.4+)

One page on deck. Depth: [AIRGAP-CONVERGE-RUNBOOK.md](AIRGAP-CONVERGE-RUNBOOK.md)
(Parts II–III), [AIRGAP-DISCOVERY.md](AIRGAP-DISCOVERY.md),
[AIRGAP-REMEDIATION-COMMANDS.md](AIRGAP-REMEDIATION-COMMANDS.md).

Everything below is copy-paste, ordered, root on the control-plane node.

```bash
export KUBECONFIG=/etc/rancher/rke2/rke2.yaml
kc() { /var/lib/rancher/rke2/bin/kubectl --kubeconfig "$KUBECONFIG" "$@"; }
cri() { /var/lib/rancher/rke2/bin/crictl --runtime-endpoint unix:///run/k3s/containerd/containerd.sock "$@"; }
PKG=$(ls -t /var/tmp/zarf-package-cybersec-dask-amd64-*.tar.zst 2>/dev/null | head -1); echo "PKG=$PKG"
```

## 0. Ground rules

- **Observe → act once → re-observe.** Oracle: `sudo bash ~/cybersec-converge/converge-node.sh verify`
- **Relationships before actions**: ownerReferences, finalizers, label selectors, UIDs.
- **Order**: build **ascends** (storage → registry → images → workloads);
  teardown **descends** (pods → objects → namespace finalize LAST, only when empty).
- **Immutable ⇒ delete-then-recreate** (PVC storageClassName, kopf CR children).
- **Conservation**: never `crictl rmi`/prune; never delete `/var/lib/zarf-registry` data
  (except documented corrupt-registry recovery).

## 1. STEP ZERO — disk + kubelet (BEFORE transport)

Default kubelet **image-GC fires at 85%** and **%-based eviction** on large disks
triggers `DiskPressure` with tens of GiB still free (e.g. 5% of 900G ≈ 45G).
Use **absolute** free-space thresholds + raised image-GC:

```bash
df -h / /var/lib/rancher /var/tmp
sudo tee /etc/rancher/rke2/config.yaml >/dev/null <<'EOF'
# Absolute free-space (not %): schedulable with ordinary free headroom on large disks.
# Image GC raised so transported Layer-A images are not pruned.
kubelet-arg:
  - "eviction-hard=nodefs.available<5Gi,imagefs.available<5Gi,nodefs.inodesFree<1%,memory.available<100Mi"
  - "eviction-soft=nodefs.available<10Gi,imagefs.available<10Gi,memory.available<200Mi"
  - "eviction-soft-grace-period=nodefs.available=5m,imagefs.available=5m,memory.available=2m"
  - "eviction-minimum-reclaim=nodefs.available=1Gi,imagefs.available=1Gi"
  - "image-gc-high-threshold=100"
  - "image-gc-low-threshold=99"
EOF
sudo systemctl restart rke2-server   # does not kill running pods (containerd keeps them)
# Expect: DiskPressure=False, no disk-pressure taint, once free space ≥ ~10Gi
```

## 2. Sitrep (paste for remote consult)

```bash
kc get nodes; df -h /var/lib/rancher | tail -1
kc get ns --show-labels | grep -E 'dask|panel-viz|jupyterhub|zarf|agent=ignore'
kc get sc -o custom-columns='NAME:.metadata.name,PROV:.provisioner,DEFAULT:.metadata.annotations.storageclass\.kubernetes\.io/is-default-class'
kc get pv,pvc -A 2>/dev/null | head -20
kc -n zarf get pods,svc,pvc 2>/dev/null || echo "no zarf ns"
kc get secrets -A -l 'owner=helm,status in (pending-install,pending-upgrade,pending-rollback)' --no-headers 2>/dev/null | grep . || echo "no wedged helm"
kc get pods -A | grep -vE 'Running|Completed' | head -10
ls -lh /var/tmp/zarf-*.tar.zst; command -v zarf && zarf version | head -1
ls -lad /var/lib/zarf-registry; stat -c '%a' /var/lib/zarf-registry 2>/dev/null
```

## 3. Pre-converge gate

| Check | Must be |
|---|---|
| node Ready, not cordoned | yes |
| kubelet policy applied (§1) | yes — BEFORE transport |
| deploy pkg + **zarf-init pkg** in `/var/tmp` | both; **only ONE deploy-pkg version** |
| `zarf` binary v0.70.x | `/usr/local/bin/zarf` |
| free disk | ≥ 12 GiB headroom |
| S3 endpoint/bucket/creds | creds-file ready |
| hostPath registry | `mkdir -p /var/lib/zarf-registry && chmod 0777 …` |

## 4. Converge — detached, then verify

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
tail -f /var/tmp/converge.log
sudo bash ~/cybersec-converge/converge-node.sh verify && shred -u /dev/shm/s3-creds
```

**Progress:** check every 5–15 min for movement. Silence during image push/helm (10–20 min)
is normal; two flat checks with no pod movement ⇒ investigate.

## 5. Friction → engine names the tier

| Diagnosis | Discover | Remediate (order!) |
|---|---|---|
| **T0** api/node | `systemctl status rke2-server` | restart; uncordon |
| **T0** disk-pressure | `df -h`; node taints | vacuum journald; delete Failed pods; NEVER rmi |
| **T0** zarf tools | `command -v zarf`; `ls /var/tmp/zarf-init-*` | transport Layer A |
| **T0.5/T1** registry/PVC | PVC phase+sc; PV claimRef; hostPath mode | engine: mounts→finalizers→recreate ns if split-brain→`--storage-class -` |
| **T1** husk zarf ns | `svc/zarf-injector` age days, no Ready registry | drain ns contents; re-init |
| **T1** seed deadline | Helm pending; FailedMount; hostPath 0700 | chmod 0777 hostPath; unwedge helm; re-init |
| **T1** agent / poison | `zarf.dev/agent=ignore`; canary dry-run | strip labels; re-init; recycle bad pods |
| **T1/T3+** helm pending | secrets `owner=helm,status=pending-*` | delete ONE latest pending secret |
| **T5** hub-db local-path | `hub-db-pv` / hub PVC Pending | engine deletes orphan PVC/PV + redeploys hub |
| manual deploy | — | secrets via `ZARF_CONFIG`; non-secrets via `--set-variables` |

**Golden rule:** re-run `converge-node.sh apply` first — it unwinds the rows above.

## 6. After CONVERGED — data + app + terminal

```bash
# seed empty bucket via JupyterHub → OTEL_Data_Generator.ipynb; then:
kc -n dask exec deploy/cybersec-dask-scheduler -- python -c "import os,s3fs; fs=s3fs.S3FileSystem(key=os.environ.get('AWS_ACCESS_KEY_ID') or None, secret=os.environ.get('AWS_SECRET_ACCESS_KEY') or None, client_kwargs={'endpoint_url': os.environ.get('S3_ENDPOINT') or None}); print('marker:', fs.exists(os.environ.get('S3_BUCKET','')+'/_active_dataset.json'))"
curl -s -o /dev/null -w 'app: %{http_code}\n' -H 'Host: panel.cybersec.local' http://localhost/otel-navigator
curl -s -i -N --max-time 6 -H 'Host: panel.cybersec.local' -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' -H 'Sec-WebSocket-Version: 13' \
  http://localhost/ws | head -1   # expect 101
# NodePort? re-apply with: PTY_PROXY_WS=ws://<node>:30765
```

## 7. Escalation ladder

1. Re-read the engine diagnosis line (names the unmet condition).
2. Re-run `apply` once (multi-pass fixpoint).
3. Surgical row from §5 → `verify` after each single action.
4. `converge-node.sh teardown` → `apply` (app stack only; registry conserved).
5. Below Layer-B only (rke2/etcd evidence): `zarf destroy --confirm` → re-init → apply.

## 8. Remote paste protocol

① §2 sitrep · ② converge status table (`TIER STATUS`) · ③ stuck pod Events ·
④ helm pending line · ⑤ `df -h /var/lib/rancher | tail -1` every paste.
Never paste creds — lengths only (`echo ${#S3_SECRET_KEY}`).
