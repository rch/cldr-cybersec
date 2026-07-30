"""Cluster configuration for sample notebooks — no hard-coded lab secrets.

Values are injected by JupyterHub singleuser ``extraEnv`` from Zarf vars that
converge passes at deploy (``--creds-file`` / ``S3_*`` / worker sizing).

Usage in a notebook first cell::

    import sys
    for p in ("/root/sample-notebooks", "/app", "/root"):
        if p not in sys.path:
            sys.path.insert(0, p)
    from cluster_env import load_cluster_config
    CFG = load_cluster_config()
    print(CFG.summary())

Never embed access keys in notebooks — only ``os.environ``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _truthy(name: str, default: str = "0") -> bool:
    return _env(name, default).lower() in ("1", "true", "yes", "on")


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """s3://bucket/prefix/ → (bucket, prefix) with no leading/trailing slashes on prefix."""
    u = (uri or "").strip()
    if u.startswith("s3://"):
        rest = u[5:].strip("/")
        if "/" in rest:
            b, p = rest.split("/", 1)
            return b, p.strip("/")
        return rest, ""
    return u.strip("/"), ""


@dataclass
class ClusterConfig:
    """Resolved runtime config for notebooks on the air-gap / RKE2 stack."""

    s3_bucket: str
    s3_endpoint: str
    s3_region: str
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_session_token: str
    otel_data_path: str  # s3://bucket/prefix/ from panel ConfigMap path
    otel_prefix: str  # prefix only (e.g. otel-notebook)
    dask_scheduler: str
    use_dask: bool = True
    # Optional sizing / profile knobs (also env)
    hdf5_profile: str = "lab"
    extras: Dict[str, str] = field(default_factory=dict)

    @property
    def dataset_root_key(self) -> str:
        """bucket/prefix for s3fs paths (no s3://)."""
        if self.otel_prefix:
            return f"{self.s3_bucket}/{self.otel_prefix}".rstrip("/")
        return self.s3_bucket

    @property
    def spans_glob(self) -> str:
        """OTEL_Data_Generator layout: {root}/spans/date=*/*.parquet (no hour=)."""
        return f"s3://{self.dataset_root_key}/spans/date=*/*.parquet"

    @property
    def spans_prefix_s3(self) -> str:
        return f"s3://{self.dataset_root_key}/spans/"

    def storage_options(self) -> Dict[str, Any]:
        """Kwargs for ``s3fs.S3FileSystem`` / ``dd.read_parquet(..., storage_options=)``."""
        opts: Dict[str, Any] = {
            "anon": False,
            "key": self.aws_access_key_id or None,
            "secret": self.aws_secret_access_key or None,
        }
        if self.aws_session_token:
            opts["token"] = self.aws_session_token
        if self.s3_endpoint:
            opts["client_kwargs"] = {
                "endpoint_url": self.s3_endpoint,
                "region_name": self.s3_region or "us-east-1",
            }
            # path-style for MinIO / gateways; timeouts avoid multi-minute hangs
            opts["config_kwargs"] = {
                "s3": {"addressing_style": "path"},
                "signature_version": "s3v4",
                "connect_timeout": 5,
                "read_timeout": 60,
                "retries": {"max_attempts": 3, "mode": "standard"},
            }
        elif self.s3_region:
            opts["client_kwargs"] = {"region_name": self.s3_region}
        return opts

    def pyarrow_s3_kwargs(self) -> Dict[str, Any]:
        """Kwargs for ``pyarrow.fs.S3FileSystem`` (writes / small metadata only)."""
        kw: Dict[str, Any] = {
            "region": self.s3_region or "us-east-1",
            "access_key": self.aws_access_key_id or "",
            "secret_key": self.aws_secret_access_key or "",
        }
        if self.aws_session_token:
            kw["session_token"] = self.aws_session_token
        if self.s3_endpoint:
            kw["endpoint_override"] = self.s3_endpoint
            kw["scheme"] = "https" if self.s3_endpoint.startswith("https") else "http"
        return kw

    def connect_dask(self):
        """Return a ``distributed.Client`` (requires dask.distributed)."""
        from dask.distributed import Client

        if not self.dask_scheduler:
            raise RuntimeError(
                "DASK_SCHEDULER_ADDRESS not set — JupyterHub should inject it from "
                "the in-cluster scheduler Service"
            )
        return Client(self.dask_scheduler)

    def summary(self) -> str:
        lines = [
            "ClusterConfig (from env — no notebook hard-codes)",
            f"  S3_BUCKET          = {self.s3_bucket}",
            f"  S3_ENDPOINT        = {self.s3_endpoint or '(AWS default)'}",
            f"  S3_REGION          = {self.s3_region}",
            f"  OTEL_DATA_PATH     = {self.otel_data_path or '(unset)'}",
            f"  otel_prefix        = {self.otel_prefix or '(bucket root)'}",
            f"  spans              = {self.spans_prefix_s3}",
            f"  DASK_SCHEDULER     = {self.dask_scheduler}",
            f"  USE_DASK           = {self.use_dask}",
            f"  AWS_ACCESS_KEY_ID  = {'set' if self.aws_access_key_id else 'empty'} "
            f"(len={len(self.aws_access_key_id)})",
            f"  AWS_SECRET         = {'set' if self.aws_secret_access_key else 'empty'} "
            f"(len={len(self.aws_secret_access_key)})",
            f"  HDF5_PROFILE       = {self.hdf5_profile}",
        ]
        return "\n".join(lines)


def load_cluster_config() -> ClusterConfig:
    """Build config from process environment (JupyterHub singleuser extraEnv)."""
    bucket = _env("S3_BUCKET")
    otel_path = _env("OTEL_DATA_PATH")
    if not otel_path and bucket:
        # Match panel default when only bucket is set
        otel_path = f"s3://{bucket}/"
    b_from_path, prefix = _parse_s3_uri(otel_path) if otel_path else ("", "")
    if not bucket:
        bucket = b_from_path or "cyberphy"
    # Explicit prefix env wins (optional override)
    prefix = _env("OTEL_PREFIX") or _env("OTEL_DATASET_PREFIX") or prefix
    # If OTEL_DATA_PATH is s3://bucket/ only, allow PREFIX default for generators
    if not prefix:
        # Field default matches panel OTEL_DATA_PATH …/otel-notebook/ (not validation-30gb)
        prefix = _env("PREFIX", "") or "otel-notebook"

    region = (
        _env("AWS_REGION")
        or _env("AWS_DEFAULT_REGION")
        or _env("S3_REGION")
        or "us-east-1"
    )
    scheduler = (
        _env("DASK_SCHEDULER_ADDRESS")
        or _env("DASK_SCHEDULER")
        or "tcp://cybersec-dask-scheduler.dask.svc.cluster.local:8786"
    )

    return ClusterConfig(
        s3_bucket=bucket,
        s3_endpoint=_env("S3_ENDPOINT"),
        s3_region=region,
        aws_access_key_id=_env("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=_env("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=_env("AWS_SESSION_TOKEN"),
        otel_data_path=otel_path or f"s3://{bucket}/",
        otel_prefix=prefix,
        dask_scheduler=scheduler,
        use_dask=_env("USE_DASK", "1").lower() not in ("0", "false", "no", "off"),
        hdf5_profile=_env("HDF5_PROFILE", "lab") or "lab",
        extras={
            k: _env(k)
            for k in (
                "DASK_WORKER_REPLICAS",
                "DASK_WORKER_NTHREADS",
                "DASK_WORKER_MEMORY",
                "HDF5_FORCE_REGENERATE",
                "DASK_VIZ_FRAC",
            )
            if _env(k)
        },
    )


def require_parquet_stack() -> dict:
    """Fail loud if Dask/PyArrow cannot be used for ``dd.read_parquet(..., engine='pyarrow')``.

    The cybersec-dask image bakes pyarrow + dask and fails the image build if imports
    break. Notebooks must not silently fall back to pandas / skip distributed reads.
    """
    info: dict = {}
    try:
        import dask
        import dask.dataframe as dd
        import pyarrow as pa
        import pyarrow.parquet  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            f"Missing Dask/PyArrow in this kernel: {e}. "
            f"Use the cybersec-dask singleuser image (not a bare python kernel)."
        ) from e
    info["dask"] = getattr(dask, "__version__", "?")
    info["pyarrow"] = getattr(pa, "__version__", "?")
    # Resolve engine the same way read_parquet will
    try:
        from dask.dataframe.io.parquet.core import get_engine
        eng = get_engine("pyarrow")
        info["engine"] = f"pyarrow ({type(eng).__module__})"
    except Exception as e:
        raise RuntimeError(
            f"dask cannot load parquet engine 'pyarrow': {e}. "
            f"Install/repair pyarrow in the image; do not use engine=None/fastparquet."
        ) from e
    return info


def list_span_parquet_keys(
    cfg: Optional[ClusterConfig] = None,
    *,
    max_list: int = 500_000,
) -> list:
    """List parquet object keys under ``{bucket}/{prefix}/spans/`` (recursive).

    Layouts (in priority order for discovery):
      1. **OTEL_Data_Generator (pyarrow notebook)** — ``spans/date=YYYY-MM-DD/*.parquet``
         (no hour= partition; batch_*.parquet directly under date=)
      2. Production-style — ``spans/date=*/hour=*/*.parquet``
      3. Shard layouts — ``spans/shard=*/date=*/…``

    Prefer ``find()`` then explicit globs. Never use ``**`` (s3fs often empty).
    """
    import s3fs

    cfg = cfg or load_cluster_config()
    fs = s3fs.S3FileSystem(**cfg.storage_options())
    root = cfg.dataset_root_key
    spans = f"{root.rstrip('/')}/spans"
    files: list = []

    # 1) Recursive find under spans/ (covers date-only and date/hour trees)
    try:
        if fs.exists(spans):
            found = fs.find(spans)
            files = [e for e in found if str(e).endswith(".parquet")]
    except Exception:
        files = []

    # 2) Explicit globs — notebook generator first (date=*/*.parquet, no hour=)
    if not files:
        for pat in (
            f"{spans}/date=*/*.parquet",  # OTEL_Data_Generator notebook
            f"{spans}/date=*/batch_*.parquet",
            f"{spans}/date=*/hour=*/*.parquet",  # production / 1TB style
            f"{spans}/date=*/*/*.parquet",
            f"{spans}/shard=*/date=*/*.parquet",
            f"{spans}/shard=*/date=*/batch_*.parquet",
            f"{spans}/shard=*/date=*/*/*.parquet",
        ):
            try:
                hits = [h for h in (fs.glob(pat) or []) if str(h).endswith(".parquet")]
            except Exception:
                hits = []
            if hits:
                files = hits
                break

    # 3) One-level ls of each date= partition (robust when glob is picky)
    if not files:
        try:
            date_dirs = fs.glob(f"{spans}/date=*") or []
            for d in date_dirs:
                try:
                    for name in fs.ls(d):
                        if str(name).endswith(".parquet"):
                            files.append(name)
                        else:
                            # hour= subdir
                            try:
                                for sub in fs.ls(name):
                                    if str(sub).endswith(".parquet"):
                                        files.append(sub)
                            except Exception:
                                pass
                except Exception:
                    continue
        except Exception:
            pass

    if len(files) > max_list:
        files = files[:max_list]
    return files


def load_active_spans_ddf(
    cfg: Optional[ClusterConfig] = None,
    *,
    columns: Optional[list] = None,
    require_dask: bool = True,
):
    """Lazy Dask DataFrame over partitioned span parquet — never full pyarrow load.

    Default field layout from **OTEL_Data_Generator** notebook::

        s3://{bucket}/{prefix}/spans/date=YYYY-MM-DD/batch_*.parquet

    (date= only — no hour=). Also accepts production date=/hour= trees.
    Always uses ``engine="pyarrow"`` (explicit — no silent engine auto-skip).
    """
    import dask.dataframe as dd

    stack = require_parquet_stack()
    print(f"parquet stack: dask={stack['dask']} pyarrow={stack['pyarrow']} engine={stack['engine']}")

    cfg = cfg or load_cluster_config()
    if require_dask and not cfg.use_dask:
        raise RuntimeError("USE_DASK=0 but load_active_spans_ddf requires Dask for large data")

    spans = f"{cfg.dataset_root_key.rstrip('/')}/spans"
    files = list_span_parquet_keys(cfg)
    if not files:
        raise FileNotFoundError(
            f"no parquet under s3://{spans}/.\n"
            f"OTEL_Data_Generator writes: s3://{spans}/date=YYYY-MM-DD/*.parquet\n"
            f"(date= only — no hour=). Not validation-30gb; not date=*/**/*.\n"
            f"Empty columns after a bad glob is PATH drift, not schema drift.\n"
            f"Fix: OTEL_DATA_PATH / prefix so root is …/otel-notebook/spans/."
        )
    uris = [f"s3://{f}" if not str(f).startswith("s3://") else str(f) for f in files]
    sample = uris[:3]
    # Detect layout for operator feedback
    layout = "date=/*.parquet"
    if any("/hour=" in u for u in sample):
        layout = "date=/hour=/*.parquet"
    print(
        f"load_active_spans_ddf: {len(uris)} parquet under s3://{spans}/ "
        f"(layout≈{layout})\n  sample: {sample}"
    )
    # Explicit URI list — never pass a glob string to dd.read_parquet.
    # Glob expansion without path-style + timeouts hangs on custom S3 endpoints
    # (field notebooks-03: correct date=* path still hung).
    ddf = dd.read_parquet(
        uris,
        storage_options=cfg.storage_options(),
        columns=columns,
        engine="pyarrow",
    )
    cols = list(getattr(ddf, "columns", []) or [])
    if not cols:
        raise RuntimeError(
            f"dd.read_parquet returned columns=[] for {len(uris)} files under "
            f"s3://{spans}/. Check storage_options / credentials; sample={sample}"
        )
    return ddf


def paste_load_spans_cell() -> str:
    """Return a self-contained cell for *old* in-situ notebooks (no package wait).

    Fixes notebooks-03 hang: old cells call ``dd.read_parquet(glob, storage_options)``
    without path-style addressing / timeouts, and block forever on custom endpoints.
    """
    return r'''# --- SAFE load (paste over hung dd.read_parquet cell) ---
# OTEL generator layout: s3://$BUCKET/$PREFIX/spans/date=YYYY-MM-DD/*.parquet
# Do NOT pass globs to dd.read_parquet; do NOT omit path-style on custom endpoints.
import os, sys
for _p in ("/root/sample-notebooks", "/root", "/app"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from cluster_env import load_cluster_config, list_span_parquet_keys, load_active_spans_ddf
    CFG = load_cluster_config()
    print(CFG.summary())
    keys = list_span_parquet_keys(CFG)
    print(f"listed {len(keys)} parquet; sample={keys[:3]}")
    if not keys:
        raise FileNotFoundError(f"no files under {CFG.spans_prefix_s3}")
    ddf = load_active_spans_ddf(CFG)
except ImportError:
    # Fallback without cluster_env — still path-style + explicit file list
    import s3fs
    import dask.dataframe as dd
    endpoint = os.environ.get("S3_ENDPOINT") or ""
    bucket = os.environ.get("S3_BUCKET") or "dhfo"
    prefix = os.environ.get("OTEL_PREFIX") or "otel-notebook"
    opts = {
        "anon": False,
        "key": os.environ.get("AWS_ACCESS_KEY_ID") or None,
        "secret": os.environ.get("AWS_SECRET_ACCESS_KEY") or None,
        "token": os.environ.get("AWS_SESSION_TOKEN") or None,
    }
    if endpoint:
        opts["client_kwargs"] = {"endpoint_url": endpoint, "region_name": os.environ.get("AWS_REGION", "us-east-1")}
        opts["config_kwargs"] = {
            "s3": {"addressing_style": "path"},
            "connect_timeout": 5,
            "read_timeout": 30,
            "retries": {"max_attempts": 2, "mode": "standard"},
        }
    fs = s3fs.S3FileSystem(**opts)
    spans = f"{bucket}/{prefix}/spans"
    print(f"listing s3://{spans}/ (find, no glob)…")
    keys = [k for k in fs.find(spans) if str(k).endswith(".parquet")]
    print(f"found {len(keys)}; sample={keys[:3]}")
    if not keys:
        # date-only generator layout
        keys = [k for k in (fs.glob(f"{spans}/date=*/*.parquet") or []) if str(k).endswith(".parquet")]
        print(f"glob date=*/* → {len(keys)}")
    if not keys:
        raise FileNotFoundError(f"no parquet under s3://{spans}/date=*/*.parquet")
    uris = [f"s3://{k}" if not str(k).startswith("s3://") else k for k in keys]
    ddf = dd.read_parquet(uris, storage_options=opts, engine="pyarrow")

print("partitions", ddf.npartitions, "columns", list(ddf.columns))
assert list(ddf.columns), "columns=[] — still path/creds; not schema"
ddf
'''
