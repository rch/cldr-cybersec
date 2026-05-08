/*
 * Copyright 2020 - 2022 Cloudera. All Rights Reserved.
 *
 * This file is licensed under the Apache License Version 2.0 (the "License"). You may not use this file
 * except in compliance with the License. You may obtain a copy of the License at
 * http://www.apache.org/licenses/LICENSE-2.0.
 *
 * This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
 * either express or implied. Refer to the License for the specific permissions and
 * limitations governing your use of the file.
 */

package com.cloudera.cyber.flink.iceberg;

import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.table.api.EnvironmentSettings;
import org.apache.flink.table.api.Table;
import org.apache.flink.table.api.TableEnvironment;
import org.apache.flink.table.api.bridge.java.StreamTableEnvironment;
import org.apache.flink.types.Row;

import java.util.HashMap;
import java.util.Map;

/**
 * Flink job to write AWS CloudTrail events to Apache Iceberg tables using Iceberg REST Catalog.
 *
 * This class demonstrates direct Iceberg integration with Flink using the industry-standard
 * Iceberg REST Catalog API. It works with any REST Catalog implementation (Apache Polaris,
 * Tabular, Nessie, etc.) and uses S3-compatible storage (MinIO, AWS S3, etc.).
 */
public class CloudTrailIcebergWriter {

    private static final String CATALOG_NAME = "iceberg_catalog";
    private static final String DATABASE_NAME = "cybersec";
    private static final String TABLE_NAME = "cloudtrail_events";
    
    /**
     * Configure and create Iceberg catalog with REST catalog backend.
     * Uses the industry-standard Iceberg REST Catalog API for all catalog operations.
     *
     * @param tableEnv The Flink table environment
     * @param catalogUri REST catalog base URI (e.g., http://localhost:8181)
     * @param warehouseName The catalog/warehouse name in the REST catalog (e.g., "cybersec")
     * @param s3Endpoint S3-compatible endpoint (e.g., http://localhost:9010 for MinIO)
     * @param s3AccessKey S3 access key
     * @param s3SecretKey S3 secret key
     */
    public static void createIcebergCatalog(
            TableEnvironment tableEnv,
            String catalogUri,
            String warehouseName,
            String s3Endpoint,
            String s3AccessKey,
            String s3SecretKey) {

        Map<String, String> catalogProperties = new HashMap<>();
        catalogProperties.put("type", "iceberg");
        catalogProperties.put("catalog-type", "rest");
        catalogProperties.put("uri", catalogUri + "/api/catalog");
        catalogProperties.put("warehouse", warehouseName);

        // OAuth credentials for REST catalog
        catalogProperties.put("credential", "admin:admin");
        catalogProperties.put("scope", "PRINCIPAL_ROLE:ALL");

        // S3/MinIO configuration - use S3FileIO to avoid Hadoop dependencies
        catalogProperties.put("io-impl", "org.apache.iceberg.aws.s3.S3FileIO");
        catalogProperties.put("s3.endpoint", s3Endpoint);
        catalogProperties.put("s3.region", "us-east-1");
        catalogProperties.put("s3.path-style-access", "true");
        catalogProperties.put("s3.access-key-id", s3AccessKey);
        catalogProperties.put("s3.secret-access-key", s3SecretKey);
        catalogProperties.put("client.region", "us-east-1");

        tableEnv.executeSql(String.format(
            "CREATE CATALOG %s WITH (%s)",
            CATALOG_NAME,
            mapToSqlProperties(catalogProperties)
        ));

        tableEnv.useCatalog(CATALOG_NAME);
    }
    
    /**
     * Create the CloudTrail events Iceberg table if it doesn't exist.
     * 
     * @param tableEnv The Flink table environment
     */
    public static void createCloudTrailTable(TableEnvironment tableEnv) {
        String createTableDdl = String.format(
            "CREATE TABLE IF NOT EXISTS %s.%s (" +
            "  event_version STRING," +
            "  event_id STRING," +
            "  event_time TIMESTAMP(3)," +
            "  event_name STRING," +
            "  aws_region STRING," +
            "  source_ip_address STRING," +
            "  user_agent STRING," +
            "  event_source STRING," +
            "  user_identity_type STRING," +
            "  user_identity_arn STRING," +
            "  user_identity_account_id STRING," +
            "  request_parameters STRING," +
            "  response_elements STRING," +
            "  event_hour STRING," +
            "  PRIMARY KEY (event_id) NOT ENFORCED" +
            ") PARTITIONED BY (aws_region, event_hour) WITH (" +
            "  'format-version' = '2'," +
            "  'write.format.default' = 'parquet'," +
            "  'write.parquet.compression-codec' = 'snappy'," +
            "  'write.metadata.delete-after-commit.enabled' = 'true'," +
            "  'write.metadata.previous-versions-max' = '5'," +
            "  'history.expire.max-snapshot-age-ms' = '3600000'" +
            ")",
            DATABASE_NAME,
            TABLE_NAME
        );
        
        tableEnv.executeSql(createTableDdl);
    }
    
    /**
     * Write a DataStream of CloudTrail events to the Iceberg table.
     * 
     * @param dataStream The input stream of Row objects containing CloudTrail events
     * @param tableEnv The stream table environment
     */
    public static void writeToIceberg(DataStream<Row> dataStream, StreamTableEnvironment tableEnv) {
        // Register the DataStream as a temporary view
        Table inputTable = tableEnv.fromDataStream(dataStream);
        tableEnv.createTemporaryView("cloudtrail_input", inputTable);
        
        // Insert data into the Iceberg table
        String insertSql = String.format(
            "INSERT INTO %s.%s.%s SELECT *, DATE_FORMAT(event_time, 'yyyy-MM-dd-HH') FROM cloudtrail_input",
            CATALOG_NAME,
            DATABASE_NAME,
            TABLE_NAME
        );
        
        tableEnv.executeSql(insertSql);
    }
    
    /**
     * Helper method to convert a map to SQL properties format.
     */
    private static String mapToSqlProperties(Map<String, String> properties) {
        StringBuilder sb = new StringBuilder();
        boolean first = true;
        for (Map.Entry<String, String> entry : properties.entrySet()) {
            if (!first) {
                sb.append(", ");
            }
            sb.append("'").append(entry.getKey()).append("' = '").append(entry.getValue()).append("'");
            first = false;
        }
        return sb.toString();
    }
    
    /**
     * Example usage: Create a complete Flink job with Iceberg sink using REST catalog.
     */
    public static StreamTableEnvironment setupIcebergEnvironment(
            String catalogUri,
            String warehouseName,
            String s3Endpoint,
            String s3AccessKey,
            String s3SecretKey) {

        EnvironmentSettings settings = EnvironmentSettings
                .newInstance()
                .inStreamingMode()
                .build();

        StreamTableEnvironment tableEnv = StreamTableEnvironment.create(
                StreamExecutionEnvironment.getExecutionEnvironment(),
                settings);

        createIcebergCatalog(tableEnv, catalogUri, warehouseName, s3Endpoint, s3AccessKey, s3SecretKey);

        // Create database if it doesn't exist (use backticks for reserved words)
        tableEnv.executeSql(String.format("CREATE DATABASE IF NOT EXISTS %s.`%s`",
                                         CATALOG_NAME, DATABASE_NAME));

        createCloudTrailTable(tableEnv);

        return tableEnv;
    }
}
