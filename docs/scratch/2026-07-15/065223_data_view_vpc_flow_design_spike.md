# Design Spike: Data-View + Streaming VPC Flow Logs

**Date**: 2026-07-15  
**Status**: spike / proposal  
**Context**: OTEL Navigator + Navigator Engine live on local RKE2; ELK-style Discover is the target second surface.  
**Reference layout**: `~/Downloads/elk_01.png` (Kibana Discover–class: facets, histogram, document table).  
**Hard requirement**: **UI continuously updates as streaming-generated flow data arrives.**

---

## 1. Problem

We have a working **density explorer** for OTEL spans (Panel + Dask + engine terminal). Operators also need a **log Discover** surface:

- Faceted navigation over structured fields (VPC flow shape).
- Time histogram + filterable document table.
- A **live** feed: constant synthetic flow generation into Iceberg/MinIO under a **fixed disk envelope**, with the UI **tracking the head of the stream** without filling the node.

These are different jobs. They share platform (Dask, MinIO, Panel, engine) but not layout or mental model.

---

## 2. Goals / Non-Goals

### Goals

| ID | Goal |
|----|------|
| G1 | Top-bar multi-page shell: **OTEL Navigator** · **Data-View** |
| G2 | Data-View layout mirrors ELK Discover (filter bar, time hist, facets, table) |
| G3 | Continuous VPC flow generator with **bounded disk envelope** (expire partitions) |
| G4 | **Live UI**: histogram, facets, and table refresh as new partitions land |
| G5 | Facet clicks → filter chips → recompute visible panes |
| G6 | Tinybox-safe defaults (disk, Dask graph size, refresh budget) |

### Non-Goals (v1)

- Full KQL/Lucene parity or saved searches.
- Elastic Security SIEM rules / ML.
- Merging density explorer and Discover into one page.
- Perfect exact counts on high-cardinality fields (top-N / sample is fine).
- Multi-tenant auth beyond what zarf already provides.

---

## 3. Product Shape

```
┌─────────────────────────────────────────────────────────────────────────┐
│  [OTEL Navigator]  [Data-View]              time range │ live ● 2s     │
├──────────────┬──────────────────────────────────────────────────────────┤
│ FACETS       │  FILTER BAR  action:REJECT  dstport:443  [×] [×]  [+]    │
│              ├──────────────────────────────────────────────────────────┤
│ action       │  ▁▂▃▅▇█▇▅▃▂▁▁▂▃  TIME HISTOGRAM (auto-refresh)           │
│  ACCEPT  82% ├──────────────────────────────────────────────────────────┤
│  REJECT  18% │  DOC TABLE (newest first, streaming tail)                │
│ protocol     │  time | srcaddr | dstaddr | sport | dport | proto | act  │
│  6       71% │  ...                                                     │
│  17      22% │                                                          │
│ dstport      │                                                          │
│  443     40% │                                                          │
│  …           │                                                          │
└──────────────┴──────────────────────────────────────────────────────────┘
```

**Top bar**

- Existing FastListTemplate header becomes multi-app nav.
- Data-View shows a **Live** indicator (refresh interval + last snapshot age).

**Data-View panes** (independent refresh priorities — §5):

1. Filter bar + chips  
2. Time histogram  
3. Facet sidebar (selected fields only)  
4. Document table (streaming tail / sample)

Engine terminal stays on OTEL page for v1; optional later `data-view` REPL commands.

---

## 4. Data Plane: Streaming VPC Flows + Envelope

### 4.1 Schema (v5-ish, analytic-friendly)

Stable columns for facets and table (names frozen early):

| Column | Type | Facet? | Notes |
|--------|------|--------|-------|
| `version` | int | no | Always 5 |
| `account_id` | string | yes | Synthetic pool |
| `interface_id` | string | yes | |
| `srcaddr` | string | top-N only | High card |
| `dstaddr` | string | top-N only | |
| `srcport` | int | bucketed | Well-known + other |
| `dstport` | int | bucketed | |
| `protocol` | int/string | yes | 6/17/1… |
| `packets` | long | no | |
| `bytes` | long | no | Metric for hist weight optional |
| `start` | timestamp | time axis | Event time |
| `end` | timestamp | no | |
| `action` | string | yes | ACCEPT/REJECT |
| `log_status` | string | yes | OK/NODATA/SKIPDATA |
| `vpc_id` | string | yes | |
| `subnet_id` | string | yes | |
| `instance_id` | string | optional | |
| `tcp_flags` | int | optional | |
| `flow_direction` | string | yes | ingress/egress |
| `pkt_srcaddr` | string | no | Optional v5 |
| `pkt_dstaddr` | string | no | |

Partition layout (Hive-style under warehouse):

```text
s3://cybersec-dask-data/vpc-flow/
  date=YYYY-MM-DD/hour=HH/minute=MM/part-*.parquet
```

Minute-level partitions keep **expire granularity** fine enough for a small envelope on tinybox. Iceberg table preferred when Polaris path is solid; raw Hive parquet is acceptable for spike if Iceberg writer lag is high — **same path shape** either way so the reader code stays stable.

### 4.2 Generator modes

| Mode | Behavior |
|------|----------|
| `seed` | Fill to envelope once (bootstrap Data-View) |
| `stream` | Constant rate forever; expire when over budget |

**Stream loop (conceptual):**

```text
every tick (e.g. 1–5s):
  1. synthesize N rows (event_time ≈ now)
  2. append partition (or append files into current minute partition)
  3. measure table size / partition count / oldest event_time
  4. if over envelope: drop oldest partitions + orphan cleanup
  5. write cursor file: _stream_cursor.json { snapshot_id|mtime, rows_written, bytes }
```

**Envelope knobs** (config; tinybox defaults):

| Knob | Default (proposal) |
|------|--------------------|
| `FLOW_RPS` | 200 |
| `FLOW_MAX_BYTES` | 8 GiB |
| `FLOW_MAX_HOURS` | 3 h |
| `FLOW_EXPIRE_EVERY` | 60 s |
| `FLOW_PARTITION` | minute |

Policy: enforce **min(time window, size cap)** — whichever binds first.

Deploy as a small K8s Deployment (or devenv process) using the same image family as datagen, **not** the Panel pod (keeps viz CPU free).

### 4.3 Why expire must be first-class

Local RKE2 already hit DiskPressure. Continuous generate without expire is a known outage mode. Generator owns retention; UI only **reads** the live window.

---

## 5. Live UI: Continuous Update Architecture

This is the spike’s center of gravity. “Load once + Ready” (OTEL path) is **wrong** for Data-View.

### 5.1 Model: head-following window, not infinite buffer

```text
                    ┌──────────── disk envelope ────────────┐
  ... expired | p−2 | p−1 | p0 (hot) |                     │
                    └──────────────────▲────────────────────┘
                                       │
                          UI reads last T minutes
                          (or last K partitions)
                          and rebinds on tick
```

- **Writer** advances the head and drops the tail (envelope).  
- **UI** holds a **query window** (e.g. last 15m / last 1h) that slides with wall clock **or** with discovered max(`start`).  
- On each refresh cycle the UI **rebinds** the Dask frame to current files/snapshot, then recomputes cheap aggregates.

### 5.2 Dual clocks

| Clock | Role |
|-------|------|
| **Discovery clock** (`catalog_tick`) | “Is there new data?” — list partitions / Iceberg snapshot id / cursor mtime |
| **Render clock** (`render_tick`) | “Recompute panes” — histogram, facets, table |

Do **not** force a full facet recompute on every new file if discovery says nothing changed. Do **not** skip recompute forever if the user is on a rolling “last 15m” window (window slides even if no write — optional; v1 can recompute only on catalog change + user filter change).

**Proposed defaults:**

| Parameter | Default | Notes |
|-----------|---------|-------|
| `DISCOVERY_INTERVAL_S` | 2 | Poll cursor / partition listing |
| `HIST_INTERVAL_S` | 2 | Align with discovery when dirty |
| `FACET_INTERVAL_S` | 5 | Heavier; debounce |
| `TABLE_INTERVAL_S` | 2 | Tail sample; cheap if partition-local |
| `UI_WINDOW` | last 15m | Independent of disk envelope (envelope ≥ window) |

### 5.3 Dirty flags + coalescing

```text
catalog_tick
  → if snapshot_id|max_partition changed: set dirty={hist, facets, table}
user filter change
  → set dirty={hist, facets, table} immediately
render loops
  → if dirty[pane] and pane interval elapsed: recompute; clear dirty
```

Coalesce bursts of writes into one recompute per interval (avoid Dask queue storms).

### 5.4 Panel wiring (concrete)

Extend Param model (DataView class):

```python
class DataView(param.Parameterized):
    # catalog
    stream_epoch = param.Integer(0)       # bumps when new partitions detected
    catalog_id = param.String("")         # snapshot id or "date=/hour=/minute=" max key
    live = param.Boolean(True)
    refresh_s = param.Number(2.0)

    # query
    time_window = param.Selector(...)     # 5m, 15m, 1h, 3h
    filters = param.List([])              # list of {field, op, value}
    filter_expr = param.String("")        # compiled pandas/dask query

    # pane tokens (force @param.depends refresh without cloning heavy dfs)
    hist_token = param.Integer(0)
    facet_token = param.Integer(0)
    table_token = param.Integer(0)

    rows_in_window = param.Integer(0)
    last_event_ts = param.String("")
```

**Background discovery** (thread or `pn.state.add_periodic_callback`):

```python
def _discover():
    cur = read_stream_cursor()  # or list new minute partitions
    if cur.id != self.catalog_id:
        self.catalog_id = cur.id
        self.stream_epoch += 1
        self._rebind_ddf()      # smart window: only files in UI_WINDOW
        self.hist_token += 1
        self.table_token += 1
        # facets less often
        if time.time() - self._last_facet > FACET_INTERVAL_S:
            self.facet_token += 1
```

**Reactive panes:**

```python
@param.depends('hist_token', 'filter_expr', 'time_window')
def histogram(self): ...

@param.depends('facet_token', 'filter_expr')
def facets(self): ...

@param.depends('table_token', 'filter_expr')
def doc_table(self): ...
```

Lessons from OTEL status bug: **every param that should repaint must be in `@param.depends`** (include `stream_epoch` / tokens, not only phase text).

### 5.5 Rebind strategy (keep Dask graph small)

Same spirit as OTEL `MAX_PARTITIONS` smart windowing:

1. Glob / Iceberg scan files with `start` in `[now - UI_WINDOW, now]`.  
2. Cap files (e.g. last 60 minute partitions).  
3. `dd.read_parquet(paths)` **new frame** on catalog change (cheaper than mutating).  
4. Apply `filter_expr` lazily; compute only aggregates + `head`/`tail` samples.

**Avoid:**

- `compute()` on full window row counts every 2s.  
- Faceting `srcaddr` without top-N limit.  
- Holding an ever-growing in-memory pandas buffer in the Panel process.

### 5.6 Table: streaming tail UX

Document table should feel live:

- Sort by `start` desc; show last N rows from **filtered** window (N=50–100).  
- Optional subtle row flash / “+K new since last refresh” badge using `stream_epoch`.  
- Do **not** try true websocket row push in v1 — interval recompute is enough if ≤2–3s.

### 5.7 Histogram

- Bin `start` into 30–60 buckets over UI window.  
- Count or sum(`bytes`) as height.  
- Datashader optional; for flow rates on tinybox, **Dask groupby_agg on time bins** is fine if window is capped.  
- Click-drag on hist (v2) → narrow time filter; v1 can skip brush.

### 5.8 Facets

Default facet fields:

```text
action, protocol, flow_direction, log_status, dstport_bucket, srcport_bucket, vpc_id
```

High-card optional (behind “more fields”):

```text
srcaddr, dstaddr  # top-N=10 on 1–2% sample
```

Click → append filter chip → bump all tokens.

**Cost control:** facet compute uses `df.sample(frac=0.05)` or partition subsample when `rows_in_window` estimate > threshold.

### 5.9 Backpressure & visibility

| Condition | UI behavior |
|-----------|-------------|
| Dask `processing` high | Slow intervals ×2; show “catching up…” |
| No catalog change for 30s | Dim Live indicator; still show last good panes |
| Generator down | Banner: “stream idle (cursor stale)” |
| Tab hidden (`document.hidden`) | Pause discovery (browser-side) if we add a small JS hook; else keep server poll cheap |

### 5.10 Consistency with expire

Readers may see missing files mid-refresh if expire races rebind:

- Prefer Iceberg snapshot reads (atomic).  
- Hive parquet: catch `FileNotFoundError`, retry rebind once, skip bad paths.  
- Envelope **≥** UI window so active files aren’t deleted under a 15m view (e.g. envelope 3h, UI 15m).

---

## 6. App Shell / Routing

### 6.1 Multi-page Panel

Options:

| Approach | Pros | Cons |
|----------|------|------|
| A. Two scripts, one `panel serve a.py b.py` | Isolation | Shared state harder |
| B. One process, routes `/otel-navigator`, `/data-view` | Shared Dask client possible | Larger process |
| C. Template tabs only | Simple | Weaker deep-linking |

**Recommendation: B** — single image/process already serves OTEL; add Data-View as second servable with header links:

```python
# conceptual
otel_app = SpanExplorer().servable(title="OTEL Navigator")
data_view = DataView().servable(path="data-view", title="Data-View")
```

Header HTML on both:

```html
<a href="/otel-navigator">OTEL Navigator</a>
<a href="/data-view">Data-View</a>
```

NodePort 30506 stays the entry; paths distinguish apps.

### 6.2 Shared vs isolated Dask clients

- OTEL and Data-View can share one `Client` (connection pool) but **must not share** the same `_ddf` / load state.  
- Engine `chk` “dataset: none” vs Panel auto-load remains a known split; Data-View should show **its own** live status strip (cursor, RPS estimate, window rows).

---

## 7. Engine (optional v1.5)

Not required for spike demo. Later:

```text
nav> load vpc-flow
nav> window 15m
nav> filter action == "REJECT"
nav> facets
```

Subscribe stream could push `catalog_id` changes to the UI (replace poll). Keep poll for v1 — already proven operationally simpler.

---

## 8. Deployment Topology (local RKE2 / zarf)

```text
┌──────────────── panel-viz ────────────────┐
│  otel-navigator (:5006)                   │
│  data-view      (:5006/data-view)          │
│  pty-proxy      (:8765)  → engine         │
└───────────────────┬───────────────────────┘
                    │ Dask
┌───────────────────▼───────────────────────┐
│  dask scheduler + workers                 │
└───────────────────┬───────────────────────┘
                    │ S3 API
┌───────────────────▼───────────────────────┐
│  MinIO  cybersec-dask-data/vpc-flow/      │
│  ←── vpc-flow-generator (stream+expire)   │
└───────────────────────────────────────────┘
```

New workload: `vpc-flow-generator` Deployment (low CPU/mem), config via ConfigMap (RPS, envelope).

`PTY_PROXY_WS` remains NodePort-aware for engine; unrelated to Data-View live path.

---

## 9. Phased Delivery

### Phase 0 — Shell (0.5–1 d)

- Multi-route Panel + top nav links.  
- Data-View static layout (empty panes, mock data) matching ELK zones.  
- Live indicator stub.

### Phase 1 — Generator + envelope (1–2 d)

- `generate-vpc-flow.py` (or extend zarf scripts): seed + stream + expire.  
- Cursor file + size/time policy.  
- Deploy manifest; validate disk steady-state on tinybox under continuous write.

### Phase 2 — Live read path (1–2 d)

- Discovery poll + ddf rebind.  
- Histogram + table auto-refresh on `stream_epoch`.  
- Prove: start generator → empty table fills → continuous row turnover without disk growth past envelope.

### Phase 3 — Facets + filters (1–2 d)

- Top-N facets, chips, filter expression.  
- Debounced facet refresh.  
- High-card safeguards.

### Phase 4 — Polish

- Backpressure, stale-stream banner, deep-link filters in URL.  
- Optional engine commands.  
- Docs + cheatsheet (`Data-View` NodePort path, generator knobs).

---

## 10. Success Criteria (spike exit)

| # | Criterion |
|---|-----------|
| 1 | Top nav reaches Data-View without breaking OTEL |
| 2 | Generator runs ≥30 min; MinIO prefix size ≤ envelope ±10% |
| 3 | With UI open, table/hist update ≥ every ~3s while stream runs |
| 4 | Facet click filters table without manual reload |
| 5 | Stopping generator → UI shows idle/stale, no crash |
| 6 | Expire deletes old partitions without crashing open Data-View sessions |

---

## 11. Risks

| Risk | Mitigation |
|------|------------|
| Dask overload from 2s full recomputes | Caps, sample facets, dirty flags, slowdown under load |
| Expire vs read races | Envelope ≫ UI window; retry rebind; Iceberg snapshots when ready |
| Panel `@param.depends` footguns | Tokens for every live pane; integration test that epoch bumps repaint |
| High-card facets | Default field list excludes raw IPs; top-N only |
| Scope creep to “full ELK” | Lock non-goals; Discover-lite only |
| Generator fills disk if expire bugs | Hard ceiling: refuse write if free disk / prefix size past hard stop |

---

## 12. Open Questions

1. **Iceberg vs Hive parquet for v1 stream?** Iceberg better for expire atomicity; Hive faster to ship if Polaris write path is sticky. Spike can start Hive+cursor, abstract `CatalogCursor`.  
2. **Event time vs ingestion time** for UI window? Prefer event time (`start`); generator must keep `start ≈ now`.  
3. **Single Panel process memory** with two apps — OK on current 4–8 Gi limits? If not, split Deployments later.  
4. **Exact `elk_01.png` chrome** — re-import screenshot into `docs/scratch/…/assets/` when available for pixel-level spacing.  
5. Should Live be **default on**, with pause toggle? **Yes** — pause useful when investigating a freeze-frame.

---

## 13. Recommendation

Proceed with **Phase 0 → 1 → 2** as the critical path: shell, bounded streamer, then **discovery-driven continuous UI**. Facets (Phase 3) are what make it feel like the ELK screenshot, but **live tail + histogram** prove the streaming thesis first.

**Do not** reuse OTEL’s one-shot `load_data` + `ready=True` lifecycle for Data-View. The core abstraction is:

> **`stream_epoch` + sliding file window + dirty coalesced pane recompute.**

That keeps the UI honest about arriving data without unbounded memory or disk.

---

## 14. Next Actions

1. Confirm tinybox envelope defaults (8 GiB / 3 h / 200 RPS) and Iceberg-vs-Hive call.  
2. Drop `elk_01.png` into repo assets for layout parity review.  
3. Implement Phase 0 shell PR (nav + empty Data-View).  
4. Implement Phase 1 generator with expire integration test (size bound).  
5. Implement Phase 2 live rebind + hist/table before facet polish.
