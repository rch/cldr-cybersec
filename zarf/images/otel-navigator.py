"""OTEL Span Explorer - Auto-loading with live Dask status.

Production Panel application for visualizing OTEL trace data with HoloViews + Datashader.

Key architecture:
- Auto-loads data on page render (no button needed)
- Live status shows Dask connection, loading, and task activity
- Zoom/pan triggers background Dask work with visible progress
- Fixed-size heatmap that doesn't collapse

Environment variables:
- DASK_SCHEDULER: Dask scheduler address (required)
- S3_BUCKET: S3 bucket name (default: cybersec-dask-data)
- OTEL_DATA_PATH: Path to OTEL data (default: s3://{S3_BUCKET}/otel-minimal/)
- S3_ENDPOINT: S3 endpoint for MinIO (optional)
- AWS_ACCESS_KEY_ID: S3 access key
- AWS_SECRET_ACCESS_KEY: S3 secret key
- AWS_REGION: AWS region (default: us-east-1)
"""
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import colorcet as cc
import dask.dataframe as dd
import holoviews as hv
import hvplot.dask  # noqa: F401 - required for hvplot extension
import panel as pn
import param
from dask.distributed import Client
from holoviews.operation.datashader import rasterize, dynspread

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
logger = logging.getLogger(__name__)

hv.extension('bokeh')
pn.extension(loading_spinner='dots', loading_color='#0072B5')

# -------------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------------

DASK_SCHEDULER = os.environ.get('DASK_SCHEDULER', '')
S3_BUCKET = os.environ.get('S3_BUCKET', 'cybersec-dask-data')
OTEL_DATA_PATH = os.environ.get('OTEL_DATA_PATH', f's3://{S3_BUCKET}/otel-minimal/')
S3_ENDPOINT = os.environ.get('S3_ENDPOINT', '')
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID', '')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY', '')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')

# Fixed canvas size to prevent collapse
CANVAS_WIDTH = 800
CANVAS_HEIGHT = 500
MIN_MAIN_HEIGHT = 600

# -------------------------------------------------------------------------
# Dask Client (lazy singleton)
# -------------------------------------------------------------------------

_dask_client = None


def get_dask_client() -> Client:
    global _dask_client
    if _dask_client is not None:
        return _dask_client
    if not DASK_SCHEDULER:
        raise RuntimeError("DASK_SCHEDULER not set")
    _dask_client = Client(DASK_SCHEDULER, timeout='30s')
    return _dask_client


def get_dask_stats() -> dict:
    """Get current Dask cluster stats."""
    try:
        client = get_dask_client()
        info = client.scheduler_info()
        workers = len(info.get('workers', {}))
        processing = sum(
            len(w.get('processing', {}))
            for w in info.get('workers', {}).values()
        )
        return {'workers': workers, 'processing': processing, 'connected': True}
    except Exception:
        return {'workers': 0, 'processing': 0, 'connected': False}


# -------------------------------------------------------------------------
# S3 Storage
# -------------------------------------------------------------------------

def get_storage_options() -> dict:
    opts = {'key': AWS_ACCESS_KEY_ID, 'secret': AWS_SECRET_ACCESS_KEY}
    if S3_ENDPOINT:
        opts['client_kwargs'] = {'endpoint_url': S3_ENDPOINT}
    if AWS_REGION:
        opts.setdefault('client_kwargs', {})
        opts['client_kwargs']['region_name'] = AWS_REGION
    return {k: v for k, v in opts.items() if v}


# -------------------------------------------------------------------------
# Dataset Detection (two-phase: minimal -> large)
# -------------------------------------------------------------------------

def get_active_dataset() -> dict:
    """Check which dataset is active/available."""
    import json
    import s3fs

    bucket = S3_BUCKET

    try:
        fs = s3fs.S3FileSystem(**get_storage_options())
        marker_path = f"{bucket}/_active_dataset.json"
        with fs.open(marker_path, 'r') as f:
            marker = json.load(f)
        dataset = marker.get('dataset', 'otel-minimal')
        phase = marker.get('phase', 'unknown')
        return {
            'dataset': dataset,
            'phase': phase,
            'path': f"s3://{bucket}/{dataset}/",
            'total_spans': marker.get('total_spans'),
        }
    except FileNotFoundError:
        logger.info("No dataset marker found, using default path")
    except Exception as e:
        logger.warning(f"Error reading dataset marker: {e}")

    return {
        'dataset': 'otel-minimal',
        'phase': 'default',
        'path': OTEL_DATA_PATH,
        'total_spans': None,
    }


# -------------------------------------------------------------------------
# Data Loader
# -------------------------------------------------------------------------

def load_span_data(start_time: datetime, end_time: datetime, data_path: str = None, on_progress=None) -> dd.DataFrame:
    import s3fs

    base_path = data_path or OTEL_DATA_PATH
    s3_base = base_path.rstrip('/') + '/spans'
    start_date, end_date = start_time.date(), end_time.date()

    if on_progress:
        on_progress("Scanning S3 partitions...")

    date_partitions = []
    current = start_date
    while current <= end_date:
        date_partitions.append(current.strftime('%Y-%m-%d'))
        current += timedelta(days=1)

    fs = s3fs.S3FileSystem(**get_storage_options())
    s3_base_path = s3_base.replace('s3://', '')

    if on_progress:
        on_progress(f"Globbing {len(date_partitions)} days...")

    parquet_files = []
    for i, date_str in enumerate(date_partitions):
        pattern = f"{s3_base_path}/date={date_str}/hour=*/*.parquet"
        files = fs.glob(pattern)
        parquet_files.extend([f"s3://{f}" for f in files])
        if on_progress and i % 2 == 0:
            on_progress(f"Found {len(parquet_files)} files...")

    if not parquet_files:
        import pandas as pd
        return dd.from_pandas(pd.DataFrame({
            'timestamp_s': pd.Series(dtype='float64'),
            'duration_ms': pd.Series(dtype='float64'),
        }), npartitions=1)

    if on_progress:
        on_progress(f"Loading {len(parquet_files)} parquet files...")

    ddf = dd.read_parquet(
        parquet_files,
        storage_options=get_storage_options(),
        columns=['start_time_unix_nano', 'duration_ns', 'service_name'],
        engine='pyarrow',
    )

    ddf['timestamp_s'] = ddf['start_time_unix_nano'] / 1_000_000_000
    ddf['duration_ms'] = ddf['duration_ns'] / 1_000_000

    if on_progress:
        on_progress(f"Ready: {ddf.npartitions} partitions")

    return ddf


# -------------------------------------------------------------------------
# Main App
# -------------------------------------------------------------------------

class SpanExplorer(param.Parameterized):
    """Auto-loading span explorer with live Dask status and dataset auto-swap."""

    # Controls
    time_preset = param.Selector(
        default='Last 24 Hours',
        objects=['Last Hour', 'Last 6 Hours', 'Last 24 Hours', 'Last 7 Days'],
    )
    cmap = param.Selector(default='fire', objects=['fire', 'viridis', 'plasma', 'inferno', 'blues'])
    spread_enabled = param.Boolean(default=True)

    # State
    phase = param.String(default='Initializing...')
    workers = param.Integer(default=0)
    processing = param.Integer(default=0)
    partitions = param.Integer(default=0)
    ready = param.Boolean(default=False)
    error = param.String(default='')

    # Dataset state
    current_dataset = param.String(default='')
    dataset_phase = param.String(default='')

    def __init__(self, **params):
        super().__init__(**params)
        self._ddf = None
        self._poll_thread = None
        self._dataset_thread = None
        self._stop_polling = False
        self._current_data_path = None

        ds_info = get_active_dataset()
        self.current_dataset = ds_info['dataset']
        self.dataset_phase = ds_info['phase']
        self._current_data_path = ds_info['path']

    def _get_time_range(self):
        now = datetime.now(timezone.utc)
        deltas = {
            'Last Hour': timedelta(hours=1),
            'Last 6 Hours': timedelta(hours=6),
            'Last 24 Hours': timedelta(hours=24),
            'Last 7 Days': timedelta(days=7),
        }
        return (now - deltas[self.time_preset], now)

    def _poll_dask_status(self):
        """Background thread to poll Dask task activity."""
        while not self._stop_polling:
            try:
                stats = get_dask_stats()
                self.workers = stats['workers']
                self.processing = stats['processing']
            except Exception:
                pass
            time.sleep(1)

    def start_polling(self):
        """Start background Dask status polling."""
        if self._poll_thread is None:
            self._stop_polling = False
            self._poll_thread = threading.Thread(target=self._poll_dask_status, daemon=True)
            self._poll_thread.start()

    def _poll_dataset_changes(self):
        """Background thread to poll for dataset changes."""
        time.sleep(10)
        while not self._stop_polling:
            try:
                ds_info = get_active_dataset()
                new_dataset = ds_info['dataset']
                if new_dataset != self.current_dataset:
                    logger.info(f"Dataset changed: {self.current_dataset} -> {new_dataset}")
                    self.current_dataset = new_dataset
                    self.dataset_phase = ds_info['phase']
                    self._current_data_path = ds_info['path']
                    self._ddf = None
                    self.ready = False
                    self.phase = f"Switched to {new_dataset}, reloading..."
                    self.load_data()
            except Exception as e:
                logger.warning(f"Dataset poll error: {e}")
            time.sleep(60)

    def start_dataset_watcher(self):
        """Start background dataset change polling."""
        if self._dataset_thread is None:
            self._dataset_thread = threading.Thread(target=self._poll_dataset_changes, daemon=True)
            self._dataset_thread.start()

    def load_data(self):
        """Load data with progress updates."""
        try:
            self.phase = f"Connecting to Dask ({self.current_dataset})..."
            get_dask_client()
            stats = get_dask_stats()
            self.workers = stats['workers']

            def on_progress(msg):
                self.phase = msg

            start, end = self._get_time_range()
            self._ddf = load_span_data(
                start, end,
                data_path=self._current_data_path,
                on_progress=on_progress,
            )
            self.partitions = self._ddf.npartitions

            self.phase = f"Ready ({self.current_dataset}: {self.partitions} partitions)"
            self.ready = True
            self.start_polling()
            self.start_dataset_watcher()

        except Exception as e:
            logger.exception("Load failed")
            self.error = str(e)
            self.phase = f"Error: {e}"

    @param.depends('phase', 'workers', 'processing', 'partitions', 'error', 'current_dataset', 'dataset_phase')
    def status_panel(self):
        """Live status panel showing Dask activity and dataset info."""
        if self.error:
            return pn.pane.Alert(f"**Error**: {self.error}", alert_type='danger')

        if self.processing > 0:
            activity = f"**{self.processing}** tasks running"
            activity_style = "color: #28a745; font-weight: bold;"
        elif self.ready:
            activity = "Idle"
            activity_style = "color: #6c757d;"
        else:
            activity = "Loading..."
            activity_style = "color: #007bff;"

        phase_emoji = {"minimal": "🔵", "large": "🟢", "default": "⚪"}.get(self.dataset_phase, "⚪")

        return pn.Column(
            pn.pane.Markdown("### Status", margin=(0, 0, 5, 0)),
            pn.pane.HTML(f"<div style='{activity_style}'>{activity}</div>"),
            pn.pane.Markdown(f"""
**Phase**: {self.phase}
**Workers**: {self.workers}
**Partitions**: {self.partitions}
**Dataset**: {phase_emoji} {self.current_dataset} ({self.dataset_phase})
            """, margin=(5, 0, 0, 0)),
            sizing_mode='stretch_width',
        )

    @param.depends('ready', 'cmap', 'spread_enabled', 'time_preset')
    def heatmap_view(self):
        """Heatmap with fixed size."""
        def wrap_content(content):
            return pn.Column(
                content,
                min_height=CANVAS_HEIGHT,
                height=CANVAS_HEIGHT,
                sizing_mode='stretch_width',
                styles={
                    'min-height': f'{CANVAS_HEIGHT}px',
                    'height': f'{CANVAS_HEIGHT}px',
                },
            )

        if self.error:
            return wrap_content(pn.pane.Alert(f"Error: {self.error}", alert_type='danger'))

        if not self.ready or self._ddf is None:
            return wrap_content(
                pn.Column(
                    pn.indicators.LoadingSpinner(value=True, size=50, color='primary'),
                    pn.pane.Markdown(f"**{self.phase}**", align='center'),
                    align='center',
                    styles={'display': 'flex', 'justify-content': 'center', 'align-items': 'center', 'height': '100%'},
                )
            )

        try:
            cmap_lookup = {
                'fire': cc.fire, 'viridis': 'viridis',
                'plasma': 'plasma', 'inferno': 'inferno', 'blues': cc.blues,
            }

            points = hv.Points(
                self._ddf,
                kdims=['timestamp_s', 'duration_ms'],
            ).opts(
                width=CANVAS_WIDTH,
                height=CANVAS_HEIGHT,
            )

            rasterized_plot = rasterize(
                points,
                aggregator='count',
                dynamic=True,
            ).opts(
                cmap=cmap_lookup.get(self.cmap, cc.fire),
                cnorm='eq_hist',
                colorbar=True,
                xlabel='Time (Unix seconds)',
                ylabel='Duration (ms)',
                title=f'Span Latency - {self.time_preset}',
                tools=['hover', 'box_zoom', 'wheel_zoom', 'pan', 'reset'],
                active_tools=['box_zoom'],
                responsive=True,
            )

            result = dynspread(rasterized_plot, max_px=3) if self.spread_enabled else rasterized_plot

            return wrap_content(
                pn.pane.HoloViews(
                    result,
                    sizing_mode='stretch_width',
                    min_height=CANVAS_HEIGHT,
                    height=CANVAS_HEIGHT,
                )
            )

        except Exception as e:
            logger.exception("Render failed")
            return wrap_content(pn.pane.Alert(f"Render error: {e}", alert_type='warning'))

    def sidebar(self):
        """Sidebar with status and controls."""
        scheduler_short = DASK_SCHEDULER.split('.')[0] if DASK_SCHEDULER else 'N/A'
        return pn.Column(
            pn.pane.Markdown("# OTEL Navigator", margin=(0, 0, 10, 0)),
            self.status_panel,
            pn.layout.Divider(),
            pn.pane.Markdown("### Time Range"),
            pn.widgets.Select.from_param(self.param.time_preset, name='', sizing_mode='stretch_width'),
            pn.layout.Divider(),
            pn.pane.Markdown("### Visualization"),
            pn.widgets.Select.from_param(self.param.cmap, name='Colormap', sizing_mode='stretch_width'),
            pn.widgets.Checkbox.from_param(self.param.spread_enabled, name='Spread'),
            pn.layout.Divider(),
            pn.pane.Markdown(f"""
**Dask**: `{scheduler_short}`
**Data**: `{self.current_dataset}/spans`
            """, styles={'font-size': '11px', 'color': '#6c757d'}),
            width=260,
        )

    def main_view(self):
        """Main content with fixed-height heatmap."""
        return pn.Column(
            self.heatmap_view,
            sizing_mode='stretch_width',
            min_height=MIN_MAIN_HEIGHT,
            styles={
                'min-height': f'{MIN_MAIN_HEIGHT}px',
                'overflow': 'visible',
            },
        )

    def servable(self):
        """Build the app and auto-trigger load."""
        raw_css = """
        .main-content, .bk-root, .bk-Column {
            min-height: 500px !important;
        }
        #main {
            min-height: calc(100vh - 80px) !important;
        }
        .pn-loading {
            min-height: 500px !important;
        }
        """
        pn.config.raw_css.append(raw_css)

        template = pn.template.FastListTemplate(
            title="OTEL Navigator",
            sidebar=[self.sidebar()],
            main=[self.main_view],
            accent_base_color="#0072B5",
            header_background="#0072B5",
            sidebar_width=280,
            theme="dark",
        )

        def on_load():
            self.load_data()

        pn.state.onload(on_load)

        return template


# Create and serve
explorer = SpanExplorer()
explorer.servable().servable()
