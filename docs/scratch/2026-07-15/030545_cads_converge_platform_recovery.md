# CADS: Anticipatory Air-Gap Converge Platform Recovery

**Date:** 2026-07-15  
**Branch:** `rch/devenv` (landing pad; cut `zarf-v1.6.4` when validated)  
**Framing:** Buckminster Fuller’s *Comprehensive Anticipatory Design Science* —
design for the preferred future (operator runs `converge-node.sh apply` once on a
wedged closed-world node) by encoding generalized principles, not one-off scripts.

## Preferred future

An operator who only has the release bundle and a half-destroyed RKE2 never has to
hand-sequence finalizer patches, namespace recreates, or hostPath chmod. The FSM
names the unmet condition and remediates to a fixpoint while conserving Layer A.

## Generalized principles (from the session)

1. **Conservation** — never delete transported images or `/var/lib/zarf-registry` data.
2. **Empty-class attractor** — resilient registry storage is always `storageClassName:""`
   + claimRef hostPath PV; default SC is hygiene to remove, not a dependency.
3. **Drain before finalize** — force-finalize only on empty namespaces (else husks).
4. **Recreate-ns before namespaced write** — split-brain PVC/ns: create ns, then patch.
5. **Release mounts before PVC delete** — `pvc-protection` is a relationship problem.
6. **Diagnose, don’t bare-rc** — every failed init reports PVC/PV/hostPath/husk/tools.
7. **Tools are Layer A** — missing `zarf` / init package is CLOSURE, not T1 mystery.

## What shipped in this iteration

| Area | Change |
|------|--------|
| `catalog.py` | hostPath prep; ns recreate; full zarf drain; husk detect; PVC mount release; seed unwedge + retry init; richer diagnose; T0.layer-a-zarf-tools; T5 hub-db orphan |
| `converge-node.sh` | comment fix: secrets via ZARF_CONFIG not bare ZARF_VAR_* |
| `test-fsm.sh` | +3 inducers: husk Service, PVC/ns split-brain, hostPath 0700 |
| `release-bundle.sh` | checksum cheatsheet + DISCOVERY + REMEDIATION; default VER 1.6.4 |
| `AIRGAP-CHEATSHEET.md` | in-tree, release-bound |
| Runbook App D | anticipatory table |

## Validation path

```bash
# import / unit-level
PYTHONPATH=zarf python3 -c 'from converge.catalog import build_catalog; print(len(build_catalog()))'

# live matrix (needs sandbox credentials)
just sandbox-test-fsm
```

## Not in this PR (still anticipatory backlog)

- JupyterHub static hostPath PVC for fully SC-less clusters (today: dynamic or orphan-delete)
- Automatic kubelet image-GC policy apply (still Layer-A / ansible)
- In-cluster Job packaging of the engine
