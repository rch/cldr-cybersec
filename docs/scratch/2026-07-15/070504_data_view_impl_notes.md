# Data-View implementation notes (2026-07-15)

## Shipped

- `zarf/images/data-view.py` — ELK-style Discover (facets, hist, table, filters, live poll)
- `zarf/scripts/generate-vpc-flow.py` — seed/stream + size/time envelope expire
- `zarf/manifests/vpc-flow-generator.yaml` — Deployment in panel-viz
- Multi-app `panel serve otel-navigator.py data-view.py`
- Top nav links on both apps
- Image tag content-hash includes new files

## Live URLs (local RKE2)

- OTEL: http://192.168.1.55:30506/otel-navigator
- Data-View: http://192.168.1.55:30506/data-view

## Defaults

- FLOW_RPS=200, FLOW_MAX_BYTES=8GiB, FLOW_MAX_HOURS=3
- DATA_VIEW_WINDOW_MIN=15, discovery every 2s

## Fix applied mid-rollout

Cursor was rewritten every batch → s3fs ETag races. Generator now writes cursor every 2s; reader invalidates cache + retries.
