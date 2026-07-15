# Data-View histogram: no negative range + preserve viewport on live refresh

## Issues
1. Zoom/pan allowed axis start < 0 (empty region, no data).
2. Live discover refresh reset the user's target range.

## Root causes
- Live refresh rebased `t_sec` on a new `t0` (window min). RangeX held *relative*
  seconds from the previous origin; `_resolved_x_range` preferred those over the
  absolute UTC viewport, so every poll remapped the view incorrectly (often full
  window / garbage).
- Pipe.send on refresh ran the DynamicMap callback with the stale stream range
  and *overwrote* `_hist_view_abs`.
- Bokeh clamp was server-only `on_change` — lag mid-drag still painted negatives.

## Fix (image `cybersec-dask:2025.2.0-e80d377b4a`)
- `_hist_view_abs` is source of truth (UTC). `None` = follow full live window.
- `_hist_applying` flag: programmatic Pipe/RangeX pushes ignore stream relative secs.
- User pan/zoom stores abs; full-range / reset clears abs (follow live).
- Client CustomJS clamp on x_range + bounds/min/max_interval + Python snap.
- `_clamp_xr` hard walls at [0, t_max], preserves span when possible.

## Deploy
- Built via `zarf/scripts/ops.sh image`
- Pushed to zarf registry `127.0.0.1:31999` with `-zarf-2560517462` suffix
- Rolled `deploy/otel-navigator` in `panel-viz`

## Verify
Hard-refresh http://192.168.1.55:30506/data-view
1. Wheel-zoom / pan left — axis should not go below 0.
2. Zoom into a mid-window band; wait for epoch/files refresh — view should hold.
