# cybersec — task runner (migrating from `devenv tasks` to `just`)
# Recipes run under bash; each line is its own shell, so a failing step halts the recipe.
set shell := ["bash", "-euo", "pipefail", "-c"]

_root    := justfile_directory()
_sb      := _root / "infra/aws/tofu-sandbox/run-sandbox.sh"
_hydrate := "uv run python " + (_root / "infra/aws/tofu-sandbox/hydrate.py")

# List available recipes
default:
    @just --list

# ----------------------------------------------------------------------------
# Air-gap convergence SANDBOX — throwaway AWS rehearsal of the resilient deploy.
# Config: config/reference.conf `cybersec.sandbox` (override per-dev via SANDBOX_* env).
# Per-dev state (tofu state, generated key, /32) -> build/sandbox (gitignored).
# Full guide: infra/aws/tofu-sandbox/RUNBOOK.md
# ----------------------------------------------------------------------------

# Hydrate HOCON cybersec.sandbox -> build/sandbox/{tfvars,env}; auto-detect /32
sandbox-config:
    {{_hydrate}}

# Full rehearsal: provision -> transport -> cut egress -> converge -> verify
sandbox: sandbox-config
    {{_sb}} up
    {{_sb}} transport
    {{_sb}} airgap
    {{_sb}} converge
    {{_sb}} verify

# Provision a fresh node (egress ON) and wait for RKE2 Ready
sandbox-up: sandbox-config
    {{_sb}} up

# scp packages + stage engine + deploy MinIO (egress ON; fetches zarf-init if missing)
sandbox-transport:
    {{_sb}} transport

# Cut egress -> closed world (confirms outbound is blocked)
sandbox-airgap:
    {{_sb}} airgap

# Restore egress
sandbox-online:
    {{_sb}} online

# The one-command resilient deploy (S3 creds kept off argv)
sandbox-converge:
    {{_sb}} converge

# converge --verify + pod / agent / ingress check
sandbox-verify:
    {{_sb}} verify

# Clean-slate the app stack (registry + node images conserved)
sandbox-teardown:
    {{_sb}} teardown

# Tear the sandbox down (stops the meter)
sandbox-destroy:
    {{_sb}} destroy

# ssh to the node, optionally running a command: just sandbox-ssh "kubectl get pods -A"
sandbox-ssh *args:
    {{_sb}} ssh {{args}}

# Bootstrap status + node readiness
sandbox-status:
    {{_sb}} status

# Print the node public IP
sandbox-ip:
    {{_sb}} ip
