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

import org.apache.flink.api.java.utils.ParameterTool;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.table.api.EnvironmentSettings;
import org.apache.flink.table.api.bridge.java.StreamTableEnvironment;

/**
 * Flink job that generates synthetic AWS CloudTrail events and writes them directly to Iceberg.
 *
 * Uses the industry-standard Iceberg REST Catalog API for catalog operations.
 * Works with any REST Catalog implementation (Apache Polaris, Tabular, Nessie, etc.)
 *
 * Usage:
 *   flink run -c com.cloudera.cyber.flink.iceberg.CloudTrailDataGenIcebergJob \
 *     flink-common-2.4.0-iceberg.jar \
 *     --catalog.uri http://localhost:8181 \
 *     --warehouse.name cybersec \
 *     --s3.endpoint http://localhost:9010 \
 *     --s3.access-key minioadmin \
 *     --s3.secret-key minioadmin \
 *     --rows-per-second 10
 */
public class CloudTrailDataGenIcebergJob {

    public static void main(String[] args) throws Exception {
        // Parse parameters
        ParameterTool params = ParameterTool.fromArgs(args);

        // REST Catalog configuration
        String catalogUri = params.get("catalog.uri", "http://localhost:8181");
        String warehouseName = params.get("warehouse.name", "cybersec");

        // S3/MinIO configuration
        String s3Endpoint = params.get("s3.endpoint", "http://localhost:9010");
        String s3AccessKey = params.get("s3.access-key", "minioadmin");
        String s3SecretKey = params.get("s3.secret-key", "minioadmin");

        int rowsPerSecond = params.getInt("rows-per-second", 10);

        // Create streaming environment
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        EnvironmentSettings settings = EnvironmentSettings
                .newInstance()
                .inStreamingMode()
                .build();
        StreamTableEnvironment tableEnv = StreamTableEnvironment.create(env, settings);

        // Setup Iceberg catalog with REST catalog
        CloudTrailIcebergWriter.createIcebergCatalog(
            tableEnv,
            catalogUri,
            warehouseName,
            s3Endpoint,
            s3AccessKey,
            s3SecretKey
        );

        // Create database if not exists (use backticks for reserved words like 'default')
        tableEnv.executeSql("CREATE DATABASE IF NOT EXISTS iceberg_catalog.`cybersec`");

        CloudTrailIcebergWriter.createCloudTrailTable(tableEnv);

        // Create DataGen source for CloudTrail events
        String createSourceDdl = String.format(
            "CREATE TEMPORARY TABLE cloudtrail_source (" +
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
            "  response_elements STRING" +
            ") WITH (" +
            "  'connector' = 'datagen'," +
            "  'rows-per-second' = '%d'," +
            "  'number-of-rows' = '10000000'," +
            "  'fields.event_version.kind' = 'sequence'," +
            "  'fields.event_version.start' = '1'," +
            "  'fields.event_version.end' = '1'," +
            "  'fields.event_id.length' = '36'," +
            "  'fields.event_name.length' = '20'," +
            "  'fields.aws_region.length' = '15'," +
            "  'fields.source_ip_address.length' = '15'," +
            "  'fields.user_agent.length' = '30'," +
            "  'fields.event_source.length' = '25'," +
            "  'fields.user_identity_type.length' = '15'," +
            "  'fields.user_identity_arn.length' = '50'," +
            "  'fields.user_identity_account_id.length' = '12'," +
            "  'fields.request_parameters.length' = '100'," +
            "  'fields.response_elements.length' = '100'" +
            ")",
            rowsPerSecond
        );
        
        tableEnv.executeSql(createSourceDdl);
        
        // Stream data from source to Iceberg table
        String insertSql = 
            "INSERT INTO iceberg_catalog.cybersec.cloudtrail_events " +
            "SELECT * FROM cloudtrail_source";
        
        System.out.println("Starting CloudTrail DataGen -> Iceberg job...");
        System.out.println("REST Catalog: " + catalogUri + "/api/catalog");
        System.out.println("Warehouse: " + warehouseName);
        System.out.println("S3 Endpoint: " + s3Endpoint);
        System.out.println("Generating " + rowsPerSecond + " events per second...");
        
        tableEnv.executeSql(insertSql);
        
        // Job will run continuously until cancelled
    }
}
