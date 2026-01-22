#!/usr/bin/env bash
# Create warehouse using Polaris Management API
set -e

echo "Creating warehouse 'cybersec' in catalog 'cybersec'..."

# Note: Polaris management API uses basic auth with realm credentials
curl -v -X POST "http://localhost:8182/api/management/v1/catalogs/cybersec/warehouses" \
  -u "admin:admin" \
  -H "Content-Type: application/json" \
  -d '{
    "warehouse": {
      "name": "cybersec",
      "type": "INTERNAL",
      "storageType": "S3"
    }
  }'

echo ""
echo "Warehouse creation complete"
