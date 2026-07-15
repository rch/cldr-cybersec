"""Data-View — ELK Discover–style live explorer for streaming VPC flow logs.

Sibling app to OTEL Navigator. Continuously rebinds to new partitions as the
vpc-flow generator appends (and expires) data under a fixed disk envelope.

Routes (panel serve multi-app):
  /data-view

Environment:
  S3_BUCKET, S3_ENDPOINT, AWS_*, VPC_FLOW_PREFIX (default vpc-flow)
  DATA_VIEW_WINDOW_MIN (default 15)
  DATA_VIEW_DISCOVERY_S (default 2)
  DATA_VIEW_FACET_S (default 5)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
import panel as pn
import param

try:
    import holoviews as hv
    hv.extension("bokeh")
    _HAS_HV = True
except Exception:  # pragma: no cover
    _HAS_HV = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("data-view")

pn.extension("tabulator", loading_spinner="dots", loading_color="#0072B5")

# -------------------------------------------------------------------------
# Config
# -------------------------------------------------------------------------

S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_SESSION_TOKEN = os.environ.get("AWS_SESSION_TOKEN", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
VPC_FLOW_PREFIX = os.environ.get("VPC_FLOW_PREFIX", "vpc-flow").strip("/")

WINDOW_MIN = int(os.environ.get("DATA_VIEW_WINDOW_MIN", "15"))
DISCOVERY_S = float(os.environ.get("DATA_VIEW_DISCOVERY_S", "2"))
FACET_S = float(os.environ.get("DATA_VIEW_FACET_S", "5"))
TABLE_ROWS = int(os.environ.get("DATA_VIEW_TABLE_ROWS", "80"))
MAX_FILES = int(os.environ.get("DATA_VIEW_MAX_FILES", "90"))

FACET_FIELDS = [
    "action",
    "protocol",
    "flow_direction",
    "log_status",
    "dstport",
    "vpc_id",
]

# Short labels — page title is separate (FastListTemplate); nav is Metrics | Data-View.
NAV_HTML = """
<div style="display:flex;gap:16px;align-items:center;font-family:-apple-system,BlinkMacSystemFont,sans-serif;font-size:13px;">
  <a href="/otel-navigator" style="color:#c9d1d9;text-decoration:none;">Metrics</a>
  <a href="/data-view" style="color:#fff;text-decoration:none;font-weight:700;border-bottom:2px solid #58a6ff;padding-bottom:2px;">Data-View</a>
</div>
"""


def _storage_options() -> dict:
    opts: dict[str, Any] = {}
    if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
        opts["key"] = AWS_ACCESS_KEY_ID
        opts["secret"] = AWS_SECRET_ACCESS_KEY
        if AWS_SESSION_TOKEN:
            opts["token"] = AWS_SESSION_TOKEN
    if S3_ENDPOINT:
        opts["client_kwargs"] = {"endpoint_url": S3_ENDPOINT}
    if AWS_REGION:
        opts.setdefault("client_kwargs", {})
        opts["client_kwargs"]["region_name"] = AWS_REGION
    return opts


def _s3fs():
    import s3fs

    return s3fs.S3FileSystem(**_storage_options())


def _port_bucket(p: int) -> str:
    if p in (80, 443, 22, 53, 123, 389, 636, 3306, 5432, 6379, 8080, 8443):
        return str(p)
    if p < 1024:
        return "sys-other"
    return "ephemeral"


# -------------------------------------------------------------------------
# Catalog / load
# -------------------------------------------------------------------------

def read_cursor(fs) -> dict:
    if not S3_BUCKET:
        return {}
    path = f"{S3_BUCKET}/{VPC_FLOW_PREFIX}/_stream_cursor.json"
    for attempt in range(3):
        try:
            # Cursor is rewritten every batch — bust s3fs ETag cache.
            try:
                fs.invalidate_cache(path)
            except Exception:
                pass
            if not fs.exists(path):
                return {}
            with fs.open(path, "rb") as f:
                return json.loads(f.read().decode("utf-8"))
        except Exception as e:
            if attempt == 2:
                logger.debug("cursor read failed: %s", e)
            time.sleep(0.05 * (attempt + 1))
    return {}


def list_recent_parquet(fs, window_min: int) -> list[str]:
    """List parquet files under prefix whose partition time is within window.

    Always busts s3fs directory cache — otherwise the UI freezes on the first
    listing while the generator keeps writing new objects.
    """
    if not S3_BUCKET:
        return []
    root = f"{S3_BUCKET}/{VPC_FLOW_PREFIX}"
    try:
        # Critical: s3fs caches listings; without invalidate, glob is sticky.
        try:
            fs.invalidate_cache(root)
            fs.invalidate_cache()
        except Exception:
            pass
        pattern = f"{root}/date=*/hour=*/minute=*/*.parquet"
        files = fs.glob(pattern, refresh=True) if hasattr(fs, "glob") else fs.glob(pattern)
    except TypeError:
        # older s3fs: no refresh kw
        try:
            fs.invalidate_cache()
            files = fs.glob(f"{root}/date=*/hour=*/minute=*/*.parquet")
        except Exception as e:
            logger.warning("glob failed: %s", e)
            return []
    except Exception as e:
        logger.warning("glob failed: %s", e)
        return []

    if not files:
        try:
            fs.invalidate_cache(root)
            files = [p for p in fs.find(root, refresh=True) if p.endswith(".parquet") and "_stream" not in p]
        except TypeError:
            files = [p for p in fs.find(root) if p.endswith(".parquet") and "_stream" not in p]
        except Exception:
            return []

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_min)
    kept: list[tuple[str, datetime]] = []
    for p in files:
        parts = {kv.split("=")[0]: kv.split("=")[1] for kv in p.split("/") if "=" in kv}
        try:
            dt = datetime(
                int(parts["date"][:4]),
                int(parts["date"][5:7]),
                int(parts["date"][8:10]),
                int(parts.get("hour", "0")),
                int(parts.get("minute", "0")),
                tzinfo=timezone.utc,
            )
        except Exception:
            # Prefer keeping unparseable paths (likely fresh) over dropping them
            dt = datetime.now(timezone.utc)
        if dt >= cutoff:
            kept.append((p if p.startswith("s3://") else f"s3://{p}", dt))

    # Newest first, cap, then chronological for stable concat
    kept.sort(key=lambda x: (x[1], x[0]), reverse=True)
    paths = [p for p, _ in kept[:MAX_FILES]]
    return list(reversed(paths))


def load_frame(paths: list[str]) -> pd.DataFrame:
    if not paths:
        return pd.DataFrame()
    try:
        import pyarrow.parquet as pq
        from pyarrow import fs as pafs

        storage = _storage_options()
        # Use pandas via s3fs for simplicity across endpoints
        frames = []
        fs = _s3fs()
        for p in paths:
            key = p.replace("s3://", "")
            try:
                with fs.open(key, "rb") as f:
                    table = pq.read_table(f)
                frames.append(table.to_pandas())
            except FileNotFoundError:
                continue
            except Exception as e:
                logger.debug("skip %s: %s", p, e)
                continue
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        if "start" in df.columns:
            df["start"] = pd.to_datetime(df["start"], utc=True, errors="coerce")
        return df
    except Exception as e:
        logger.exception("load_frame failed: %s", e)
        return pd.DataFrame()


def apply_filters(df: pd.DataFrame, filters: list[dict]) -> pd.DataFrame:
    if df.empty or not filters:
        return df
    out = df
    for f in filters:
        field, op, value = f.get("field"), f.get("op", "=="), f.get("value")
        if field not in out.columns:
            continue
        try:
            col = out[field]
            if op == "==":
                if pd.api.types.is_numeric_dtype(col):
                    try:
                        value_n = pd.to_numeric(value)
                        out = out[col == value_n]
                    except Exception:
                        out = out[col.astype(str) == str(value)]
                else:
                    out = out[col.astype(str) == str(value)]
            elif op == "!=":
                out = out[col.astype(str) != str(value)]
        except Exception as e:
            logger.warning("filter apply %s: %s", f, e)
    return out


# -------------------------------------------------------------------------
# DataView app
# -------------------------------------------------------------------------

class DataView(param.Parameterized):
    live = param.Boolean(default=True)
    time_window = param.Selector(default=f"{WINDOW_MIN}m", objects=["5m", "15m", "1h", "3h"])
    stream_epoch = param.Integer(default=0)
    catalog_id = param.String(default="")
    filters = param.List(default=[])
    hist_token = param.Integer(default=0)
    facet_token = param.Integer(default=0)
    table_token = param.Integer(default=0)
    rows_in_window = param.Integer(default=0)
    last_event_ts = param.String(default="—")
    status_line = param.String(default="Starting…")
    rps_est = param.Number(default=0.0)
    error = param.String(default="")

    def __init__(self, **params):
        super().__init__(**params)
        self._df_raw = pd.DataFrame()
        self._df = pd.DataFrame()
        self._lock = threading.Lock()
        self._last_facet = 0.0
        self._last_rows = 0
        self._last_rows_t = time.time()
        self._busy = False
        self._cb = None  # periodic callback handle
        self._filter_input = pn.widgets.TextInput(
            name="",  # no label — keeps the control bar on one baseline
            placeholder='field:value  e.g. action:REJECT  dstport:443',
            sizing_mode="stretch_width",
        )
        self._filter_input.param.watch(self._on_filter_submit, "value")
        self._add_btn = pn.widgets.Button(name="Add filter", button_type="primary", width=110)
        self._add_btn.on_click(self._on_add_click)
        self._clear_btn = pn.widgets.Button(name="Clear", width=70)
        self._clear_btn.on_click(self._on_clear)
        self._pause = pn.widgets.Toggle(name="Live", value=True, width=70)
        self._pause.param.watch(self._on_live_toggle, "value")
        self._window = pn.widgets.Select.from_param(self.param.time_window, name="", width=100)

    # -- filter UI --
    def _on_live_toggle(self, event):
        self.live = bool(event.new)

    def _on_clear(self, *_):
        self.filters = []
        self._bump_all()

    def _on_add_click(self, *_):
        self._parse_and_add(self._filter_input.value)
        self._filter_input.value = ""

    def _on_filter_submit(self, event):
        # Enter in some browsers fires value change with same text; only on explicit add for safety
        pass

    def _parse_and_add(self, text: str):
        text = (text or "").strip()
        if not text:
            return
        if ":" in text:
            field, _, value = text.partition(":")
            field, value = field.strip(), value.strip()
        elif "==" in text:
            field, _, value = text.partition("==")
            field, value = field.strip(), value.strip().strip("\"'")
        else:
            self.error = f"Bad filter: {text!r} (use field:value)"
            return
        self.filters = list(self.filters) + [{"field": field, "op": "==", "value": value}]
        self.error = ""
        self._bump_all()

    def add_facet_filter(self, field: str, value: str):
        self.filters = list(self.filters) + [{"field": field, "op": "==", "value": value}]
        self._bump_all()

    def remove_filter(self, idx: int):
        fl = list(self.filters)
        if 0 <= idx < len(fl):
            fl.pop(idx)
            self.filters = fl
            self._bump_all()

    def _bump_all(self):
        self.hist_token += 1
        self.facet_token += 1
        self.table_token += 1
        self._recompute_filtered()

    def _window_minutes(self) -> int:
        m = {"5m": 5, "15m": 15, "1h": 60, "3h": 180}
        return m.get(self.time_window, WINDOW_MIN)

    def _recompute_filtered(self):
        with self._lock:
            raw = self._df_raw
            self._df = apply_filters(raw, list(self.filters))
            self.rows_in_window = len(self._df)
            if not self._df.empty and "start" in self._df.columns:
                mx = self._df["start"].max()
                self.last_event_ts = str(mx) if pd.notna(mx) else "—"

    # -- discovery (Bokeh periodic callback — NOT a raw thread) --
    # Param updates from a daemon thread do not push to open browser sessions.
    # pn.state.add_periodic_callback runs on the server event loop and does.
    def start(self):
        if self._cb is not None:
            return
        # Immediate first tick so the page isn't empty until the first interval.
        try:
            self._discover_once()
        except Exception as e:
            logger.exception("initial discover: %s", e)
            self.error = str(e)
        period_ms = max(int(DISCOVERY_S * 1000), 500)
        self._cb = pn.state.add_periodic_callback(self._tick, period=period_ms)
        logger.info("Data-View live poll every %sms", period_ms)

    def _tick(self):
        if not self.live:
            return
        if self._busy:
            return
        self._busy = True
        try:
            self._discover_once()
        except Exception as e:
            logger.exception("discover: %s", e)
            self.error = str(e)
        finally:
            self._busy = False

    def _discover_once(self):
        if not S3_BUCKET:
            self.status_line = "S3_BUCKET not set"
            return
        fs = _s3fs()
        cur = read_cursor(fs)
        catalog = str(
            cur.get("catalog_id")
            or cur.get("rows_written")
            or cur.get("last_path")
            or cur.get("last_partition")
            or ""
        )
        paths = list_recent_parquet(fs, self._window_minutes())
        # Prefer newest slice for fast reload (full window still capped by MAX_FILES)
        live_paths = paths[-min(len(paths), 24):] if paths else []
        sig = f"{catalog}|{len(paths)}|{paths[-1] if paths else ''}|{paths[0] if paths else ''}"
        if sig != self.catalog_id:
            self.catalog_id = sig
            # Load the recent tail for snappy live updates; fall back to full set if tiny
            load_paths = live_paths if len(live_paths) >= 1 else paths
            df = load_frame(load_paths)
            with self._lock:
                self._df_raw = df
            self._recompute_filtered()
            now = time.time()
            dt = max(now - self._last_rows_t, 0.001)
            delta = max(len(df) - self._last_rows, 0)
            self.rps_est = round(delta / dt, 1) if delta > 0 else max(self.rps_est * 0.5, 0.0)
            self._last_rows = len(df)
            self._last_rows_t = now
            self.stream_epoch += 1
            self.hist_token += 1
            self.table_token += 1
            if now - self._last_facet >= FACET_S:
                self.facet_token += 1
                self._last_facet = now
            age = cur.get("updated_at", "")
            self.status_line = (
                f"Live · epoch {self.stream_epoch} · {len(load_paths)}/{len(paths)} files · "
                f"{self.rows_in_window:,} rows · cursor {age or 'n/a'}"
            )
            self.error = ""
            logger.info(
                "discover epoch=%s files=%s rows=%s last=%s",
                self.stream_epoch, len(load_paths), self.rows_in_window,
                paths[-1] if paths else None,
            )
        else:
            if not self.catalog_id:
                self.status_line = "Waiting for vpc-flow stream (no partitions yet)…"
            else:
                # Heartbeat so the strip shows the poll is alive even if listing is sticky
                self.status_line = (
                    f"Live · epoch {self.stream_epoch} · poll ok · "
                    f"{self.rows_in_window:,} rows (no new files)"
                )

    # -- panes --
    @param.depends("filters", "error")
    def filter_bar(self):
        chips = []
        for i, f in enumerate(self.filters):
            label = f"{f['field']}:{f['value']}"
            btn = pn.widgets.Button(name=f"× {label}", button_type="light", width=max(120, 10 * len(label)))
            # capture index
            def _rm(event, idx=i):
                self.remove_filter(idx)
            btn.on_click(_rm)
            chips.append(btn)
        err = pn.pane.Alert(self.error, alert_type="warning") if self.error else pn.Spacer(height=0)
        return pn.Column(
            pn.Row(self._filter_input, self._add_btn, self._clear_btn, self._window, self._pause, sizing_mode="stretch_width"),
            pn.Row(*chips, sizing_mode="stretch_width") if chips else pn.Spacer(height=0),
            err,
            sizing_mode="stretch_width",
        )

    @param.depends("hist_token", "stream_epoch", "time_window")
    def histogram_pane(self):
        """Auto-advancing line/area time series (live edge follows stream_epoch)."""
        df = self._df
        if df is None or df.empty or "start" not in df.columns:
            return pn.pane.Markdown(
                "### Time histogram\n_No data in window — start vpc-flow-generator stream._",
                sizing_mode="stretch_width",
            )
        try:
            s = df.dropna(subset=["start"]).set_index("start").sort_index()
            # resample to ~40 bins
            span = (s.index.max() - s.index.min()).total_seconds() or 1
            rule = "30s" if span < 1800 else ("1min" if span < 7200 else "5min")
            counts = s.resample(rule).size()
            if _HAS_HV:
                curve = hv.Curve((counts.index, counts.values), "time", "flows").opts(
                    height=140,
                    responsive=True,
                    color="#58a6ff",
                    line_width=2,
                    tools=["hover"],
                    xlabel="",
                    ylabel="flows",
                    bgcolor="#0d1117",
                )
                area = hv.Area((counts.index, counts.values), "time", "flows").opts(
                    alpha=0.25, color="#58a6ff", bgcolor="#0d1117",
                )
                return pn.pane.HoloViews(area * curve, sizing_mode="stretch_width", height=160)
            # fallback table stats
            return pn.pane.Markdown(f"**{len(df):,}** flows in window (hist backend unavailable)")
        except Exception as e:
            logger.exception("hist")
            return pn.pane.Alert(f"Histogram error: {e}", alert_type="warning")

    @param.depends("facet_token", "stream_epoch")
    def facets_pane(self):
        df = self._df
        if df is None or df.empty:
            return pn.pane.Markdown("_No facets yet_", sizing_mode="stretch_width")

        sections = [pn.pane.Markdown("### Fields", margin=(0, 0, 8, 0))]
        work = df
        # sample for high-ish volume
        if len(work) > 50_000:
            work = work.sample(n=50_000, random_state=0)

        for field in FACET_FIELDS:
            if field not in work.columns:
                continue
            vc = work[field].astype(str).value_counts().head(8)
            total = max(int(vc.sum()), 1)
            rows = [pn.pane.Markdown(f"**{field}**", margin=(8, 0, 2, 0))]
            for val, cnt in vc.items():
                pct = 100.0 * cnt / total
                label = f"{val}  {cnt:,} ({pct:.0f}%)"
                b = pn.widgets.Button(name=label, button_type="light", sizing_mode="stretch_width",
                                     styles={"text-align": "left", "font-size": "12px"})
                def _click(event, f=field, v=str(val)):
                    self.add_facet_filter(f, v)
                b.on_click(_click)
                rows.append(b)
            sections.extend(rows)

        return pn.Column(*sections, sizing_mode="stretch_width", scroll=True)

    @param.depends("table_token", "stream_epoch")
    def table_pane(self):
        df = self._df
        cols = [
            c for c in [
                "start", "srcaddr", "dstaddr", "srcport", "dstport",
                "protocol", "action", "bytes", "packets", "flow_direction", "vpc_id",
            ] if df is not None and c in df.columns
        ]
        if df is None or df.empty or not cols:
            return pn.pane.Markdown("_No documents in window_", sizing_mode="stretch_both")
        show = df.sort_values("start", ascending=False).head(TABLE_ROWS) if "start" in df.columns else df.head(TABLE_ROWS)
        show = show[cols].copy()
        if "start" in show.columns:
            show["start"] = show["start"].astype(str)
        return pn.widgets.Tabulator(
            show,
            pagination="remote",
            page_size=20,
            sizing_mode="stretch_both",
            theme="midnight",
            layout="fit_data_stretch",
        )

    @param.depends("status_line", "rps_est", "rows_in_window", "stream_epoch", "live", "last_event_ts")
    def status_strip(self):
        live_col = "#3fb950" if self.live else "#d29922"
        live_txt = "● LIVE" if self.live else "○ PAUSED"
        return pn.pane.HTML(
            f"<div style='display:flex;gap:18px;align-items:center;font-size:12px;"
            f"color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,sans-serif;'>"
            f"<span style='color:{live_col};font-weight:700;'>{live_txt}</span>"
            f"<span>{self.status_line}</span>"
            f"<span>rows: <b>{self.rows_in_window:,}</b></span>"
            f"<span>≈ {self.rps_est:.0f} Δrows/s</span>"
            f"<span>last event: {self.last_event_ts}</span>"
            f"</div>",
            sizing_mode="stretch_width",
        )

    def layout(self):
        # pn.panel() wraps @param.depends methods so token bumps repaint the DOM.
        main = pn.Column(
            pn.pane.HTML(NAV_HTML),
            pn.panel(self.status_strip),
            pn.panel(self.filter_bar),
            pn.layout.Divider(),
            pn.panel(self.histogram_pane),
            pn.layout.Divider(),
            pn.pane.Markdown("### Documents", margin=(0, 0, 4, 0)),
            pn.panel(self.table_pane),
            sizing_mode="stretch_both",
            min_height=700,
        )
        side = pn.Column(
            pn.pane.Markdown("# Data-View", margin=(0, 0, 6, 0)),
            pn.pane.Markdown(
                f"**Prefix** `{VPC_FLOW_PREFIX}/`  \n"
                f"**Bucket** `{S3_BUCKET or '—'}`  \n"
                f"**Window** sliding (see control)",
                styles={"font-size": "11px", "color": "#8b949e"},
            ),
            pn.layout.Divider(),
            pn.panel(self.facets_pane),
            width=280,
            sizing_mode="fixed",
            scroll=True,
        )
        return pn.template.FastListTemplate(
            title="Data-View · VPC Flow",
            sidebar=[side],
            main=[main],
            accent_base_color="#0072B5",
            header_background="#161b22",
            sidebar_width=300,
            theme="dark",
        )


# -------------------------------------------------------------------------
# Serve — one DataView per browser session (script re-executes per session)
# -------------------------------------------------------------------------

view = DataView()
tmpl = view.layout()
# panel serve re-executes this script per browser session, so a periodic
# callback registered here is session-scoped and drives live updates.
view.start()
# Also hook onload in case the callback needs a fully ready Document.
pn.state.onload(view.start)
tmpl.servable()
print("[DATA-VIEW] App ready", flush=True)
