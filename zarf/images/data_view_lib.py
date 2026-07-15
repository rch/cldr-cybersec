"""Process-stable I/O for Data-View.

panel serve re-executes data-view.py per browser session and can leave that
session module without ``__builtins__``. Background discover threads then die
with ``KeyError: '__builtins__'`` and the histogram never gets data.

This module is a normal ``import`` target (lives in ``sys.modules``) so its
globals stay intact for the life of the process.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

logger = logging.getLogger("data-view")

S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_SESSION_TOKEN = os.environ.get("AWS_SESSION_TOKEN", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
VPC_FLOW_PREFIX = os.environ.get("VPC_FLOW_PREFIX", "vpc-flow").strip("/")

WINDOW_MIN = int(os.environ.get("DATA_VIEW_WINDOW_MIN", "15"))
_DEFAULT_MAX_FILES = max(WINDOW_MIN * 6 + 12, 48)
MAX_FILES = int(os.environ.get("DATA_VIEW_MAX_FILES", str(_DEFAULT_MAX_FILES)))
CACHE_TTL_S = float(os.environ.get("DATA_VIEW_CACHE_TTL_S", "120"))

_FRAME_CACHE: dict[str, Any] = {
    "sig": "",
    "df": None,
    "paths": [],
    "cursor": {},
    "ts": 0.0,
    "load_ms": 0.0,
}
_FRAME_CACHE_LOCK = threading.Lock()


def storage_options() -> dict:
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


def s3fs_client():
    import s3fs

    return s3fs.S3FileSystem(**storage_options())


def read_cursor(fs) -> dict:
    if not S3_BUCKET:
        return {}
    path = "%s/%s/_stream_cursor.json" % (S3_BUCKET, VPC_FLOW_PREFIX)
    for attempt in range(3):
        try:
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
    """List parquet under recent minute= prefixes only (O(window) lists)."""
    if not S3_BUCKET:
        return []
    root = "%s/%s" % (S3_BUCKET, VPC_FLOW_PREFIX)
    now = datetime.now(timezone.utc)
    kept: list[str] = []
    for i in range(int(window_min) + 1):
        dt = now - timedelta(minutes=i)
        prefix = "%s/date=%s/hour=%s/minute=%s" % (
            root,
            dt.strftime("%Y-%m-%d"),
            dt.strftime("%H"),
            dt.strftime("%M"),
        )
        try:
            try:
                fs.invalidate_cache(prefix)
            except Exception:
                pass
            entries = fs.ls(prefix, detail=False)
        except (FileNotFoundError, OSError, PermissionError):
            continue
        except Exception as e:
            logger.debug("ls %s: %s", prefix, e)
            continue
        for p in entries:
            ps = str(p)
            if not ps.endswith(".parquet"):
                continue
            if "_stream" in ps:
                continue
            kept.append(ps if ps.startswith("s3://") else ("s3://%s" % ps))

    kept = sorted(set(kept))
    if len(kept) > MAX_FILES:
        kept = kept[-MAX_FILES:]
    return kept


def _pyarrow_s3():
    from pyarrow import fs as pafs

    kwargs: dict[str, Any] = {"region": AWS_REGION or "us-east-1"}
    if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
        kwargs["access_key"] = AWS_ACCESS_KEY_ID
        kwargs["secret_key"] = AWS_SECRET_ACCESS_KEY
        if AWS_SESSION_TOKEN:
            kwargs["session_token"] = AWS_SESSION_TOKEN
    if S3_ENDPOINT:
        host = S3_ENDPOINT.split("://", 1)[-1]
        kwargs["endpoint_override"] = host
        kwargs["scheme"] = "http" if S3_ENDPOINT.startswith("http://") else "https"
    return pafs.S3FileSystem(**kwargs)


def load_frame(paths: list[str]) -> pd.DataFrame:
    """Load parquet paths. Prefer pyarrow.dataset (batch); fall back to sequential."""
    if not paths:
        return pd.DataFrame()
    keys = [p.replace("s3://", "") for p in paths]
    try:
        import pyarrow.dataset as ds

        s3 = _pyarrow_s3()
        dataset = ds.dataset(keys, filesystem=s3, format="parquet")
        df = dataset.to_table().to_pandas()
        if "start" in df.columns:
            df["start"] = pd.to_datetime(df["start"], utc=True, errors="coerce")
        return df
    except Exception as e:
        logger.warning("dataset load failed (%s); sequential fallback", e)

    try:
        import pyarrow.parquet as pq

        frames = []
        fs = s3fs_client()
        for key in keys:
            try:
                with fs.open(key, "rb") as f:
                    frames.append(pq.read_table(f).to_pandas())
            except FileNotFoundError:
                continue
            except Exception as ex:
                logger.debug("skip %s: %s", key, ex)
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        if "start" in df.columns:
            df["start"] = pd.to_datetime(df["start"], utc=True, errors="coerce")
        return df
    except Exception as e:
        logger.exception("load_frame failed: %s", e)
        return pd.DataFrame()


def cache_get() -> dict[str, Any] | None:
    with _FRAME_CACHE_LOCK:
        df = _FRAME_CACHE.get("df")
        ts = float(_FRAME_CACHE.get("ts") or 0)
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return None
        if time.time() - ts > CACHE_TTL_S:
            return None
        return {
            "sig": _FRAME_CACHE.get("sig") or "",
            "df": df,
            "paths": list(_FRAME_CACHE.get("paths") or []),
            "cursor": dict(_FRAME_CACHE.get("cursor") or {}),
            "load_ms": float(_FRAME_CACHE.get("load_ms") or 0),
            "ts": ts,
        }


def cache_put(*, sig: str, df, paths: list, cursor: dict, load_ms: float) -> None:
    with _FRAME_CACHE_LOCK:
        _FRAME_CACHE["sig"] = sig
        _FRAME_CACHE["df"] = df
        _FRAME_CACHE["paths"] = paths
        _FRAME_CACHE["cursor"] = cursor
        _FRAME_CACHE["ts"] = time.time()
        _FRAME_CACHE["load_ms"] = load_ms


def _col_match_mask(col: pd.Series, value, *, equal: bool) -> pd.Series:
    """Match facet/text filter values across numeric and string columns.

    Facet buttons always pass string labels (from value_counts.astype(str)),
    while parquet columns may be int/float/bool — compare both ways.
    """
    sval = str(value).strip()
    as_str = col.astype(str)
    # Normalize "443.0" style float strings from pandas.
    as_str_norm = as_str.str.replace(r"\.0$", "", regex=True)
    mask_str = as_str == sval
    mask_norm = as_str_norm == sval
    mask = mask_str | mask_norm
    if pd.api.types.is_numeric_dtype(col) or pd.api.types.is_bool_dtype(col):
        try:
            value_n = pd.to_numeric(sval)
            mask = mask | (col == value_n)
        except Exception:
            pass
        # bool facets may show "True"/"False"
        if sval.lower() in ("true", "false", "1", "0"):
            try:
                mask = mask | (col.astype(bool) == (sval.lower() in ("true", "1")))
            except Exception:
                pass
    return mask if equal else ~mask


def apply_filters(df: pd.DataFrame, filters: list[dict]) -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty or not filters:
        return df if df is not None else pd.DataFrame()
    out = df
    for f in filters:
        field, op, value = f.get("field"), f.get("op", "=="), f.get("value")
        if not field or field not in out.columns:
            logger.debug("filter skip unknown field %s", field)
            continue
        try:
            col = out[field]
            if op in ("==", "eq", None, ""):
                out = out[_col_match_mask(col, value, equal=True)]
            elif op in ("!=", "ne"):
                out = out[_col_match_mask(col, value, equal=False)]
            else:
                logger.warning("filter unsupported op %r on %s", op, field)
        except Exception as e:
            logger.warning("filter apply %s: %s", f, e)
    return out


def catalog_sig(window_min: int) -> dict[str, Any]:
    """List-only: return cursor + paths + signature without loading parquet."""
    t0 = time.perf_counter()
    if not S3_BUCKET:
        return {"error": "S3_BUCKET not set"}
    fs = s3fs_client()
    cur = read_cursor(fs)
    catalog = str(
        cur.get("catalog_id")
        or cur.get("rows_written")
        or cur.get("last_path")
        or cur.get("last_partition")
        or ""
    )
    paths = list_recent_parquet(fs, window_min)
    sig = "%s|%s|%s|%s" % (
        catalog,
        len(paths),
        paths[-1] if paths else "",
        paths[0] if paths else "",
    )
    return {
        "sig": sig,
        "paths": paths,
        "cursor": cur,
        "list_ms": (time.perf_counter() - t0) * 1000,
    }


def discover(window_min: int, *, skip_if_sig: str | None = None) -> dict[str, Any]:
    """List + load recent window. Safe to call from any thread / session.

    If ``skip_if_sig`` matches the current catalog signature, returns
    ``unchanged=True`` without re-reading parquet.
    """
    t0 = time.perf_counter()
    meta = catalog_sig(window_min)
    if meta.get("error"):
        return meta
    sig = meta["sig"]
    paths = meta["paths"]
    cur = meta["cursor"]
    if skip_if_sig is not None and sig == skip_if_sig:
        return {
            "df": None,
            "sig": sig,
            "paths": paths,
            "cursor": cur,
            "load_ms": (time.perf_counter() - t0) * 1000,
            "unchanged": True,
        }
    df = load_frame(paths)
    load_ms = (time.perf_counter() - t0) * 1000
    cache_put(sig=sig, df=df, paths=paths, cursor=cur, load_ms=load_ms)
    logger.info(
        "bg discover sig=%s files=%s rows=%s load_ms=%.0f",
        sig[:48],
        len(paths),
        len(df),
        load_ms,
    )
    return {
        "df": df,
        "sig": sig,
        "paths": paths,
        "cursor": cur,
        "load_ms": load_ms,
        "unchanged": False,
    }
