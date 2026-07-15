#!/usr/bin/env python3
"""Streaming VPC Flow Log generator with fixed disk envelope.

Writes Hive-partitioned Parquet under:
  s3://{bucket}/{prefix}/date=YYYY-MM-DD/hour=HH/minute=MM/part-*.parquet

Cursor:
  s3://{bucket}/{prefix}/_stream_cursor.json

Modes:
  seed   — fill toward envelope once (bootstrap)
  stream — constant RPS forever; expire when over size/time budget

Usage:
  uv run python zarf/scripts/generate-vpc-flow.py --mode seed
  uv run python zarf/scripts/generate-vpc-flow.py --mode stream --rps 200

Env:
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_ENDPOINT, AWS_REGION
  FLOW_RPS, FLOW_MAX_BYTES, FLOW_MAX_HOURS, FLOW_PREFIX, S3_BUCKET
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    from pyarrow import fs as pafs
except ImportError:
    print("ERROR: pyarrow and numpy required", file=sys.stderr)
    sys.exit(1)


FLOW_SCHEMA = pa.schema([
    pa.field("version", pa.int32()),
    pa.field("account_id", pa.string()),
    pa.field("interface_id", pa.string()),
    pa.field("srcaddr", pa.string()),
    pa.field("dstaddr", pa.string()),
    pa.field("srcport", pa.int32()),
    pa.field("dstport", pa.int32()),
    pa.field("protocol", pa.int32()),
    pa.field("packets", pa.int64()),
    pa.field("bytes", pa.int64()),
    pa.field("start", pa.timestamp("us", tz="UTC")),
    pa.field("end", pa.timestamp("us", tz="UTC")),
    pa.field("action", pa.string()),
    pa.field("log_status", pa.string()),
    pa.field("vpc_id", pa.string()),
    pa.field("subnet_id", pa.string()),
    pa.field("instance_id", pa.string()),
    pa.field("tcp_flags", pa.int32()),
    pa.field("flow_direction", pa.string()),
    pa.field("pkt_srcaddr", pa.string()),
    pa.field("pkt_dstaddr", pa.string()),
])

WELL_KNOWN_PORTS = np.array([80, 443, 22, 53, 123, 389, 636, 3306, 5432, 6379, 8080, 8443, 25, 587], dtype=np.int32)
PROTOCOLS = np.array([6, 6, 6, 6, 17, 17, 1], dtype=np.int32)  # TCP heavy
ACTIONS = np.array(["ACCEPT", "ACCEPT", "ACCEPT", "ACCEPT", "REJECT"])
DIRECTIONS = np.array(["ingress", "egress"])
LOG_STATUS = np.array(["OK", "OK", "OK", "OK", "NODATA", "SKIPDATA"])


def _s3_fs(endpoint: str | None, region: str) -> pafs.S3FileSystem:
    kwargs: dict[str, Any] = {
        "access_key": os.environ.get("AWS_ACCESS_KEY_ID", ""),
        "secret_key": os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        "region": region,
    }
    token = os.environ.get("AWS_SESSION_TOKEN") or ""
    if token:
        kwargs["session_token"] = token
    if endpoint:
        kwargs["endpoint_override"] = endpoint.replace("https://", "").replace("http://", "")
        # pyarrow wants host:port without scheme in endpoint_override on some versions;
        # also set scheme
        if endpoint.startswith("http://"):
            kwargs["scheme"] = "http"
        else:
            kwargs["scheme"] = "https"
        # Keep full override as host:port
        host = endpoint.split("://", 1)[-1]
        kwargs["endpoint_override"] = host
    return pafs.S3FileSystem(**kwargs)


def _ip_pool(n: int = 200) -> list[str]:
    random.seed(42)
    pool = [f"10.{random.randint(0, 3)}.{random.randint(0, 255)}.{random.randint(1, 254)}" for _ in range(n)]
    pool += [f"52.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}" for _ in range(n // 2)]
    return pool


IPS = _ip_pool()
ACCOUNTS = [f"{random.randint(100000000000, 999999999999)}" for _ in range(5)]
VPCS = [f"vpc-{uuid.uuid4().hex[:8]}" for _ in range(4)]
SUBNETS = [f"subnet-{uuid.uuid4().hex[:8]}" for _ in range(12)]
ENIS = [f"eni-{uuid.uuid4().hex[:8]}" for _ in range(30)]
INSTANCES = [f"i-{uuid.uuid4().hex[:8]}" for _ in range(40)]


def generate_batch(
    n: int,
    now: datetime | None = None,
    window_sec: float = 2.0,
) -> pa.Table:
    """Vectorized synthetic VPC flow rows with event times in [now-window_sec, now]."""
    now = now or datetime.now(timezone.utc)
    window_sec = max(float(window_sec), 0.1)
    offsets = np.random.uniform(-window_sec, 0.0, size=n)
    starts = [now + timedelta(seconds=float(o)) for o in offsets]
    ends = [s + timedelta(seconds=float(x)) for s, x in zip(starts, np.random.uniform(0.001, 30.0, size=n))]

    src = np.random.choice(IPS, size=n)
    dst = np.random.choice(IPS, size=n)
    # 70% well-known dst ports
    wk = np.random.random(n) < 0.7
    dstport = np.where(wk, np.random.choice(WELL_KNOWN_PORTS, size=n), np.random.randint(1024, 65535, size=n)).astype(np.int32)
    srcport = np.random.randint(1024, 65535, size=n).astype(np.int32)
    protocol = np.random.choice(PROTOCOLS, size=n)
    action = np.random.choice(ACTIONS, size=n)
    direction = np.random.choice(DIRECTIONS, size=n)
    log_status = np.random.choice(LOG_STATUS, size=n)
    packets = np.random.randint(1, 500, size=n).astype(np.int64)
    bytes_ = (packets * np.random.randint(64, 1500, size=n)).astype(np.int64)
    tcp_flags = np.where(protocol == 6, np.random.choice([2, 16, 18, 24], size=n), 0).astype(np.int32)

    arrays = {
        "version": pa.array(np.full(n, 5, dtype=np.int32)),
        "account_id": pa.array(np.random.choice(ACCOUNTS, size=n).tolist()),
        "interface_id": pa.array(np.random.choice(ENIS, size=n).tolist()),
        "srcaddr": pa.array(src.tolist()),
        "dstaddr": pa.array(dst.tolist()),
        "srcport": pa.array(srcport),
        "dstport": pa.array(dstport),
        "protocol": pa.array(protocol),
        "packets": pa.array(packets),
        "bytes": pa.array(bytes_),
        "start": pa.array(starts, type=pa.timestamp("us", tz="UTC")),
        "end": pa.array(ends, type=pa.timestamp("us", tz="UTC")),
        "action": pa.array(action.tolist()),
        "log_status": pa.array(log_status.tolist()),
        "vpc_id": pa.array(np.random.choice(VPCS, size=n).tolist()),
        "subnet_id": pa.array(np.random.choice(SUBNETS, size=n).tolist()),
        "instance_id": pa.array(np.random.choice(INSTANCES, size=n).tolist()),
        "tcp_flags": pa.array(tcp_flags),
        "flow_direction": pa.array(direction.tolist()),
        "pkt_srcaddr": pa.array(src.tolist()),
        "pkt_dstaddr": pa.array(dst.tolist()),
    }
    return pa.table(arrays, schema=FLOW_SCHEMA)


def partition_key(dt: datetime) -> str:
    return f"date={dt.strftime('%Y-%m-%d')}/hour={dt.strftime('%H')}/minute={dt.strftime('%M')}"


def write_table(s3: pafs.S3FileSystem, base: str, table: pa.Table, now: datetime) -> tuple[str, int]:
    part = partition_key(now)
    name = f"part-{int(now.timestamp())}-{uuid.uuid4().hex[:8]}.parquet"
    path = f"{base}/{part}/{name}"
    pq.write_table(table, path, filesystem=s3, compression="zstd")
    # size estimate
    try:
        info = s3.get_file_info(path)
        size = int(info.size) if info and info.size is not None else table.nbytes
    except Exception:
        size = table.nbytes
    return path, size


def list_parts(s3: pafs.S3FileSystem, base: str) -> list[dict]:
    """Return list of {path, size, sort_key} for parquet under base."""
    out: list[dict] = []
    selector = pafs.FileSelector(base, recursive=True)
    try:
        infos = s3.get_file_info(selector)
    except Exception:
        return out
    for info in infos:
        if info.type != pafs.FileType.File:
            continue
        if not info.path.endswith(".parquet"):
            continue
        if "_stream" in info.path:
            continue
        out.append({
            "path": info.path,
            "size": int(info.size or 0),
            "mtime": info.mtime_ns or 0,
        })
    out.sort(key=lambda x: (x["mtime"], x["path"]))
    return out


def expire(
    s3: pafs.S3FileSystem,
    base: str,
    max_bytes: int,
    max_hours: float,
) -> dict:
    """Drop oldest parquet until under max_bytes and max_hours."""
    parts = list_parts(s3, base)
    if not parts:
        return {"deleted": 0, "bytes": 0, "remaining": 0}

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_hours)
    cutoff_ns = int(cutoff.timestamp() * 1e9)

    deleted = 0
    deleted_bytes = 0
    total = sum(p["size"] for p in parts)

    # First pass: age
    keep: list[dict] = []
    for p in parts:
        if p["mtime"] and p["mtime"] < cutoff_ns:
            try:
                s3.delete_file(p["path"])
                deleted += 1
                deleted_bytes += p["size"]
                total -= p["size"]
            except Exception as e:
                print(f"  expire delete failed {p['path']}: {e}", flush=True)
        else:
            keep.append(p)

    # Second pass: size (oldest first)
    keep.sort(key=lambda x: (x["mtime"], x["path"]))
    while total > max_bytes and keep:
        p = keep.pop(0)
        try:
            s3.delete_file(p["path"])
            deleted += 1
            deleted_bytes += p["size"]
            total -= p["size"]
        except Exception as e:
            print(f"  expire delete failed {p['path']}: {e}", flush=True)

    return {"deleted": deleted, "bytes": deleted_bytes, "remaining": total, "files": len(keep)}


def write_cursor(s3: pafs.S3FileSystem, base: str, payload: dict) -> None:
    path = f"{base}/_stream_cursor.json"
    body = json.dumps(payload, indent=2).encode()
    with s3.open_output_stream(path) as out:
        out.write(body)


def run_seed(s3, base: str, rps: int, max_bytes: int, max_hours: float, batch: int) -> int:
    """Write until ~50% of envelope filled or 60s, whichever first."""
    target = max_bytes // 2
    written = 0
    rows = 0
    t0 = time.time()
    print(f"[seed] target ~{target} bytes", flush=True)
    while written < target and (time.time() - t0) < 120:
        now = datetime.now(timezone.utc)
        table = generate_batch(batch, now=now)
        path, size = write_table(s3, base, table, now)
        written += size
        rows += batch
        print(f"  +{batch} rows {path} ({size} B) total_bytes≈{written}", flush=True)
    stats = expire(s3, base, max_bytes, max_hours)
    write_cursor(s3, base, {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "seed",
        "rows_written": rows,
        "bytes_approx": written,
        "last_partition": partition_key(datetime.now(timezone.utc)),
        "catalog_id": f"seed-{int(time.time())}-{rows}",
        "expire": stats,
        "rps": rps,
    })
    print(f"[seed] done rows={rows} bytes≈{written} expire={stats}", flush=True)
    return 0


def run_stream(
    s3,
    base: str,
    rps: int,
    max_bytes: int,
    max_hours: float,
    batch: int,
    expire_every: float,
    files_per_min: int = 6,
) -> int:
    """Stream at ~rps by buffering, then flush files_per_min objects per minute=.

    Default files_per_min=6 → one flush every 10s, ~rps*10 rows per object.
    Avoids the small-file tax of writing once per second.
    """
    files_per_min = max(int(files_per_min), 1)
    flush_interval = 60.0 / files_per_min
    rows_per_flush = max(int(round(rps * flush_interval)), 1)
    # `batch` is ignored for pacing; kept for CLI/seed compatibility.
    print(
        f"[stream] rps={rps} files_per_min={files_per_min} "
        f"flush_every={flush_interval:.1f}s rows_per_flush={rows_per_flush} "
        f"max_bytes={max_bytes} max_hours={max_hours}",
        flush=True,
    )
    rows_total = 0
    bytes_total = 0
    last_expire = 0.0
    last_cursor = 0.0
    last_path = ""
    cursor_every = max(flush_interval, 2.0)
    while True:
        t_loop = time.time()
        now = datetime.now(timezone.utc)
        # Event times spread across this flush window (not a 2s spike).
        table = generate_batch(rows_per_flush, now=now, window_sec=flush_interval)
        path, size = write_table(s3, base, table, now)
        last_path = path
        rows_total += rows_per_flush
        bytes_total += size
        catalog_id = f"{partition_key(now)}:{rows_total}"
        stats: dict = {}
        if t_loop - last_expire >= expire_every:
            stats = expire(s3, base, max_bytes, max_hours)
            last_expire = t_loop
        if t_loop - last_cursor >= cursor_every:
            write_cursor(s3, base, {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "mode": "stream",
                "rows_written": rows_total,
                "bytes_approx": bytes_total,
                "last_partition": partition_key(now),
                "catalog_id": catalog_id,
                "last_path": last_path,
                "expire": stats,
                "rps": rps,
                "files_per_min": files_per_min,
                "rows_per_flush": rows_per_flush,
            })
            last_cursor = t_loop
        print(
            f"[stream] rows={rows_total:,} last={path} size={size} "
            f"part={partition_key(now)} expire={stats or 'skip'}",
            flush=True,
        )
        elapsed = time.time() - t_loop
        sleep_for = flush_interval - elapsed
        if sleep_for > 0:
            time.sleep(sleep_for)


def main() -> int:
    p = argparse.ArgumentParser(description="VPC flow generator with disk envelope")
    p.add_argument("--mode", choices=["seed", "stream"], default=os.environ.get("FLOW_MODE", "stream"))
    p.add_argument("--bucket", default=os.environ.get("S3_BUCKET", "cybersec-dask-data"))
    p.add_argument("--prefix", default=os.environ.get("FLOW_PREFIX", "vpc-flow"))
    p.add_argument("--endpoint", default=os.environ.get("S3_ENDPOINT"))
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    p.add_argument("--rps", type=int, default=int(os.environ.get("FLOW_RPS", "200")))
    p.add_argument("--max-bytes", type=int, default=int(os.environ.get("FLOW_MAX_BYTES", str(8 * 1024**3))))
    p.add_argument("--max-hours", type=float, default=float(os.environ.get("FLOW_MAX_HOURS", "3")))
    p.add_argument("--batch", type=int, default=int(os.environ.get("FLOW_BATCH", "200")))
    p.add_argument(
        "--files-per-min",
        type=int,
        default=int(os.environ.get("FLOW_FILES_PER_MIN", "6")),
        help="Objects flushed per minute= partition (default: 6 → every 10s)",
    )
    p.add_argument("--expire-every", type=float, default=float(os.environ.get("FLOW_EXPIRE_EVERY", "60")))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    print("=" * 60)
    print("VPC Flow Generator")
    print(f"  mode={args.mode} bucket={args.bucket} prefix={args.prefix}")
    print(f"  rps={args.rps} files_per_min={args.files_per_min}")
    print(f"  max_bytes={args.max_bytes} max_hours={args.max_hours}")
    print(f"  endpoint={args.endpoint or '(AWS)'}")
    print("=" * 60)
    if args.dry_run:
        return 0

    if not os.environ.get("AWS_ACCESS_KEY_ID"):
        print("ERROR: AWS_ACCESS_KEY_ID required", file=sys.stderr)
        return 1

    s3 = _s3_fs(args.endpoint, args.region)
    base = f"{args.bucket}/{args.prefix.strip('/')}"
    # ensure prefix exists by writing nothing — first write creates it
    if args.mode == "seed":
        return run_seed(s3, base, args.rps, args.max_bytes, args.max_hours, args.batch)
    return run_stream(
        s3, base, args.rps, args.max_bytes, args.max_hours, args.batch,
        args.expire_every, files_per_min=args.files_per_min,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[stream] stopped", flush=True)
        raise SystemExit(0)
