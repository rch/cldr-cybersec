-- Continuous Flink DataGen -> Iceberg streaming job
-- Generates one message every 3 seconds continuously

-- Create Iceberg catalog with Polaris REST
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

-- Use the e2e_test database
USE e2e_test;

-- Create a DataGen source that generates rows continuously
-- With 1 row per second, we'll see frequent updates
CREATE TEMPORARY TABLE streaming_source (
  id STRING,
  name STRING,
  amount BIGINT,
  region STRING
) WITH (
  'connector' = 'datagen',
  'rows-per-second' = '1',  -- 1 row per second for visible streaming
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

-- Insert continuously into the test_data table
INSERT INTO test_data
SELECT * FROM streaming_source;
