# Local Telemetry Collection

Collecting and exporting telemetry from local Flink jobs for remote analysis.

## OpenTelemetry Stack

The devenv environment includes a full OTEL stack:

| Component | Port | Purpose |
|-----------|------|---------|
| OTEL Collector (gRPC) | 4317 | Receive OTLP traces/metrics |
| OTEL Collector (HTTP) | 4318 | Receive OTLP (HTTP) |
| Prometheus Exporter | 8889 | Export metrics for Prometheus |
| NiFi OTLP Receiver | 4319 | Flow visualization |

## Configuring Telemetry Export

### Flink Metrics

```yaml
# flink-conf.yaml
metrics.reporters: otel
metrics.reporter.otel.class: org.apache.flink.metrics.otel.OpenTelemetryMetricReporter
metrics.reporter.otel.endpoint: http://localhost:4317
```

### Python Applications

```python
from opentelemetry import trace, metrics
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# Configure tracing
trace.set_tracer_provider(TracerProvider())
tracer_provider = trace.get_tracer_provider()
tracer_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="localhost:4317"))
)

tracer = trace.get_tracer(__name__)

with tracer.start_as_current_span("process_events"):
    # Your code here
    pass
```

## OTEL Collector Configuration

The collector routes telemetry to multiple backends:

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 1s
    send_batch_size: 1024

exporters:
  prometheus:
    endpoint: 0.0.0.0:8889
  otlp/remote:
    endpoint: rke2-otel.example.com:4317
    tls:
      insecure: false

service:
  pipelines:
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [prometheus, otlp/remote]
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlp/remote]
```

## Verifying Telemetry

```bash
# Check local Prometheus metrics
curl http://localhost:8889/metrics | grep flink

# Query Prometheus
curl 'http://localhost:9090/api/v1/query?query=flink_jobmanager_job_uptime'
```

## Telemetry Pipeline

```d2
direction: right

Flink: "Flink Jobs"
Python: "Python Apps"
Collector: "OTEL Collector"
Local: "Local Prometheus"
Remote: "Remote RKE2"
NiFi: "NiFi Flows"

Flink -> Collector: "OTLP"
Python -> Collector: "OTLP"
Collector -> Local: "Prometheus"
Collector -> Remote: "OTLP"
Collector -> NiFi: "Traces"
```

## Related Scenarios

See [Scenarios](./scenarios.md) for testable workflows.
