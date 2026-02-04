"""Service topology visualization using NetworkX and HoloViews.

This module provides service dependency graph visualizations:
- Service topology from trace data
- Call flow diagrams
- Dependency heat maps

Example:
    from cybersec.observability import OTelDataset
    from cybersec.observability.viz.topology import TopologyVisualizer

    dataset = OTelDataset("s3://cybersec/otel/")
    viz = TopologyVisualizer(dataset)

    # Service dependency graph
    graph = viz.service_graph(
        start_time=datetime.now() - timedelta(hours=1),
        end_time=datetime.now(),
    )
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import holoviews as hv
import networkx as nx
import numpy as np
import pandas as pd
from holoviews import opts

if TYPE_CHECKING:
    import dask.dataframe as dd
    from cybersec.observability.reader import OTelDataset

logger = logging.getLogger(__name__)

# Enable Bokeh extension
hv.extension("bokeh")

# Node colors by service type
SERVICE_TYPE_COLORS = {
    "gateway": "#1f77b4",
    "service": "#2ca02c",
    "database": "#d62728",
    "cache": "#ff7f0e",
    "queue": "#9467bd",
    "external": "#8c564b",
    "default": "#7f7f7f",
}


class TopologyVisualizer:
    """NetworkX and HoloViews-based service topology visualizations.

    Provides visualization methods for service dependencies extracted from
    OpenTelemetry trace data.

    Attributes:
        dataset: OTelDataset instance for loading data
    """

    def __init__(self, dataset: OTelDataset | None = None) -> None:
        """Initialize TopologyVisualizer.

        Args:
            dataset: Optional OTelDataset for loading span data
        """
        self.dataset = dataset

    def _extract_edges(self, df: pd.DataFrame) -> pd.DataFrame:
        """Extract service-to-service edges from spans.

        Args:
            df: DataFrame with spans

        Returns:
            DataFrame with caller_service, callee_service, count, error_count
        """
        # Build span_id to service mapping
        span_service = df.set_index("span_id")["service_name"].to_dict()

        # Find cross-service calls
        edges = []
        for _, row in df.iterrows():
            parent_id = row.get("parent_span_id")
            if pd.notna(parent_id) and parent_id in span_service:
                caller = span_service[parent_id]
                callee = row["service_name"]
                if caller != callee:
                    edges.append({
                        "caller_service": caller,
                        "callee_service": callee,
                        "is_error": row.get("status_code") == "ERROR",
                        "duration_ns": row.get("duration_ns", 0),
                    })

        if not edges:
            return pd.DataFrame(columns=[
                "caller_service", "callee_service", "call_count",
                "error_count", "avg_duration_ms"
            ])

        edges_df = pd.DataFrame(edges)

        # Aggregate
        return edges_df.groupby(["caller_service", "callee_service"]).agg(
            call_count=("caller_service", "count"),
            error_count=("is_error", "sum"),
            avg_duration_ms=("duration_ns", lambda x: x.mean() / 1_000_000),
        ).reset_index()

    def _extract_nodes(self, df: pd.DataFrame, edges_df: pd.DataFrame) -> pd.DataFrame:
        """Extract service node metrics.

        Args:
            df: DataFrame with spans
            edges_df: DataFrame with edges

        Returns:
            DataFrame with service metrics
        """
        # Get unique services
        services = set(df["service_name"].unique())
        if len(edges_df) > 0:
            services.update(edges_df["caller_service"].unique())
            services.update(edges_df["callee_service"].unique())

        # Calculate metrics per service
        nodes = []
        for service in services:
            service_spans = df[df["service_name"] == service]
            nodes.append({
                "service_name": service,
                "span_count": len(service_spans),
                "error_count": (service_spans["status_code"] == "ERROR").sum() if len(service_spans) > 0 else 0,
                "avg_duration_ms": service_spans["duration_ns"].mean() / 1_000_000 if len(service_spans) > 0 else 0,
            })

        nodes_df = pd.DataFrame(nodes)
        nodes_df["error_rate"] = nodes_df["error_count"] / nodes_df["span_count"].replace(0, 1)

        return nodes_df

    def _infer_service_type(self, service_name: str) -> str:
        """Infer service type from name for coloring."""
        name_lower = service_name.lower()
        if "gateway" in name_lower or "api" in name_lower:
            return "gateway"
        if "database" in name_lower or "db" in name_lower or "postgres" in name_lower or "mysql" in name_lower:
            return "database"
        if "cache" in name_lower or "redis" in name_lower:
            return "cache"
        if "queue" in name_lower or "kafka" in name_lower or "rabbit" in name_lower:
            return "queue"
        if "external" in name_lower:
            return "external"
        return "service"

    def service_graph(
        self,
        ddf: dd.DataFrame | pd.DataFrame | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        service_names: list[str] | None = None,
        layout: str = "spring",
        width: int = 800,
        height: int = 600,
        show_metrics: bool = True,
    ) -> hv.Element:
        """Create service dependency graph visualization.

        Args:
            ddf: Pre-loaded DataFrame with spans
            start_time: Start of time range
            end_time: End of time range
            service_names: Optional service filter
            layout: Graph layout algorithm (spring, circular, kamada_kawai)
            width: Chart width
            height: Chart height
            show_metrics: Whether to show metrics on nodes/edges

        Returns:
            HoloViews Graph element
        """
        if ddf is None:
            if self.dataset is None:
                raise ValueError("Must provide either ddf or dataset")
            if start_time is None:
                start_time = datetime.now(timezone.utc) - timedelta(hours=1)
            if end_time is None:
                end_time = datetime.now(timezone.utc)

            ddf = self.dataset.load_spans(
                start_time=start_time,
                end_time=end_time,
                service_names=service_names,
                columns=[
                    "span_id", "parent_span_id", "service_name",
                    "status_code", "duration_ns"
                ],
            )

        if hasattr(ddf, "compute"):
            df = ddf.compute()
        else:
            df = ddf

        if len(df) == 0:
            return hv.Text(0.5, 0.5, "No span data").opts(width=width, height=height)

        # Extract edges and nodes
        edges_df = self._extract_edges(df)
        nodes_df = self._extract_nodes(df, edges_df)

        if len(nodes_df) == 0:
            return hv.Text(0.5, 0.5, "No services found").opts(width=width, height=height)

        # Build NetworkX graph for layout
        G = nx.DiGraph()

        # Add nodes with metrics
        for _, row in nodes_df.iterrows():
            service_type = self._infer_service_type(row["service_name"])
            G.add_node(
                row["service_name"],
                span_count=row["span_count"],
                error_rate=row["error_rate"],
                avg_duration_ms=row["avg_duration_ms"],
                service_type=service_type,
            )

        # Add edges
        for _, row in edges_df.iterrows():
            G.add_edge(
                row["caller_service"],
                row["callee_service"],
                call_count=row["call_count"],
                error_count=row["error_count"],
                avg_duration_ms=row["avg_duration_ms"],
            )

        # Calculate layout
        if layout == "spring":
            pos = nx.spring_layout(G, k=2, iterations=50)
        elif layout == "circular":
            pos = nx.circular_layout(G)
        elif layout == "kamada_kawai":
            pos = nx.kamada_kawai_layout(G)
        else:
            pos = nx.spring_layout(G)

        # Create HoloViews data structures
        node_data = []
        for node, (x, y) in pos.items():
            attrs = G.nodes[node]
            node_data.append({
                "x": x,
                "y": y,
                "name": node,
                "span_count": attrs.get("span_count", 0),
                "error_rate": attrs.get("error_rate", 0),
                "avg_duration_ms": attrs.get("avg_duration_ms", 0),
                "service_type": attrs.get("service_type", "default"),
            })
        nodes_hv_df = pd.DataFrame(node_data)

        edge_data = []
        for source, target, attrs in G.edges(data=True):
            edge_data.append({
                "source": source,
                "target": target,
                "x0": pos[source][0],
                "y0": pos[source][1],
                "x1": pos[target][0],
                "y1": pos[target][1],
                "call_count": attrs.get("call_count", 0),
                "error_count": attrs.get("error_count", 0),
                "avg_duration_ms": attrs.get("avg_duration_ms", 0),
            })
        edges_hv_df = pd.DataFrame(edge_data) if edge_data else pd.DataFrame(
            columns=["source", "target", "x0", "y0", "x1", "y1", "call_count", "error_count", "avg_duration_ms"]
        )

        # Create HoloViews elements
        # Nodes as points
        nodes_hv = hv.Points(
            nodes_hv_df,
            kdims=["x", "y"],
            vdims=["name", "span_count", "error_rate", "avg_duration_ms", "service_type"],
        )

        # Node labels
        labels = hv.Labels(
            nodes_hv_df,
            kdims=["x", "y"],
            vdims=["name"],
        )

        # Edges as segments
        if len(edges_hv_df) > 0:
            edges_hv = hv.Segments(
                edges_hv_df,
                kdims=["x0", "y0", "x1", "y1"],
                vdims=["source", "target", "call_count", "error_count", "avg_duration_ms"],
            )
        else:
            edges_hv = hv.Segments([])

        # Compose and style
        graph = (edges_hv * nodes_hv * labels)

        # Apply styling
        return graph.opts(
            opts.Segments(
                color="#888888",
                line_width=hv.dim("call_count").norm() * 3 + 1 if show_metrics else 2,
                alpha=0.6,
            ),
            opts.Points(
                size=hv.dim("span_count").norm() * 30 + 10 if show_metrics else 15,
                color=hv.dim("service_type").categorize(SERVICE_TYPE_COLORS),
                line_color="black",
                line_width=1,
                tools=["hover"],
            ),
            opts.Labels(
                text_font_size="9pt",
                text_color="black",
                yoffset=0.08,
            ),
            opts.Overlay(
                width=width,
                height=height,
                title="Service Dependency Graph",
                xaxis=None,
                yaxis=None,
            ),
        )

    def call_flow(
        self,
        trace_id: str | None = None,
        df: pd.DataFrame | None = None,
        width: int = 800,
        height: int = 400,
    ) -> hv.Element:
        """Create call flow diagram for a single trace.

        Shows the sequence of service calls in a trace.

        Args:
            trace_id: Trace ID to visualize
            df: Pre-loaded trace spans
            width: Chart width
            height: Chart height

        Returns:
            HoloViews Sankey or similar flow diagram
        """
        if df is None:
            if self.dataset is None or trace_id is None:
                raise ValueError("Must provide either df or trace_id with dataset")
            result = self.dataset.get_trace(trace_id)
            df = result["spans"].compute()

        if len(df) == 0:
            return hv.Text(0.5, 0.5, "No spans found").opts(width=width, height=height)

        # Sort by start time
        df = df.sort_values("start_time_unix_nano")

        # Extract flow
        span_service = df.set_index("span_id")["service_name"].to_dict()

        flows = []
        for _, row in df.iterrows():
            parent_id = row.get("parent_span_id")
            if pd.notna(parent_id) and parent_id in span_service:
                flows.append({
                    "source": span_service[parent_id],
                    "target": row["service_name"],
                    "value": 1,
                })

        if not flows:
            return hv.Text(0.5, 0.5, "No cross-service calls found").opts(
                width=width, height=height
            )

        flows_df = pd.DataFrame(flows)
        flows_agg = flows_df.groupby(["source", "target"]).sum().reset_index()

        # Create Sankey diagram
        sankey = hv.Sankey(flows_agg, kdims=["source", "target"], vdims=["value"])

        return sankey.opts(
            opts.Sankey(
                width=width,
                height=height,
                edge_color="source",
                node_color="index",
                cmap="Category10",
                title=f"Call Flow: {trace_id or 'Trace'}",
            )
        )

    def dependency_matrix(
        self,
        ddf: dd.DataFrame | pd.DataFrame | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        metric: str = "call_count",
        width: int = 600,
        height: int = 600,
    ) -> hv.Element:
        """Create service dependency matrix (heatmap).

        Shows the relationship intensity between services.

        Args:
            ddf: Pre-loaded DataFrame with spans
            start_time: Start of time range
            end_time: End of time range
            metric: Metric to display (call_count, error_count, avg_duration_ms)
            width: Chart width
            height: Chart height

        Returns:
            HoloViews HeatMap
        """
        if ddf is None:
            if self.dataset is None:
                raise ValueError("Must provide either ddf or dataset")
            if start_time is None:
                start_time = datetime.now(timezone.utc) - timedelta(hours=1)
            if end_time is None:
                end_time = datetime.now(timezone.utc)

            ddf = self.dataset.load_spans(
                start_time=start_time,
                end_time=end_time,
                columns=["span_id", "parent_span_id", "service_name", "status_code", "duration_ns"],
            )

        if hasattr(ddf, "compute"):
            df = ddf.compute()
        else:
            df = ddf

        if len(df) == 0:
            return hv.Text(0.5, 0.5, "No data").opts(width=width, height=height)

        # Extract edges
        edges_df = self._extract_edges(df)

        if len(edges_df) == 0:
            return hv.Text(0.5, 0.5, "No cross-service calls found").opts(
                width=width, height=height
            )

        # Create heatmap data
        heatmap_data = []
        for _, row in edges_df.iterrows():
            heatmap_data.append((
                row["caller_service"],
                row["callee_service"],
                row[metric],
            ))

        heatmap = hv.HeatMap(heatmap_data, kdims=["caller_service", "callee_service"], vdims=[metric])

        return heatmap.opts(
            opts.HeatMap(
                width=width,
                height=height,
                colorbar=True,
                cmap="YlOrRd",
                tools=["hover"],
                xlabel="Caller",
                ylabel="Callee",
                title=f"Dependency Matrix: {metric}",
                xrotation=45,
            )
        )

    def service_health_dashboard(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        width: int = 300,
        height: int = 200,
    ) -> hv.Layout:
        """Create dashboard showing health status of all services.

        Args:
            start_time: Start of time range
            end_time: End of time range
            width: Width per panel
            height: Height per panel

        Returns:
            HoloViews Layout with service health panels
        """
        if self.dataset is None:
            raise ValueError("Dataset required for service_health_dashboard")

        if start_time is None:
            start_time = datetime.now(timezone.utc) - timedelta(hours=1)
        if end_time is None:
            end_time = datetime.now(timezone.utc)

        ddf = self.dataset.load_spans(
            start_time=start_time,
            end_time=end_time,
            columns=["service_name", "status_code", "duration_ns"],
        )

        if hasattr(ddf, "compute"):
            df = ddf.compute()
        else:
            df = ddf

        if len(df) == 0:
            return hv.Text(0.5, 0.5, "No data").opts(width=width * 2, height=height * 2)

        # Calculate service metrics
        service_metrics = df.groupby("service_name").agg(
            request_count=("service_name", "count"),
            error_count=("status_code", lambda x: (x == "ERROR").sum()),
            avg_duration_ms=("duration_ns", lambda x: x.mean() / 1_000_000),
            p99_duration_ms=("duration_ns", lambda x: x.quantile(0.99) / 1_000_000),
        ).reset_index()
        service_metrics["error_rate"] = service_metrics["error_count"] / service_metrics["request_count"]

        # Create panels for each service
        panels = []
        for _, row in service_metrics.iterrows():
            service = row["service_name"]

            # Determine health color
            if row["error_rate"] > 0.1:
                color = "#F44336"  # Red
                status = "Critical"
            elif row["error_rate"] > 0.05:
                color = "#FF9800"  # Orange
                status = "Warning"
            else:
                color = "#4CAF50"  # Green
                status = "Healthy"

            # Create info text
            info = (
                f"{service}\n"
                f"Status: {status}\n"
                f"Requests: {int(row['request_count'])}\n"
                f"Error Rate: {row['error_rate']:.1%}\n"
                f"Avg Latency: {row['avg_duration_ms']:.1f}ms"
            )

            text = hv.Text(0.5, 0.5, info).opts(
                text_font_size="10pt",
                text_align="center",
            )

            # Background color
            bg = hv.Rectangles([(0, 0, 1, 1, color)], vdims=["color"]).opts(
                fill_alpha=0.3,
                line_width=2,
                line_color=color,
            )

            panel = (bg * text).opts(
                opts.Overlay(
                    width=width,
                    height=height,
                    xaxis=None,
                    yaxis=None,
                    title=service,
                )
            )
            panels.append(panel)

        cols = min(3, len(panels))
        return hv.Layout(panels).cols(cols)
