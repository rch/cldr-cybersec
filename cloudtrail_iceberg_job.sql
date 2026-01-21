-- CloudTrail to Iceberg Job using DataGen Source
-- Run with: flink-sql-client.sh -f cloudtrail_iceberg_job.sql

-- Create DataGen source table for CloudTrail events
CREATE TEMPORARY TABLE cloudtrail_datagen (
  event_id STRING,
  event_name STRING,
  event_source STRING,
  aws_region STRING,
  source_ip_address STRING,
  user_agent STRING,
  user_identity_type STRING,
  user_identity_principal_id STRING,
  user_identity_arn STRING,
  request_parameters STRING,
  response_elements STRING,
  event_time TIMESTAMP(3),
  event_type STRING
) WITH (
  'connector' = 'datagen',
  'rows-per-second' = '10',
  'fields.event_id.kind' = 'random',
  'fields.event_id.length' = '36',
  'fields.event_name.kind' = 'random',
  'fields.event_name.length' = '20',
  'fields.event_source.kind' = 'random',
  'fields.event_source.length' = '30',
  'fields.aws_region.kind' = 'random',
  'fields.aws_region.length' = '15',
  'fields.source_ip_address.kind' = 'random',
  'fields.source_ip_address.length' = '15',
  'fields.user_agent.kind' = 'random',
  'fields.user_agent.length' = '50',
  'fields.user_identity_type.kind' = 'random',
  'fields.user_identity_type.length' = '20',
  'fields.user_identity_principal_id.kind' = 'random',
  'fields.user_identity_principal_id.length' = '30',
  'fields.user_identity_arn.kind' = 'random',
  'fields.user_identity_arn.length' = '100',
  'fields.request_parameters.kind' = 'random',
  'fields.request_parameters.length' = '200',
  'fields.response_elements.kind' = 'random',
  'fields.response_elements.length' = '200',
  'fields.event_time.kind' = 'sequence',
  'fields.event_time.start' = '2026-01-01 00:00:00',
  'fields.event_time.end' = '2026-01-20 23:59:59',
  'fields.event_type.kind' = 'random',
  'fields.event_type.length' = '15'
);

-- Create Iceberg catalog using Polaris REST catalog  
-- Using HadoopFileIO with S3A configuration
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

-- Use the Iceberg catalog
USE CATALOG iceberg_catalog;

-- Create database if not exists
CREATE DATABASE IF NOT EXISTS cloudtrail_db;

USE cloudtrail_db;

-- Create Iceberg table for CloudTrail events
CREATE TABLE IF NOT EXISTS cloudtrail_events (
  event_id STRING,
  event_name STRING,
  event_source STRING,
  aws_region STRING,
  source_ip_address STRING,
  user_agent STRING,
  user_identity_type STRING,
  user_identity_principal_id STRING,
  user_identity_arn STRING,
  request_parameters STRING,
  response_elements STRING,
  event_time TIMESTAMP(3),
  event_type STRING,
  PRIMARY KEY (event_id) NOT ENFORCED
) PARTITIONED BY (aws_region);

-- Insert data from DataGen into Iceberg (runs continuously until stopped)
INSERT INTO cloudtrail_events
SELECT * FROM default_catalog.default_database.cloudtrail_datagen;
