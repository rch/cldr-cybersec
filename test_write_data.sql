-- Simple E2E Test: Write data from DataGen to Iceberg
-- This version uses a Flink job submission to actually write data

-- Create DataGen source
CREATE TEMPORARY TABLE test_source (
  id STRING,
  name STRING,
  amount BIGINT,
  region STRING
) WITH (
  'connector' = 'datagen',
  'rows-per-second' = '10',
  'number-of-rows' = '100',
  'fields.id.kind' = 'random',
  'fields.id.length' = '10',
  'fields.name.kind' = 'random',
  'fields.name.length' = '20',
  'fields.amount.min' = '1',
  'fields.amount.max' = '1000',
  'fields.region.kind' = 'sequence',
  'fields.region.start' = '0',
  'fields.region.end' = '4'
);

-- Create Iceberg catalog
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

USE CATALOG iceberg_catalog;
CREATE DATABASE IF NOT EXISTS e2e_test;
USE e2e_test;

-- Create Iceberg sink table
CREATE TABLE IF NOT EXISTS test_data (
  id STRING,
  name STRING,
  amount BIGINT,
  region STRING,
  PRIMARY KEY (id) NOT ENFORCED
) PARTITIONED BY (region);

-- Submit insert job (will complete after inserting 100 rows)
INSERT INTO test_data SELECT * FROM default_catalog.default_database.test_source;
