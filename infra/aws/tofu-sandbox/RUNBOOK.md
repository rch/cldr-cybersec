# Air-Gap Convergent Deploy — AWS Rehearsal Runbook

**Purpose.** Rehearse the COMPLETE resilient air-gap deploy in a throwaway AWS sandbox so
you understand every step before the real air-gap environment. The whole rehearsal is one
command — `just sandbox` — but this runbook walks each phase so you know exactly what it
does and how it maps to the real air-gap (marked ▶ REAL).

Validated 2026-06-06: one command, fully air-gapped, bare RKE2 → all 14 convergence
invariants green (dask + panel-viz + jupyterhub + sample notebooks), idempotent on re-run.

---

## 0. Mental model (read once)

- **Two layers.** *Layer A* = transported artifacts you carry IN (deploy package, the
  zarf-INIT package, the converge engine) — never re-fetched. *Layer B* = the deployment
  (K8s resources, registry content) — disposable; converge rebuilds it.
- **Resilient storage = NO StorageClass.** The Zarf registry binds a claimRef hostPath PV;
  the engine pre-makes that hostPath writable (the registry runs as UID 1000). No
  provisioner, no bootstrap images, no SC.
- **The closed world MUST contain the zarf-INIT package** (registry/agent/injector images),
  not just the deploy package — air-gapped `zarf init` fails without it. (The rehearsal
  auto-fetches it; in real air-gap it ships in your bundle.)
- **Never iterate `zarf init` on a live node.** If init wedges, reprovision — the tooling
  makes that one command. Repeated partial init+delete cycles corrupt cluster state.

---

## 1. Prerequisites (laptop, with internet)

```bash
aws sts get-caller-identity --query Account --output text     # must be 050330818249
just --version                                                # task runner (in devenv.nix)
ls zarf/zarf-package-cybersec-dask-amd64-1.4.0.tar.zst        # deploy package
#   build/rebuild it (bakes in the latest manifests):  cd zarf && zarf package create --confirm
```
The **zarf-init package is fetched automatically** by `just sandbox-transport`.
▶ REAL: it must be in your physical transport bundle — there's no internet on Monday.

---

## 2. Configuration — repeatable for any developer (HOCON)

Every sandbox setting lives in `config/reference.conf` under **`cybersec.sandbox`** (region,
instance type, disk, zarf version, the rehearsal S3). Override per-dev WITHOUT editing any
committed file, via `SANDBOX_*` env vars:

```bash
export SANDBOX_INSTANCE_TYPE=m7i.4xlarge   # bigger node
export SANDBOX_SSH_CIDR=1.2.3.4/32         # pin your /32 (else auto-detected each run)
export SANDBOX_S3_BUCKET=my-otel           # the deploy-time bucket name
```

All per-developer artifacts — tofu state, your generated SSH key, your resolved /32 — are
written under **`build/sandbox/` (gitignored)**. So the same committed tooling works for
everyone with their own keys and IPs; nothing secret is ever committed.

---

## 3. The whole rehearsal — one command

```bash
just sandbox        # config → up → transport → airgap → converge → verify
```
Ends with all 14 invariants `[ok]` and the stack Running. The phases below explain each step
and let you run them one at a time (and re-run any of them).

---

## 4. Phase by phase

### `just sandbox-config` — hydrate the config
Resolves `cybersec.sandbox` (+ your `SANDBOX_*` overrides), auto-detects your egress /32, and
writes `build/sandbox/{sandbox.auto.tfvars, sandbox.env}`. Run it alone to preview values.
▶ REAL: same — environment-driven, no per-site code changes.

### `just sandbox-up` — provision (egress ON)
Account-guarded `tofu apply` (state under `build/sandbox/`): one amd64 node, RKE2 via
user-data, your /32 for SSH; waits for the node Ready. Confirm the **pristine baseline**:
```bash
just sandbox-ssh "sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml get sc,ns | grep -iE 'storageclass|zarf' || echo PRISTINE"
```
Expect no default StorageClass and no `zarf` ns.
▶ REAL: **SKIP** — you're handed an existing single-node RKE2; just run the baseline check on it.

### `just sandbox-transport` — assemble the closed world (egress ON)
scps the deploy package + (auto-fetched) zarf-init package to `/var/tmp`, stages the converge
engine under `~/cybersec-converge`, and deploys in-cluster MinIO (the "provided" S3 stand-in;
its image is pulled now, before the egress cut).
▶ REAL: carry the packages (incl. the zarf-init package) on media; S3 is the provided
endpoint/bucket — set them via `SANDBOX_S3_*`.

### `just sandbox-airgap` — seal the closed world
Removes the SG egress rule and confirms the node's outbound is **BLOCKED** (inbound SSH
survives — security groups are stateful).
▶ REAL: already sealed — just confirm BLOCKED.

### `just sandbox-converge` — the one command
Runs `converge-node.sh apply` on the node, fully air-gapped. S3 creds go to a node tmpfs file
(never on argv). Watch for, in order: `registry hostPath … prepared` → `zarf init: rc=0`
(registry + agent + webhook) → `T2.images-pushed` → components → `✔ CONVERGED`.
▶ REAL: identical — the provided S3 values flow in the same way.

### `just sandbox-verify` — prove green
`converge --verify` (all 14 `[ok]`, idempotent) plus a pod / agent / ingress check.

---

## 5. Clean-slate + redeploy (the first air-gap phase gate)

```bash
just sandbox-teardown     # remove the app stack (registry + node images CONSERVED)
just sandbox-converge     # fast redeploy — no re-init, no re-push
just sandbox-verify
```

## 6. Tear down (stop the meter)

```bash
just sandbox-destroy      # account-guarded tofu destroy; clears build/sandbox state
```

## 7. If something goes sideways

- `just sandbox-status` — bootstrap status + node readiness.
- `just sandbox-ssh "<cmd>"` — poke around (e.g. `kubectl get pods -A`).
- `just sandbox-online` — temporarily restore egress (pull an extra image), then `just sandbox-airgap` again.
- **`zarf init` wedged?** Do NOT iterate — `just sandbox-destroy` then `just sandbox` for a
  fresh node. (The thrashing lesson: repeated partial inits corrupt cluster state.)

---

## Appendix A — Rehearsal vs. real air-gap

| Step | Rehearsal | Real air-gap (Monday) |
|------|-----------|------------------------|
| Node | `just sandbox-up` provisions | existing single-node RKE2 (skip up) |
| Transport | `sandbox-transport` (scp + auto-fetch init pkg) | physical media incl. the **zarf-init package** |
| S3 | in-cluster MinIO (`sandbox-transport`) | provided endpoint + given bucket (`SANDBOX_S3_*`) |
| Egress | `sandbox-airgap` toggles it | already sealed — confirm BLOCKED |
| Deploy | `sandbox-converge` | the same `converge-node.sh apply` on the node |
| Recover | `sandbox-destroy` + `sandbox` | `zarf destroy` / a fresh node |

## Appendix B — Gotchas the tooling already handles (recognize the symptoms)

1. **Missing zarf-init package** → `zarf init` errors "requires a zarf-init package".
   (transport ships/auto-fetches it; the engine runs from its directory so init finds it.)
2. **Root-owned registry hostPath** → image push 500s "permission denied" (registry runs
   non-root). (`converge-node.sh` chmods `/var/lib/zarf-registry` before init.)
3. **Agent/webhook absent** (an unwritable registry can't be seeded) → component images keep
   their `ghcr.io`/`quay.io` refs and time out. (Fixed by #2 — a writable registry lets
   `zarf init` fully complete, agent included, so images get rewritten to the internal registry.)
4. **Leftover default StorageClass** → can hijack the registry PVC away from the claimRef PV.
   (The Phase-`up` baseline check flags it; remove its `is-default-class` annotation if present.)
5. **Thrashed state** from iterating `zarf init` after a partial failure → reprovision; don't fight it.
