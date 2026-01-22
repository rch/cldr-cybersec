#!/usr/bin/env bash
# Initialize Polaris warehouse for the cybersec catalog

set -e

echo "Initializing Polaris warehouse..."

# Wait for Polaris to be ready
until curl -sf http://localhost:8182/q/health/ready > /dev/null 2>&1; do
  echo "Waiting for Polaris..."
  sleep 2
done

echo "Polaris is ready. Creating warehouse..."

# Get admin token
TOKEN=$(curl -s -X POST http://localhost:8181/api/catalog/v1/oauth/tokens \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=admin&client_secret=admin&scope=PRINCIPAL_ROLE:ALL" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "Got OAuth token"

# Create warehouse in the cybersec catalog using Polaris API
# The catalog already exists from bootstrap, we need to create a warehouse within it
echo "Creating warehouse 'cybersec' in Polaris..."

# Try to create warehouse via catalog API
curl -v -X PUT "http://localhost:8181/api/catalog/v1/namespaces/cybersec" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "properties": {
      "location": "s3://cybersec/iceberg/warehouse"
    }
  }'

echo ""
echo "Warehouse creation attempted. Verifying..."

# Verify warehouse exists
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8181/api/catalog/v1/config?warehouse=cybersec" \
  | python3 -m json.tool

echo ""
echo "Done"
