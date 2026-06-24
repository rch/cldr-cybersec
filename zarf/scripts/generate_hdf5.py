#!/usr/bin/env python3
"""Synthetic HDF5 telemetry generator (OpenTelemetry-framed).

Generates synthetic OpenTelemetry datasets in HDF5 and lands them in S3 (MinIO
locally, AWS S3 in the cloud), to exercise our OTel N-D access patterns against a
dense source in addition to the row-oriented span/metric tables. The container
layout is a faithful, **domain-neutral structural analog** of the dense telemetry
HDF5 a collector hands us — same shape, reframed entirely in OTel terms:

  * a depth-4 group tree (root -> ResourceMetrics -> {Scope, Resource[i], Metric[i]}
    -> ScopeAnchors[j] -> dataset), 7 groups, 5 datasets,
  * rich node-level metadata: 56 attributes whose per-node CARDINALITY and TYPE mix
    mirror the reference (fixed-length |S strings incl. |S36 uuids / |S32 ISO
    timestamps, float64 value + ".unit" string pairs, int64/int32 counts, bool flags),
  * one dominant 2D "values" array  (series x time)  +  a 1D int64 timestamp index,
  * compound side tables: Windows (i4,i4,i4) viewport ranges, SeriesAnchor (i8,f8,f8)
    resource/scope anchors (a short 2-row table + a full series-length table),
  * chunking tuned for hyperslab / viewport reads (pan/zoom -> sub-region GET) with
    `fill_time = NEVER` so chunks are not initialized at creation; `--contiguous`
    reproduces the reference's unchunked storage layout exactly instead.

Why this maps onto the OTel Navigator access pattern:

    Navigator viewport (RangeXY)   ==  hyperslab (series_lo:series_hi, t_lo:t_hi)
    filter-before-rasterize        ==  read only the selected hyperslab
    datashader rasterization       ==  identical, over the selected 2D block
    time-window query              ==  hyperslab on the time axis

Dask workers read *independent* hyperslabs (separate processes, no MPI communicator),
so chunk layout + S3 ranged reads -- not collective I/O -- govern latency. With
--emit-kerchunk we also write a reference manifest so the chunks can be read lazily as
a virtual Zarr through fsspec/Dask, fetching only the chunks a viewport covers.

Usage:
    python generate_hdf5.py --out s3://cybersec/hdf5/ --n-series 5001 --n-time 10000
    python generate_hdf5.py --out ./local_hdf5 --n-parts 4 --emit-kerchunk
    python generate_hdf5.py --out ./local_hdf5 --contiguous   # match reference storage

Dependencies (not in the base env): h5py, numpy; boto3 for S3; kerchunk for reference
emit. Quick run:
    uv run --with h5py,numpy,boto3,kerchunk python zarf/scripts/generate_hdf5.py ...
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import numpy as np

try:
    import h5py
except ImportError:  # pragma: no cover
    raise SystemExit(
        "h5py is required: uv run --with h5py,numpy python zarf/scripts/generate_hdf5.py ..."
    )

SCHEMA_VERSION = "2.0"
OTEL_SCHEMA_URL = "https://opentelemetry.io/schemas/1.27.0"
DEFAULT_DTYPE = "int16"


# ---------------------------------------------------------------------------
# Attribute helpers — reproduce the reference's fixed-length |S typing and the
# numeric value + ".unit" string pairing, with OpenTelemetry-flavoured keys.
# ---------------------------------------------------------------------------
def _fix(node, name: str, s: str) -> None:
    """Write a FIXED-LENGTH ASCII string attribute sized to its content (|S<len>),
    matching the reference's |S36 / |S32 / |S2 ... attribute typing (h5py's default
    for a Python str is a variable-length UTF-8 string, which we deliberately avoid)."""
    b = s.encode("ascii", "replace")
    node.attrs.create(name, np.bytes_(b), dtype=np.dtype(f"S{max(1, len(b))}"))


def _pair(node, name: str, value: float, unit: str) -> None:
    """A float64 metric value plus its ".unit" fixed-string sibling — the reference's
    `GaugeLength` / `GaugeLength.uom` pattern, which is exactly an OTel metric's
    (value, unit). Counts as TWO node attributes."""
    node.attrs.create(name, np.float64(value))
    _fix(node, name + ".unit", unit)


def _uuid() -> str:
    return str(uuid.uuid4())  # 36 chars -> |S36, like the reference's id attributes


def _iso(ns: int) -> str:
    """ISO-8601 micros + UTC offset -> 32 chars -> |S32, like the reference timestamps."""
    dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=ns // 1000)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "+00:00"


# ---------------------------------------------------------------------------
# Value model (unchanged): a representative (series x time) telemetry block.
# ---------------------------------------------------------------------------
def synth_signal(n_series: int, n_time: int, rng: np.random.Generator, dtype: str) -> np.ndarray:
    """Compose three observability-flavoured components so datashader renders real
    spatio-temporal structure -- not flat noise -- under viewport/hyperslab reads:

      baseline  - per-series level + Gaussian jitter (steady-state emit)
      periodic  - shared load oscillation (correlated background demand)
      cascades  - occasional anomalies that spread across a contiguous band of
                  dependent series with a per-series onset offset (a correlated
                  incident propagating along a dependency chain)
    """
    t = np.arange(n_time, dtype=np.float32)
    base_level = rng.uniform(40, 120, size=(n_series, 1)).astype(np.float32)
    sig = base_level + rng.normal(0, 8, size=(n_series, n_time)).astype(np.float32)

    n_cycles = float(rng.uniform(2, 6))
    phase = rng.uniform(0, 2 * np.pi, size=(n_series, 1)).astype(np.float32)
    sig += 15.0 * np.sin(2 * np.pi * n_cycles * t / max(n_time, 1) + phase).astype(np.float32)

    n_events = max(1, int(n_series * n_time / 5_000_000) + int(rng.integers(1, 4)))
    for _ in range(n_events):
        s0 = int(rng.integers(0, n_series))
        band = int(rng.integers(20, max(21, n_series // 8)))
        t0 = int(rng.integers(0, n_time))
        offset = float(rng.uniform(0.2, 2.0))
        width = int(rng.integers(30, 200))
        amp = float(rng.uniform(60, 200))
        for k in range(band):
            s = s0 + k
            if s >= n_series:
                break
            c = int(t0 + k * offset)
            lo, hi = max(0, c - width), min(n_time, c + width)
            if lo >= hi:
                continue
            env = amp * np.exp(-((np.arange(lo, hi) - c) ** 2) / (2 * (width / 3.0) ** 2))
            sig[s, lo:hi] += env.astype(np.float32)

    if np.dtype(dtype).kind in ("i", "u"):
        info = np.iinfo(dtype)
        sig = np.clip(np.rint(sig), info.min, info.max)
    return sig.astype(dtype)


# ---------------------------------------------------------------------------
# HDF5 writer
# ---------------------------------------------------------------------------
def _create_values_dataset(group, name: str, shape, dtype: str, chunks):
    """The primary 2D array. chunks=None -> CONTIGUOUS (matches the reference's
    unchunked storage). Otherwise chunked with fill_time = NEVER (chunks not
    initialized at creation -- the allocation cost parallel-HDF5 guidance flags --
    safe because every chunk is fully written exactly once below)."""
    if chunks is None:
        return group.create_dataset(name, shape=tuple(shape), dtype=np.dtype(dtype))
    space = h5py.h5s.create_simple(tuple(shape))
    dcpl = h5py.h5p.create(h5py.h5p.DATASET_CREATE)
    dcpl.set_chunk(tuple(chunks))
    dcpl.set_fill_time(h5py.h5d.FILL_TIME_NEVER)
    tid = h5py.h5t.py_create(np.dtype(dtype), logical=True)
    dsid = h5py.h5d.create(group.id, name.encode("utf-8"), tid, space, dcpl)
    return h5py.Dataset(dsid)


def write_part(path: str, part_idx: int, args, rng: np.random.Generator) -> str:
    """Write one HDF5 part: the OTel-framed structural analog of the reference file.
    Per-node attribute cardinality matches the reference exactly (1/21/9/4/2/2/6/5/6)."""
    n_series, n_time = args.n_series, args.n_time
    chunks = None if args.contiguous else (min(args.chunk_series, n_series),
                                           min(args.chunk_time, n_time))
    si_s = args.sampling_interval_ms / 1000.0
    step_ns = int(args.sampling_interval_ms * 1_000_000)
    base_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
    start_ns = base_ns + part_idx * n_time * step_ns
    end_ns = start_ns + n_time * step_ns

    with h5py.File(path, "w", libver="latest") as f:
        # --- root (1 attr) ----------------------------------------------------
        _fix(f, "collection.uuid", _uuid())

        # --- ResourceMetrics (≈ top collection group; 21 attrs) ---------------
        rm = f.create_group("ResourceMetrics")
        # 6 float64 value + ".unit" pairs (12)
        _pair(rm, "collection.interval", si_s, "s")
        _pair(rm, "export.timeout", float(args.export_timeout_s), "s")
        _pair(rm, "sample.rate.max", float(1.0 / si_s) if si_s else 0.0, "Hz")
        _pair(rm, "sample.rate.min", 0.0, "Hz")
        _pair(rm, "batch.flush.latency", float(rng.uniform(1.0, 9.0)), "ms")
        _pair(rm, "spatial.resolution", float(args.spatial_resolution), "m")
        # 2 int64
        rm.attrs.create("series.count", np.int64(n_series))
        rm.attrs.create("start.series.index", np.int64(args.start_series_index))
        # 1 bool
        rm.attrs.create("aggregation.temporality.is_delta", np.bool_(False))
        # 6 fixed strings (2 are |S36 uuids, 1 is a |S32 timestamp)
        _fix(rm, "schema.url", OTEL_SCHEMA_URL)
        _fix(rm, "collection.id", _uuid())
        _fix(rm, "service.name", args.service_name)
        _fix(rm, "service.instance.id", _uuid())
        _fix(rm, "start.time", _iso(start_ns))
        _fix(rm, "otel.schema.version", SCHEMA_VERSION)

        # --- ResourceMetrics/Scope (≈ instrumentation scope / SDK config; 9) ---
        scope = rm.create_group("Scope")
        # 3 int32
        scope.attrs.create("batch.max.size", np.int32(args.batch_max_size))
        scope.attrs.create("queue.capacity", np.int32(2048))
        scope.attrs.create("export.retry.count", np.int32(5))
        # 2 float64
        scope.attrs.create("sampling.ratio", np.float64(1.0))
        scope.attrs.create("compression.ratio", np.float64(float(rng.uniform(1.5, 4.0))))
        # 2 bool
        scope.attrs.create("data.transposed", np.bool_(True))
        scope.attrs.create("values.relative", np.bool_(False))
        # 2 fixed strings
        _fix(scope, "telemetry.sdk.name", "opentelemetry")
        _fix(scope, "telemetry.sdk.language", "python")
        # Scope/Windows — compound (i4,i4,i4) viewport ranges (≈ reference Zones)
        win_dt = np.dtype([("StartIndex", "<i4"), ("EndIndex", "<i4"), ("Stride", "<i4")])
        n_win = max(1, n_time // max(args.chunk_time, 1))
        edges = np.linspace(0, n_time, n_win + 1).astype(np.int32)
        windows = np.zeros(n_win, dtype=win_dt)
        windows["StartIndex"], windows["EndIndex"], windows["Stride"] = edges[:-1], edges[1:], 1
        scope.create_dataset("Windows", data=windows)

        # --- ResourceMetrics/Resource[0] (≈ resource; 4 attrs) ----------------
        res = rm.create_group("Resource[0]")
        _fix(res, "resource.schema.url", OTEL_SCHEMA_URL)
        _fix(res, "service.namespace", args.service_namespace)
        _fix(res, "host.name", args.service_name + "-host")
        _fix(res, "host.arch", "amd64")
        # two indexed scope-anchor groups, each with a compound side table
        anchor_dt = np.dtype(
            [("SeriesIndex", "<i8"), ("ResourceOffset", "<f8"), ("ScopeOffset", "<f8")]
        )
        for ai in range(2):
            sa = res.create_group(f"ScopeAnchors[{ai}]")  # 2 attrs each
            _fix(sa, "scope.note", "all-series" if ai else "anchor-pair")
            _fix(sa, "reference.frame", "collection-start")
            npts = 2 if ai == 0 else n_series
            anchor = np.zeros(npts, dtype=anchor_dt)
            anchor["SeriesIndex"] = np.linspace(0, n_series - 1, npts).astype(np.int64)
            anchor["ResourceOffset"] = np.linspace(0.0, 1.0, npts)
            anchor["ScopeOffset"] = 0.0
            sa.create_dataset("SeriesAnchor", data=anchor)

        # --- ResourceMetrics/Metric[0] (≈ the metric block; 6 attrs) ----------
        met = rm.create_group("Metric[0]")
        met.attrs.create("series.count", np.int64(n_series))
        met.attrs.create("start.series.index", np.int64(args.start_series_index))
        _pair(met, "output.data.rate", float(1.0 / si_s) if si_s else 0.0, "Hz")
        _fix(met, "value.unit", args.value_unit)
        _fix(met, "metric.uuid", _uuid())

        # Metric[0]/Values — the dominant 2D array (≈ RawData; 5 attrs)
        values = _create_values_dataset(met, "Values", (n_series, n_time), args.dtype, chunks)
        stripe = chunks[0] if chunks else max(1, min(n_series, 1024))
        for s0 in range(0, n_series, stripe):
            s1 = min(s0 + stripe, n_series)
            values[s0:s1, :] = synth_signal(s1 - s0, n_time, rng, args.dtype)
        values.attrs.create("count", np.int64(n_series) * np.int64(n_time))
        values.attrs.create("start.index", np.int64(0))
        _fix(values, "dimensions", "series,time")
        _fix(values, "part.start.time", _iso(start_ns))
        _fix(values, "part.end.time", _iso(end_ns))

        # Metric[0]/Timestamps — 1D int64 unix-nanos index (≈ RawDataTime; 6 attrs)
        ts = met.create_dataset(
            "Timestamps", data=(start_ns + np.arange(n_time, dtype=np.int64) * step_ns)
        )
        ts.attrs.create("count", np.int64(n_time))
        ts.attrs.create("start.index", np.int64(0))
        _fix(ts, "part.start.time", _iso(start_ns))
        _fix(ts, "part.end.time", _iso(end_ns))
        _fix(ts, "start.time", _iso(start_ns))
        _fix(ts, "unit", "ns")

    return path


# ---------------------------------------------------------------------------
# Sinks
# ---------------------------------------------------------------------------
def emit_kerchunk(local_path: str, out_json: str):
    """Write a kerchunk reference so chunks read lazily as a virtual Zarr."""
    try:
        from kerchunk.hdf import SingleHdf5ToZarr
    except ImportError:
        print("  kerchunk not installed; skipping reference (uv run --with kerchunk ...)")
        return None
    with open(local_path, "rb") as fo:
        refs = SingleHdf5ToZarr(fo, url=local_path, inline_threshold=0).translate()
    with open(out_json, "w") as f:
        json.dump(refs, f)
    return out_json


def upload_s3(local_path: str, bucket: str, key: str, endpoint: str | None):
    import boto3

    boto3.client("s3", endpoint_url=endpoint).upload_file(local_path, bucket, key)
    return f"s3://{bucket}/{key}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(
        description="Generate synthetic OTel-framed HDF5 telemetry datasets for access-pattern testing."
    )
    p.add_argument("--out", required=True, help="Output dir or s3://bucket/prefix")
    p.add_argument("--n-series", type=int, default=5001, help="Rows of the values matrix")
    p.add_argument("--n-time", type=int, default=10000, help="Time samples (columns)")
    p.add_argument("--n-parts", type=int, default=1, help="Number of HDF5 part files")
    p.add_argument("--chunk-series", type=int, default=512, help="Chunk size along series")
    p.add_argument("--chunk-time", type=int, default=2048, help="Chunk size along time")
    p.add_argument("--contiguous", action="store_true",
                   help="Unchunked contiguous storage (matches the reference layout exactly)")
    p.add_argument("--sampling-interval-ms", type=float, default=10.0)
    p.add_argument("--dtype", default=DEFAULT_DTYPE, help="Values dtype (e.g. int16, float32)")
    p.add_argument("--value-unit", default="ratio of (2^15-1) full-scale, dimensionless")
    p.add_argument("--service-name", default="synthetic-collector")
    p.add_argument("--service-namespace", default="cybersec.otel")
    p.add_argument("--start-series-index", type=int, default=0)
    p.add_argument("--export-timeout-s", type=float, default=30.0)
    p.add_argument("--batch-max-size", type=int, default=8192)
    p.add_argument("--spatial-resolution", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--emit-kerchunk", action="store_true", help="Also write a kerchunk reference")
    p.add_argument("--s3-endpoint", default=os.environ.get("S3_ENDPOINT") or None)
    args = p.parse_args()

    is_s3 = args.out.startswith("s3://")
    if is_s3:
        u = urlparse(args.out)
        bucket, prefix = u.netloc, u.path.lstrip("/")
        workdir = tempfile.mkdtemp(prefix="hdf5-gen-")
    else:
        bucket, prefix = "", ""
        workdir = args.out
        os.makedirs(workdir, exist_ok=True)

    layout = "contiguous" if args.contiguous else f"chunks=({args.chunk_series},{args.chunk_time})"
    print(
        f"Generating {args.n_parts} part(s): {args.n_series} series x {args.n_time} time, "
        f"dtype={args.dtype}, {layout} -> {args.out}"
    )

    for i in range(args.n_parts):
        rng = np.random.default_rng(args.seed + i)
        fname = f"telemetry-{i:04d}.h5"
        local = os.path.join(workdir, fname)
        write_part(local, i, args, rng)
        size = os.path.getsize(local)
        print(f"  [{i + 1}/{args.n_parts}] {fname}  ({size / 1e6:.1f} MB)")

        ref_local = None
        if args.emit_kerchunk:
            ref_local = emit_kerchunk(local, local + ".kerchunk.json")

        if is_s3:
            upload_s3(local, bucket, f"{prefix}{fname}", args.s3_endpoint)
            if ref_local:
                upload_s3(ref_local, bucket, f"{prefix}{fname}.kerchunk.json", args.s3_endpoint)
            print(f"      -> s3://{bucket}/{prefix}{fname}")

    print("Done.")


if __name__ == "__main__":
    main()
