#!/usr/bin/env bash
# Create warehouse in Polaris after bootstrap
set -e

echo "Creating Polaris warehouse 'cybersec'..."

# Wait for Polaris to be ready
until curl -sf http://localhost:8182/q/health/ready > /dev/null 2>&1; do
  echo "Waiting for Polaris..."
  sleep 2
done

echo "Polaris is ready. Creating warehouse..."

# Get OAuth token with CATALOG_MANAGE_CONTENT scope
TOKEN=$(curl -s -X POST http://localhost:8181/api/catalog/v1/oauth/tokens \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=admin&client_secret=admin&scope=PRINCIPAL_ROLE:ALL" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "Got OAuth token"

# Create warehouse using management API
# In Polaris, a warehouse is created via the catalog namespace API
echo "Creating warehouse namespace..."
curl -v -X POST "http://localhost:8181/api/catalog/v1/namespaces" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Iceberg-Warehouse: cybersec" \
  -d '{
    "namespace": ["cybersec"],
    "properties": {
      "location": "s3://cybersec/iceberg/warehouse"
    }
  }'

echo ""
echo "Warehouse created. Verifying..."

# Verify warehouse exists
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8181/api/catalog/v1/config?warehouse=cybersec" \
  | python3 -m json.tool

echo ""
echo "Done"
