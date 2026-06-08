# Air-Gap Convergent Deploy — Operator Runbook

**Audience.** You have a single-node, air-gapped Kubernetes (RKE2) — possibly already carrying a
**PARTIAL or STUCK Zarf procedure**: `zarf init` half-finished, the registry up but the
zarf-agent missing, app images still pointing at `ghcr.io`/`quay.io` and `ImagePullBackOff`, a
namespace wedged in `Terminating`, or workers oversubscribing the node. The linear
`zarf init && zarf package deploy` path does not recover from these states.

**This runbook drives the deployment to target state from *any* partial-failure state — one
command, on the node, no internet — using the convergence engine.**

---

## What it is

A deterministic finite-state machine: **detect → remediate → re-detect → fixpoint**, over a
tiered catalog of invariants (node → storage → registry → images → operator → scheduler →
workloads → ingress). It is **idempotent** (`verify` is a clean no-op once at target) and
**never destroys a transported (Layer-A) artifact** — that guard is structural in the engine.
It **picks up wherever your procedure stalled**: already-good tiers detect `[ok]` and are
skipped; only the broken ones are remediated.

## The two layers

- **Layer A** — artifacts you transport IN: the deploy package, the **zarf-init package**, the
  engine bundle. Never re-fetched; the engine verifies, never pulls.
- **Layer B** — the deployment (K8s resources, registry content). Disposable; rebuilt from Layer A.

## Resilient by default (no StorageClass required)

The Zarf registry binds a claimRef hostPath PV, and the engine pre-makes that path writable
(the registry runs non-root). So a single, disk-limited node with a **bare RKE2** — no default
StorageClass, no provisioner, no bootstrap images — is the baseline. Resourced multi-node
clusters that run workloads needing dynamic PVCs can opt in with `CONVERGE_DYNAMIC_PROVISIONING=1`.

---

## 1. Prerequisites (on the node)

- An existing single-node RKE2 — `kubectl get nodes` shows it `Ready`.
- `python3` (standard library only — the engine needs no extra packages).
- `zarf` v0.70.1 on `PATH`.
- root (RKE2's kubeconfig is root-only).

## 2. Transport the release assets into the closed world

| Asset | Destination |
|-------|-------------|
| `zarf-package-cybersec-dask-amd64-1.5.0.tar.zst` | `/var/tmp/` |
| `zarf-init-amd64-v0.70.1.tar.zst` *(Layer A — the piece partial procedures most often lack)* | `/var/tmp/` (beside the deploy package) |
| `cybersec-converge-1.5.0.tar.gz` *(the engine)* | unpack to `~/cybersec-converge/` |

```bash
mkdir -p ~/cybersec-converge && tar xzf cybersec-converge-1.5.0.tar.gz -C ~/cybersec-converge
```

## 3. Converge — one command

S3 is **provided** in your environment (an endpoint + a given bucket name). Keep the secret off
the process table by passing a creds-file:

```bash
umask 077; cat > /dev/shm/s3-creds <<'EOF'
S3_ENDPOINT=<provided-endpoint>
S3_BUCKET=<given-bucket-name>
S3_REGION=<region>
S3_ACCESS_KEY=<key>
S3_SECRET_KEY=<secret>
EOF

sudo env CONVERGE_CREDS_FILE=/dev/shm/s3-creds bash ~/cybersec-converge/converge-node.sh apply
shred -u /dev/shm/s3-creds
```

The engine discovers both packages in `/var/tmp`, preps the registry hostPath, runs `zarf init`
(registry + agent + webhook), pushes the app images from the package, deploys the components,
and stops at a fixpoint. Watch for: `registry hostPath … prepared` → `zarf init: rc=0` →
`T2.images-pushed` → components → `✔ CONVERGED`.

## 4. Verify

```bash
sudo bash ~/cybersec-converge/converge-node.sh verify     # all invariants [ok] = at target
```

---

## What it resolves (partial-procedure realities)

| Symptom in a stuck procedure | The engine's action |
|------------------------------|---------------------|
| `zarf init`: "requires a zarf-init package" | runs from the directory holding the transported init package so init finds it |
| image push `500 … permission denied` | preps the registry hostPath writable (the registry runs as a non-root UID; `fsGroup` does not chown hostPath) |
| pods `ImagePullBackOff` on `ghcr.io`/`quay.io` | a fully-completed `zarf init` deploys the **agent + webhook**, which rewrite images to the internal registry |
| namespace wedged `Terminating` | force-finalizes the namespace and re-initializes the registry |
| workers oversubscribed (`Pending`) | reaps excess worker Deployments to fit the node's schedulable capacity |
| re-run of an already-good cluster | fast no-op (every tier detects `[ok]`) |

## Recovery — do NOT iterate a wedged `zarf init`

If `zarf init` itself is wedged (for example `"namespaces zarf not found"` after repeated
partial inits), **stop** — retrying corrupts cluster state further. Run `zarf destroy --confirm`
(or reprovision the node) for a clean base, then re-run converge.

## Diagnose / other modes

- `converge-node.sh dry-run` — show what *would* be remediated; changes nothing.
- `converge-node.sh verify` — read-only target oracle (exit 0 = at target).
- `converge-node.sh teardown` — clean-slate the app stack (registry + node images CONSERVED)
  for a fast redeploy.
- `CONVERGE_DYNAMIC_PROVISIONING=1` — opt in to the default-StorageClass path (multi-node).

## Exit codes

`0` = converged / clean verify. `1` = not converged (see the table). `2` = a CLOSURE violation
— a transported Layer-A artifact is missing; re-transport it (the engine will not pull).
