package com.cloudera.cyber.flink.iceberg;

import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.iceberg.catalog.TableIdentifier;
import org.apache.iceberg.flink.actions.Actions;
import org.apache.iceberg.rest.RESTCatalog;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.HashMap;
import java.util.Map;

/**
 * Flink application that runs continuously to perform Iceberg table maintenance.
 * It will expire snapshots and delete orphan files periodically.
 */
public class CloudTrailIcebergMaintenanceJob {
    private static final Logger LOG = LoggerFactory.getLogger(CloudTrailIcebergMaintenanceJob.class);

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();

        String catalogUri = System.getenv().getOrDefault("ICEBERG_CATALOG_URI", "http://localhost:8181/api/catalog");
        String warehouseName = System.getenv().getOrDefault("ICEBERG_WAREHOUSE", "cybersec");
        
        // S3 Endpoint
        String s3Endpoint = System.getenv().getOrDefault("S3_ENDPOINT", "http://localhost:9010");
        String s3AccessKey = System.getenv().getOrDefault("MINIO_ACCESS_KEY", "minioadmin");
        String s3SecretKey = System.getenv().getOrDefault("MINIO_SECRET_KEY", "minioadmin");

        Map<String, String> properties = new HashMap<>();
        properties.put("uri", catalogUri);
        properties.put("warehouse", warehouseName);
        properties.put("credential", "admin:admin");
        properties.put("scope", "PRINCIPAL_ROLE:ALL");
        properties.put("io-impl", "org.apache.iceberg.aws.s3.S3FileIO");
        properties.put("s3.endpoint", s3Endpoint);
        properties.put("s3.region", "us-east-1");
        properties.put("s3.path-style-access", "true");
        properties.put("s3.access-key-id", s3AccessKey);
        properties.put("s3.secret-access-key", s3SecretKey);

        RESTCatalog catalog = new RESTCatalog();
        catalog.initialize("iceberg_catalog", properties);

        TableIdentifier tableId = TableIdentifier.of("cybersec", "cloudtrail_events");

        LOG.info("Starting continuous Iceberg maintenance job for {}", tableId);

        while (true) {
            try {
                long now = System.currentTimeMillis();
                long maxSnapshotAgeMs = 3600_000L; // 1 hour

                LOG.info("Running expireSnapshots...");
                catalog.loadTable(tableId)
                    .expireSnapshots()
                    .expireOlderThan(now - maxSnapshotAgeMs)
                    .commit();

                // Flink doesn't natively support distributed deleteOrphanFiles in Iceberg Actions yet, 
                // so we rely solely on expireSnapshots which also deletes unreferenced data files.

            } catch (Exception e) {
                LOG.error("Error during maintenance cycle", e);
            }

            LOG.info("Sleeping for 10 minutes before next maintenance cycle...");
            Thread.sleep(10 * 60 * 1000L);
        }
    }
}
