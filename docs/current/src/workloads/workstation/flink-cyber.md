# Flink + Cyber Toolkit

Deploying the full Flink streaming stack with Cloudera's Cyber Toolkit enrichments.

## Cyber Toolkit Overview

The `flink-cyber/` directory contains Java-based Flink jobs for security data processing:

| Module | Purpose |
|--------|---------|
| `parser-chains-flink` | Flexible log parsing with chain configuration |
| `flink-enrichment` | CIDR lookup, geocoding, ThreatQ, HBase |
| `flink-profiler-java` | Event profiling and aggregation |
| `flink-alert-scoring` | Alert scoring and prioritization |
| `flink-indexing` | Iceberg/Hive table writing |

## Building the Toolkit

```bash
cd flink-cyber
mvn clean install -DskipTests

# With tests
mvn clean install
```

## Parser Chain Configuration

Parser chains define how logs are parsed and transformed:

```yaml
# config/parser-chain.yaml
chains:
  - name: cloudtrail
    parsers:
      - type: json
        config:
          timestamp_field: eventTime
          timestamp_format: ISO8601
      - type: enrichment
        config:
          enrichments:
            - cidr_lookup
            - geo_ip
```

## Enrichment Pipeline

```d2
direction: right

Raw: "Raw Events"
Parse: "Parser Chain"
Enrich: "Enrichment"
Profile: "Profiler"
Score: "Scorer"
Index: "Iceberg"

Raw -> Parse -> Enrich -> Profile -> Score -> Index
```

### Available Enrichments

| Enrichment | Description |
|------------|-------------|
| `cidr_lookup` | Match IPs against CIDR ranges |
| `geo_ip` | Add geographic data from MaxMind |
| `threatq` | Query ThreatQ for threat intel |
| `hbase_lookup` | Lookup reference data in HBase |
| `stellar` | Custom Stellar expressions |

## Deploying to RKE2

```bash
# Create Flink namespace
kubectl create namespace flink

# Deploy JobManager
kubectl apply -f deploy/flink/jobmanager.yaml

# Deploy TaskManagers
kubectl apply -f deploy/flink/taskmanager.yaml

# Submit job
kubectl exec -it flink-jobmanager-0 -- \
  flink run /opt/flink/jobs/parser-chains-flink.jar \
  --config /etc/flink/parser-chain.yaml
```

## Monitoring

Flink metrics are exported to Prometheus:

```yaml
# flink-conf.yaml
metrics.reporters: prom
metrics.reporter.prom.class: org.apache.flink.metrics.prometheus.PrometheusReporter
metrics.reporter.prom.port: 9249
```

## Related Scenarios

See [Scenarios](./scenarios.md) for testable workflows.
