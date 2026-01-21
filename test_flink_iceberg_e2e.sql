-- E2E Test: Flink DataGen -> Iceberg -> MinIO
-- This script tests the complete pipeline from data generation to storage

-- Step 1: Create a simple DataGen source
CREATE TEMPORARY TABLE test_datagen (
  id STRING,
  name STRING,
  amount BIGINT,
  region STRING,
  created_at BIGINT  -- Use BIGINT instead of TIMESTAMP for Iceberg compatibility
) WITH (
  'connector' = 'datagen',
  'rows-per-second' = '5',
  'fields.id.kind' = 'random',
  'fields.id.length' = '10',
  'fields.name.kind' = 'random',
  'fields.name.length' = '20',
  'fields.amount.min' = '1',
  'fields.amount.max' = '1000',
  'fields.region.kind' = 'random',
  'fields.region.length' = '10',
  'fields.created_at.min' = '1704067200000',  -- 2024-01-01 00:00:00
  'fields.created_at.max' = '1735689599000'   -- 2024-12-31 23:59:59
);

-- Step 2: Create Iceberg catalog with Polaris REST
CREATE CATALOG iceberg_catalog WITH (
  'type' = 'iceberg',
  'catalog-type' = 'rest',
  'uri' = 'http://localhost:8181/api/catalog',
  'warehouse' = 'cybersec',
  'credential' = 'admin:admin',
  'oauth2-server-uri' = 'http://localhost:8181/api/catalog/v1/oauth/tokens',
  'scope' = 'PRINCIPAL_ROLE:ALL',
  'io-impl' = 'org.apache.iceberg.hadoop.HadoopFileIO',
  'fs.s3a.endpoint' = 'http://localhost:9010',
  'fs.s3a.access.key' = 'minioadmin',
  'fs.s3a.secret.key' = 'minioadmin',
  'fs.s3a.path.style.access' = 'true',
  'fs.s3a.connection.ssl.enabled' = 'false',
  'fs.s3a.impl' = 'org.apache.hadoop.fs.s3a.S3AFileSystem'
);

-- Step 3: Switch to Iceberg catalog and create database
USE CATALOG iceberg_catalog;
CREATE DATABASE IF NOT EXISTS test_db;
USE test_db;

-- Step 4: Create Iceberg table
CREATE TABLE IF NOT EXISTS test_events (
  id STRING,
  name STRING,
  amount BIGINT,
  region STRING,
  created_at BIGINT,
  PRIMARY KEY (id) NOT ENFORCED
) PARTITIONED BY (region);

-- Step 5: Insert test data (limited run for testing)
INSERT INTO test_events
SELECT * FROM default_catalog.default_database.test_datagen
LIMIT 100;
