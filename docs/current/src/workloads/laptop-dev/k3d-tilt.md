# Laptop dev: zarf-style stack on k3d with Tilt

A fast inner-loop for the OTEL Navigator app (`navigator-engine` + `panel-viz`)
on a laptop. It stands up the **same workloads the air-gap Zarf deploy ships**,
on a local **k3d** cluster, with **Tilt** live-reloading the two app Deployments.

This is the **laptop "local"** — distinct from the two other targets:

| Target | What | Tooling |
|---|---|---|
| **Laptop local (this doc)** | k3d + Tilt, manifest-direct, fast UI loop | `just dev-*` |
| Workstation local | RKE2 on a GPU box | `KUBECONFIG=~/.kube/rke2.yaml tilt up` |
| Air-gap deploy | Zarf package + convergence engine | `just sandbox` / release |

It is **manifest-direct**: it applies the same manifests (Dask operator +
`DaskCluster`, engine, panel-viz) without running `zarf init` / `zarf package
deploy`. The real-Zarf path stays the RKE2/air-gap domain.

## Prerequisites

- `devenv up` running (provides **MinIO** at `localhost:9010`, bound `0.0.0.0`).
  The k3d stack reuses it as its S3 backend — pods reach it via
  `host.k3d.internal:9010`.
- `k3d`, `helm`, `tilt`, `uv`, and a container builder (`podman`/`docker`) — all
  provided by the devenv shell.

## One command

```bash
just dev-up
```

Which runs, in order (each is also a standalone recipe):

| Step | Recipe | What |
|---|---|---|
| preflight | — | verifies MinIO is live at `localhost:9010` |
| cluster | `just dev-cluster` | k3d cluster `cybersec-dev` (registry-less), traefik/servicelb off, NodePort host-maps |
| image | `just dev-image` | builds `cybersec-dask:dev`, `k3d image import` (Dask baseline; ~1.3 GiB, one-time) |
| deploy | `just dev-deploy` | namespaces, `otel-navigator-config`/`-credentials`, Dask operator (bundled chart) + `DaskCluster`, the engine/navigator Services; **auto-detects the in-cluster route to MinIO** |
| seed | `just dev-seed` | `generate-otel-data.py --mode minimal` into the MinIO bucket `cybersec-dask-data` |
| tilt | `just dev-tilt` | `tilt up` — builds the app image and `k3d image import`s it, then live-reloads `otel-navigator` + `navigator-engine` |

## Access

- **Panel app**: <http://localhost:15006/otel-navigator> (Tilt port-forward) or
  <http://localhost:30506/otel-navigator> (NodePort host-map).
- **Terminal WS**: `:18765` (Tilt) / `:30765` (NodePort).
- **Dask dashboard**: `:30087` (NodePort host-map).
- **Tilt UI**: <http://localhost:10350>.

Edit `zarf/images/otel-navigator.py`, `cybersec/engine/`, or
`cybersec/pty_proxy/` → Tilt syncs into the running pod in ~3–5s. Editing the
Dockerfile or `requirements-airgap.txt` triggers a full rebuild + push.

## Image delivery (registry-less)

This path is **registry-less** — a deliberate choice for k3d-on-podman, where the
k3d-managed registry attaches to a `bridge` network podman doesn't provide, and
pushing to a host-published registry from the podman VM is unreliable. Instead:

- **Dask pods** (not Tilt-managed) use `cybersec-dask:dev`, built once and loaded
  with `k3d image import` (`just dev-image`).
- **App pods** (Tilt-managed): the Tiltfile runs in `disable_push` mode when
  `TILT_K3D_IMPORT=<cluster>` is set (by `just dev-tilt`), so `build-and-push.sh`
  builds locally and `k3d image import`s straight into the cluster. `.py` edits
  still live-sync into the running pod (no rebuild).

With `TILT_K3D_IMPORT` unset, the Tiltfile uses the Zarf NodePort registry
`127.0.0.1:31999` — so the **workstation RKE2 path is unchanged**.

## Networking on podman (macOS) — two automatic fixes

`just dev-cluster` handles two podman-specific gaps so the path is turn-key:

1. **k3d host-gateway**: k3d injects `host.k3d.internal:host-gateway`, which podman
   can't resolve until told the host-internal IP. The script writes a one-time,
   reversible drop-in inside the podman machine
   (`/etc/containers/containers.conf.d/99-k3d-hostgw.conf`) with the IP podman
   itself reports for `host.containers.internal`.
2. **In-cluster S3 route**: on podman-mac, `host.k3d.internal` resolves to the
   k3d-network gateway — which is **not** the macOS host where MinIO runs. So
   `dev-deploy` **probes** the candidates and picks the one that actually reaches
   MinIO (here: `host.containers.internal` / `192.168.127.254`). Override with
   `DEV_S3_ENDPOINT_CLUSTER`.

## Teardown

```bash
just dev-down     # deletes the k3d cluster + registry; devenv MinIO + data are kept
```

## Notes / gotchas

- **MinIO must bind `0.0.0.0`** (it does, per `devenv.nix` `services.minio`) so
  pods reach it via the auto-detected host alias.
- The seeder writes `s3://cybersec-dask-data/otel-minimal/spans/date=…/hour=…/`
  plus `_active_dataset.json`; the app discovers the active dataset from that
  marker (nothing hardcoded).
- Override defaults via env: `DEV_DASK_WORKERS`, `DEV_S3_BUCKET`, `DEV_SEED_MODE`,
  `DEV_S3_ENDPOINT_CLUSTER`, `DEV_PODMAN_MACHINE`, etc. (see `scripts/dev-k3d.sh`).
- The first `tilt up` (and `just dev-image`) build the ~1.3 GiB image and
  `k3d image import` it — slow once, then cached. `.py` edits live-sync and never
  trigger a rebuild.
