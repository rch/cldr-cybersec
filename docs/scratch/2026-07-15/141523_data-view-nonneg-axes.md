# Data-View: non-negative axes for Σ bytes + bg-load NameError

## What the user saw
Viewport preserve worked, but the histogram still showed a **negative range**
(wrong for elapsed-seconds x and especially for **bytes (Σ)** y).

## Causes
1. **Bokeh DataRange1d `range_padding≈0.1`** pads *below* the data minimum.
   When min is 0, the axis starts negative — empty region under a sum-of-bytes
   histogram.
2. **`hv.Bars` on continuous x** can half-width-bleed past 0.
3. Concurrently, panel-serve session teardown cleared module globals while
   daemon bg threads still ran → `NameError: json/time/logger not defined`,
   so live reload was flaky.

## Fix (`cybersec-dask:2025.2.0-dff2148cbe`)
- Switch plot to `hv.Histogram` (explicit bin edges).
- Clip metric/vals ≥ 0; drop t_sec < 0 rows.
- Axis hook: `range_padding=0`, bounds, JS+Python snaps for **x and y**.
- `ylim=(0, y_hi)`, `redim.range(value=(0, None))`.
- Local imports in bg I/O path (session-global-safe).

## Verify
Hard-refresh http://192.168.1.55:30506/data-view
- y-axis for bytes (Σ) starts at 0 (no negative band)
- x-axis seconds start at 0
- pan/zoom still preserves viewport across live refresh
