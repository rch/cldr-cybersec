#!/usr/bin/env bash
#
# Submit CloudTrail DataGen to Iceberg streaming job using Iceberg runtime JAR
#

set -e

FLINK_HOME=/nix/store/szp8lcvci3r3ncpr58qnim82zd2h5y2h-flink-2.1.0/opt/flink
JAR_PATH=/Users/ryanhill/local/src/current/cldr-oss/cybersec/flink-cyber/flink-common/target/flink-common-2.4.0-iceberg.jar
ICEBERG_RUNTIME=~/.m2/repository/org/apache/iceberg/iceberg-flink-runtime-1.20/1.7.0/iceberg-flink-runtime-1.20-1.7.0.jar

echo "═══════════════════════════════════════════════════════════════"
echo "  CloudTrail DataGen → Iceberg Streaming Job (with classpath)"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "📦 Application JAR: flink-common-2.4.0-iceberg.jar"
echo "📚 Iceberg Runtime: $ICEBERG_RUNTIME"
echo "🎯 Main Class: com.cloudera.cyber.flink.iceberg.CloudTrailDataGenIcebergJob"
echo "🔧 Configuration:"
echo "   - PostgreSQL: localhost:5438/iceberg"
echo "   - MinIO: http://localhost:9010"
echo "   - Event Rate: 5 events/second"
echo ""

# Submit job
echo "🚀 Submitting job..."
$FLINK_HOME/bin/flink run \
  -C "file://$ICEBERG_RUNTIME" \
  -d \
  "$JAR_PATH" \
  --postgres.host localhost \
  --postgres.port 5438 \
  --postgres.db iceberg \
  --postgres.user $USER \
  --minio.endpoint http://localhost:9010 \
  --minio.access-key minioadmin \
  --minio.secret-key minioadmin \
  --rows-per-second 5

echo ""
echo "✅ Job submitted!"
echo "📊 Monitor at: http://localhost:8081"
echo ""
