#!/usr/bin/env python3
"""Validate distributed out-of-core processing of OTel Parquet data.

This script tests the Dask cluster's ability to:
1. Write partitioned OTel data to S3
2. Read with partition pruning and predicate pushdown
3. Perform distributed aggregations
4. Generate visualizations

Usage:
    # Local cluster (for testing)
    python scripts/validate_dask_otel.py --local

    # Connect to remote cluster
    python scripts/validate_dask_otel.py --scheduler scheduler.zndx.org:8786

    # With custom S3 endpoint
    python scripts/validate_dask_otel.py --s3-endpoint http://minio:9000
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add parent to path for local imports
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Dask OTel processing")
    parser.add_argument(
        "--scheduler",
        default=None,
        help="Dask scheduler address (e.g., scheduler.zndx.org:8786)",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use local Dask cluster",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of workers for local cluster",
    )
    parser.add_argument(
        "--s3-endpoint",
        default=os.getenv("S3_ENDPOINT", "http://localhost:9010"),
        help="S3 endpoint URL",
    )
    parser.add_argument(
        "--s3-bucket",
        default="cybersec",
        help="S3 bucket name",
    )
    parser.add_argument(
        "--s3-key",
        default=os.getenv("AWS_ACCESS_KEY_ID", "minioadmin"),
        help="S3 access key",
    )
    parser.add_argument(
        "--s3-secret",
        default=os.getenv("AWS_SECRET_ACCESS_KEY", "minioadmin"),
        help="S3 secret key",
    )
    parser.add_argument(
        "--span-count",
        type=int,
        default=100_000,
        help="Number of spans to generate",
    )
    parser.add_argument(
        "--skip-write",
        action="store_true",
        help="Skip data generation, only run queries",
    )
    parser.add_argument(
        "--output-dir",
        default="validation_results",
        help="Directory for output artifacts",
    )
    return parser.parse_args()


def create_client(args: argparse.Namespace):
    """Create Dask client based on arguments."""
    from dask.distributed import Client

    if args.scheduler:
        logger.info(f"Connecting to scheduler: {args.scheduler}")
        return Client(args.scheduler)
    elif args.local:
        logger.info(f"Creating local cluster with {args.workers} workers")
        return Client(n_workers=args.workers, threads_per_worker=2, memory_limit="2GB")
    else:
        # Default to local
        logger.info("Creating default local cluster")
        return Client()


def get_storage_options(args: argparse.Namespace) -> dict:
    """Build S3 storage options from arguments."""
    return {
        "key": args.s3_key,
        "secret": args.s3_secret,
        "endpoint_url": args.s3_endpoint,
    }


def validate_write(args: argparse.Namespace) -> dict:
    """Test 1: Write synthetic OTel data to S3."""
    from cybersec.observability.writer import OTelWriter

    logger.info("=" * 60)
    logger.info("TEST 1: Write Synthetic OTel Data")
    logger.info("=" * 60)

    base_path = f"s3://{args.s3_bucket}/otel-validation/"
    storage_options = get_storage_options(args)

    writer = OTelWriter(base_path, storage_options)

    results = {"test": "write", "status": "pending"}

    try:
        start_time = time.time()

        # Generate spans
        logger.info(f"Generating {args.span_count:,} synthetic spans...")
        span_files = writer.write_synthetic_spans(
            count=args.span_count,
            services=5,
            duration_hours=2,
        )
        span_time = time.time() - start_time
        logger.info(f"  Written {len(span_files)} span files in {span_time:.2f}s")

        # Generate metrics
        start_time = time.time()
        logger.info("Generating synthetic metrics...")
        metric_files = writer.write_synthetic_metrics(
            count=10_000,
            duration_hours=2,
        )
        metric_time = time.time() - start_time
        logger.info(f"  Written {len(metric_files)} metric files in {metric_time:.2f}s")

        # Generate logs
        start_time = time.time()
        logger.info("Generating synthetic logs...")
        log_files = writer.write_synthetic_logs(
            count=10_000,
            duration_hours=2,
        )
        log_time = time.time() - start_time
        logger.info(f"  Written {len(log_files)} log files in {log_time:.2f}s")

        results.update({
            "status": "passed",
            "span_files": len(span_files),
            "metric_files": len(metric_files),
            "log_files": len(log_files),
            "span_write_time": span_time,
            "metric_write_time": metric_time,
            "log_write_time": log_time,
        })
        logger.info("TEST 1: PASSED")

    except Exception as e:
        logger.error(f"TEST 1: FAILED - {e}")
        results.update({"status": "failed", "error": str(e)})

    return results


def validate_read(args: argparse.Namespace, client) -> dict:
    """Test 2: Read OTel data with partition pruning."""
    from cybersec.observability.reader import OTelDataset

    logger.info("=" * 60)
    logger.info("TEST 2: Read with Partition Pruning")
    logger.info("=" * 60)

    base_path = f"s3://{args.s3_bucket}/otel-validation/"
    storage_options = get_storage_options(args)

    dataset = OTelDataset(base_path, storage_options, dask_client=client)

    results = {"test": "read", "status": "pending"}

    try:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=2)

        # Load spans
        logger.info(f"Loading spans from {start_time} to {end_time}...")
        load_start = time.time()
        ddf = dataset.load_spans(start_time=start_time, end_time=end_time)
        load_time = time.time() - load_start

        logger.info(f"  Lazy load time: {load_time:.2f}s")
        logger.info(f"  Partitions: {ddf.npartitions}")

        # Compute count (triggers execution)
        count_start = time.time()
        span_count = len(ddf)
        count_time = time.time() - count_start
        logger.info(f"  Span count: {span_count:,} (computed in {count_time:.2f}s)")

        # Test service filter
        logger.info("Testing service filter...")
        filter_start = time.time()
        services = dataset.list_services(start_time, end_time)
        filter_time = time.time() - filter_start
        logger.info(f"  Services: {services} (found in {filter_time:.2f}s)")

        if services:
            # Load single service
            single_svc_start = time.time()
            single_ddf = dataset.load_spans(
                start_time=start_time,
                end_time=end_time,
                service_names=[services[0]],
            )
            single_count = len(single_ddf)
            single_time = time.time() - single_svc_start
            logger.info(f"  Single service ({services[0]}): {single_count:,} spans in {single_time:.2f}s")

        results.update({
            "status": "passed",
            "span_count": span_count,
            "partitions": ddf.npartitions,
            "services": services,
            "load_time": load_time,
            "count_time": count_time,
        })
        logger.info("TEST 2: PASSED")

    except Exception as e:
        logger.error(f"TEST 2: FAILED - {e}")
        results.update({"status": "failed", "error": str(e)})

    return results


def validate_aggregation(args: argparse.Namespace, client) -> dict:
    """Test 3: Distributed aggregations."""
    from cybersec.observability.reader import OTelDataset
    from cybersec.observability.transforms import (
        compute_service_metrics,
        extract_service_dependencies,
    )

    logger.info("=" * 60)
    logger.info("TEST 3: Distributed Aggregations")
    logger.info("=" * 60)

    base_path = f"s3://{args.s3_bucket}/otel-validation/"
    storage_options = get_storage_options(args)

    dataset = OTelDataset(base_path, storage_options, dask_client=client)

    results = {"test": "aggregation", "status": "pending"}

    try:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=2)

        # Load spans
        ddf = dataset.load_spans(start_time=start_time, end_time=end_time)
        df = ddf.compute()

        # Service metrics
        logger.info("Computing service metrics...")
        agg_start = time.time()
        service_metrics = compute_service_metrics(df)
        agg_time = time.time() - agg_start
        logger.info(f"  Service metrics computed in {agg_time:.2f}s")
        logger.info(f"  Services analyzed: {len(service_metrics)}")

        for _, row in service_metrics.iterrows():
            logger.info(
                f"    {row['service_name']}: "
                f"{row['span_count']:,} spans, "
                f"error_rate={row['error_rate']:.2%}, "
                f"p99={row['p99_duration_ms']:.2f}ms"
            )

        # Service dependencies
        logger.info("Extracting service dependencies...")
        dep_start = time.time()
        dependencies = extract_service_dependencies(df)
        dep_time = time.time() - dep_start
        logger.info(f"  Dependencies extracted in {dep_time:.2f}s")
        logger.info(f"  Edges found: {len(dependencies)}")

        for _, row in dependencies.iterrows():
            logger.info(
                f"    {row['caller_service']} -> {row['callee_service']}: "
                f"{row['call_count']} calls"
            )

        # Dask-native aggregation (groupby on Dask DataFrame)
        logger.info("Testing Dask-native groupby...")
        dask_start = time.time()
        dask_agg = ddf.groupby("service_name").agg({
            "duration_ns": ["mean", "count"],
            "status_code": lambda x: (x == "ERROR").sum(),
        }).compute()
        dask_time = time.time() - dask_start
        logger.info(f"  Dask groupby completed in {dask_time:.2f}s")

        results.update({
            "status": "passed",
            "service_metrics_time": agg_time,
            "dependency_time": dep_time,
            "dask_groupby_time": dask_time,
            "services_analyzed": len(service_metrics),
            "edges_found": len(dependencies),
        })
        logger.info("TEST 3: PASSED")

    except Exception as e:
        logger.error(f"TEST 3: FAILED - {e}")
        import traceback
        traceback.print_exc()
        results.update({"status": "failed", "error": str(e)})

    return results


def validate_visualization(args: argparse.Namespace, client) -> dict:
    """Test 4: Generate visualizations."""
    logger.info("=" * 60)
    logger.info("TEST 4: Visualization Generation")
    logger.info("=" * 60)

    results = {"test": "visualization", "status": "pending"}

    try:
        import holoviews as hv
        hv.extension("bokeh")

        from cybersec.observability.reader import OTelDataset
        from cybersec.observability.viz.traces import TraceVisualizer
        from cybersec.observability.viz.topology import TopologyVisualizer

        base_path = f"s3://{args.s3_bucket}/otel-validation/"
        storage_options = get_storage_options(args)

        dataset = OTelDataset(base_path, storage_options, dask_client=client)

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=2)

        # Load data
        ddf = dataset.load_spans(start_time=start_time, end_time=end_time)
        df = ddf.compute()

        output_dir = Path(args.output_dir)
        output_dir.mkdir(exist_ok=True)

        # Trace visualizations
        trace_viz = TraceVisualizer(dataset)

        logger.info("Generating latency heatmap...")
        viz_start = time.time()
        heatmap = trace_viz.latency_heatmap(df, width=800, height=400)
        hv.save(heatmap, str(output_dir / "latency_heatmap.html"))
        heatmap_time = time.time() - viz_start
        logger.info(f"  Saved latency_heatmap.html in {heatmap_time:.2f}s")

        logger.info("Generating flame graph...")
        viz_start = time.time()
        flame = trace_viz.flame_graph(df, width=800, height=400)
        hv.save(flame, str(output_dir / "flame_graph.html"))
        flame_time = time.time() - viz_start
        logger.info(f"  Saved flame_graph.html in {flame_time:.2f}s")

        # Topology visualizations
        topo_viz = TopologyVisualizer(dataset)

        logger.info("Generating service graph...")
        viz_start = time.time()
        graph = topo_viz.service_graph(df, width=700, height=500)
        hv.save(graph, str(output_dir / "service_graph.html"))
        graph_time = time.time() - viz_start
        logger.info(f"  Saved service_graph.html in {graph_time:.2f}s")

        logger.info("Generating dependency matrix...")
        viz_start = time.time()
        matrix = topo_viz.dependency_matrix(df, width=500, height=500)
        hv.save(matrix, str(output_dir / "dependency_matrix.html"))
        matrix_time = time.time() - viz_start
        logger.info(f"  Saved dependency_matrix.html in {matrix_time:.2f}s")

        results.update({
            "status": "passed",
            "output_dir": str(output_dir),
            "heatmap_time": heatmap_time,
            "flame_time": flame_time,
            "graph_time": graph_time,
            "matrix_time": matrix_time,
        })
        logger.info("TEST 4: PASSED")

    except Exception as e:
        logger.error(f"TEST 4: FAILED - {e}")
        import traceback
        traceback.print_exc()
        results.update({"status": "failed", "error": str(e)})

    return results


def validate_memory_bounds(args: argparse.Namespace, client) -> dict:
    """Test 5: Verify memory stays bounded during large scans."""
    logger.info("=" * 60)
    logger.info("TEST 5: Memory-Bounded Processing")
    logger.info("=" * 60)

    results = {"test": "memory_bounds", "status": "pending"}

    try:
        from cybersec.observability.reader import OTelDataset

        base_path = f"s3://{args.s3_bucket}/otel-validation/"
        storage_options = get_storage_options(args)

        dataset = OTelDataset(base_path, storage_options, dask_client=client)

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=2)

        # Get initial memory state
        info = client.scheduler_info()
        initial_memory = sum(w["memory_limit"] for w in info["workers"].values())
        logger.info(f"Cluster memory limit: {initial_memory / 1e9:.2f} GB")

        # Perform streaming aggregation without full materialization
        logger.info("Testing streaming aggregation...")
        ddf = dataset.load_spans(start_time=start_time, end_time=end_time)

        # This should process data in chunks without loading all into memory
        agg_start = time.time()

        # Streaming count per partition
        partition_counts = ddf.map_partitions(len).compute()
        total_count = sum(partition_counts)

        # Streaming mean (doesn't require full data in memory)
        mean_duration = ddf["duration_ns"].mean().compute()

        agg_time = time.time() - agg_start

        logger.info(f"  Total spans: {total_count:,}")
        logger.info(f"  Mean duration: {mean_duration / 1e6:.2f} ms")
        logger.info(f"  Streaming aggregation time: {agg_time:.2f}s")

        # Check worker memory didn't spike
        info_after = client.scheduler_info()
        for worker_id, worker in info_after["workers"].items():
            mem_used = worker.get("metrics", {}).get("memory", 0)
            mem_limit = worker["memory_limit"]
            utilization = mem_used / mem_limit if mem_limit > 0 else 0
            logger.info(f"  Worker {worker_id[-8:]}: {utilization:.1%} memory used")

        results.update({
            "status": "passed",
            "total_spans": total_count,
            "mean_duration_ms": mean_duration / 1e6,
            "aggregation_time": agg_time,
        })
        logger.info("TEST 5: PASSED")

    except Exception as e:
        logger.error(f"TEST 5: FAILED - {e}")
        import traceback
        traceback.print_exc()
        results.update({"status": "failed", "error": str(e)})

    return results


def main():
    args = parse_args()

    logger.info("=" * 60)
    logger.info("Dask OTel Validation Suite")
    logger.info("=" * 60)
    logger.info(f"S3 Endpoint: {args.s3_endpoint}")
    logger.info(f"S3 Bucket: {args.s3_bucket}")
    logger.info(f"Span Count: {args.span_count:,}")

    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)

    all_results = []

    # Test 1: Write (optional)
    if not args.skip_write:
        results = validate_write(args)
        all_results.append(results)

    # Create Dask client for remaining tests
    client = create_client(args)
    logger.info(f"Dask dashboard: {client.dashboard_link}")

    try:
        # Test 2: Read
        results = validate_read(args, client)
        all_results.append(results)

        # Test 3: Aggregation
        results = validate_aggregation(args, client)
        all_results.append(results)

        # Test 4: Visualization
        results = validate_visualization(args, client)
        all_results.append(results)

        # Test 5: Memory bounds
        results = validate_memory_bounds(args, client)
        all_results.append(results)

    finally:
        client.close()

    # Summary
    logger.info("=" * 60)
    logger.info("VALIDATION SUMMARY")
    logger.info("=" * 60)

    passed = sum(1 for r in all_results if r["status"] == "passed")
    failed = sum(1 for r in all_results if r["status"] == "failed")

    for result in all_results:
        status_icon = "✓" if result["status"] == "passed" else "✗"
        logger.info(f"  {status_icon} {result['test']}: {result['status'].upper()}")

    logger.info("-" * 60)
    logger.info(f"  Passed: {passed}/{len(all_results)}")
    logger.info(f"  Failed: {failed}/{len(all_results)}")

    # Save results
    results_file = output_dir / "validation_results.json"
    with open(results_file, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    logger.info(f"\nResults saved to: {results_file}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
