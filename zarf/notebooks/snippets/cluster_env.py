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
        """Glob matching app layout: {root}/spans/date=*/hour=*/*.parquet."""
        root = self.dataset_root_key
        return f"s3://{root}/spans/date=*/*/*.parquet"

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


def load_active_spans_ddf(
    cfg: Optional[ClusterConfig] = None,
    *,
    columns: Optional[list] = None,
    require_dask: bool = True,
):
    """Lazy Dask DataFrame over partitioned span parquet — never full pyarrow load.

    Discovers files under ``{bucket}/{prefix}/spans/`` (date=/hour= or shard=).
    """
    import dask.dataframe as dd
    import s3fs

    cfg = cfg or load_cluster_config()
    if require_dask and not cfg.use_dask:
        raise RuntimeError("USE_DASK=0 but load_active_spans_ddf requires Dask for large data")

    fs = s3fs.S3FileSystem(**cfg.storage_options())
    root = cfg.dataset_root_key
    spans = f"{root}/spans"
    files = []
    for pat in (
        f"{spans}/date=*/hour=*/*.parquet",
        f"{spans}/date=*/*.parquet",
        f"{spans}/shard=*/date=*/*.parquet",
        f"{spans}/shard=*/date=*/batch_*.parquet",
    ):
        try:
            hits = [h for h in (fs.glob(pat) or []) if str(h).endswith(".parquet")]
        except Exception:
            hits = []
        if hits:
            files = hits
            break
    if not files and fs.exists(spans):
        files = [e for e in fs.find(spans) if str(e).endswith(".parquet")]
    if not files:
        raise FileNotFoundError(
            f"no parquet under s3://{spans}/ (partitioned layout required).\n"
            f"This is PATH config drift (wrong prefix), not schema drift — "
            f"empty columns from a missing prefix look like a missing schema.\n"
            f"Expected: s3://{{bucket}}/otel-notebook/spans/date=*/hour=*/*.parquet "
            f"(or your OTEL_DATA_PATH + /spans/).\n"
            f"Do not load validation-30gb / validation-dask unless you generated them.\n"
            f"Fix: OTEL_DATA_PATH / OTEL_PREFIX from JupyterHub env (converge → zarf)."
        )
    # dd.read_parquet wants s3:// URIs when using storage_options
    uris = [f"s3://{f}" if not str(f).startswith("s3://") else str(f) for f in files]
    return dd.read_parquet(
        uris,
        storage_options=cfg.storage_options(),
        columns=columns,
        engine="pyarrow",
    )
