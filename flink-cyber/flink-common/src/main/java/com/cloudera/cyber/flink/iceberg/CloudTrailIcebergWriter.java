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

import java.sql.DriverManager;
import java.util.HashMap;
import java.util.Map;

/**
 * Flink job to write AWS CloudTrail events to Apache Iceberg tables using MinIO and PostgreSQL catalog.
 * 
 * This class demonstrates direct Iceberg integration with Flink using the Iceberg connector.
 * It creates an Iceberg catalog backed by PostgreSQL and writes data to MinIO S3-compatible storage.
 */
public class CloudTrailIcebergWriter {
    
    // Force PostgreSQL driver registration for JDBC DriverManager
    static {
        try {
            // Explicitly register the PostgreSQL driver with DriverManager
            // This is needed because the driver is loaded by Flink's user classloader
            // but DriverManager uses the system classloader
            Class.forName("org.postgresql.Driver");
            DriverManager.registerDriver(new org.postgresql.Driver());
            System.out.println("PostgreSQL driver registered successfully");
        } catch (Exception e) {
            throw new RuntimeException("Failed to register PostgreSQL driver", e);
        }
    }
    
    private static final String CATALOG_NAME = "iceberg_catalog";
    private static final String DATABASE_NAME = "cybersec";
    private static final String TABLE_NAME = "cloudtrail_events";
    
    /**
     * Configure and create Iceberg catalog with REST catalog backend (Apache Polaris).
     * Uses REST API for all catalog operations - avoids all classloader conflicts.
     * 
     * @param tableEnv The Flink table environment
     * @param catalogUri REST catalog URI (default: http://localhost:8181)
     * @param warehouse Warehouse location (default: s3://cybersec/iceberg/warehouse)
     */
    public static void createIcebergCatalog(
            TableEnvironment tableEnv,
            String catalogUri,
            String warehouse) {
        
        Map<String, String> catalogProperties = new HashMap<>();
        catalogProperties.put("type", "iceberg");
        catalogProperties.put("catalog-impl", "org.apache.iceberg.rest.RESTCatalog");
        catalogProperties.put("uri", catalogUri);
        catalogProperties.put("warehouse", warehouse);
        
        // Credentials handled by Polaris
        catalogProperties.put("credential", "admin:admin");
        
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
            "  PRIMARY KEY (event_id) NOT ENFORCED" +
            ") WITH (" +
            "  'format-version' = '2'," +
            "  'write.format.default' = 'parquet'," +
            "  'write.parquet.compression-codec' = 'snappy'" +
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
            "INSERT INTO %s.%s.%s SELECT * FROM cloudtrail_input",
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
            String warehouse) {
        
        EnvironmentSettings settings = EnvironmentSettings
                .newInstance()
                .inStreamingMode()
                .build();
        
        StreamTableEnvironment tableEnv = StreamTableEnvironment.create(
                StreamExecutionEnvironment.getExecutionEnvironment(),
                settings);
        
        createIcebergCatalog(tableEnv, catalogUri, warehouse);
        
        // Create database if it doesn't exist
        tableEnv.executeSql(String.format("CREATE DATABASE IF NOT EXISTS %s.%s", 
                                         CATALOG_NAME, DATABASE_NAME));
        
        createCloudTrailTable(tableEnv);
        
        return tableEnv;
    }
}
