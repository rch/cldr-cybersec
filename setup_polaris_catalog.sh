#!/usr/bin/env bash
# Setup Polaris catalog and warehouse using Management API
set -e

echo "=== Setting up Polaris Catalog and Warehouse ==="

# Wait for Polaris to be ready
echo "Waiting for Polaris Management API to be available..."
for i in {1..30}; do
  if curl -s -f "http://localhost:8182/q/health/ready" > /dev/null 2>&1; then
    echo "Polaris is ready!"
    break
  fi
  echo "Waiting... attempt $i/30"
  sleep 2
done

# Get OAuth token for all operations
echo "Getting OAuth token..."
TOKEN=$(curl -s -X POST http://localhost:8181/api/catalog/v1/oauth/tokens \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=admin&client_secret=admin&scope=PRINCIPAL_ROLE:ALL" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null)

if [ -z "$TOKEN" ]; then
  echo "Warning: Could not get OAuth token. Trying with Basic auth..."
  AUTH_HEADER="Authorization: Basic YWRtaW46YWRtaW4="
else
  echo "Got OAuth token"
  AUTH_HEADER="Authorization: Bearer $TOKEN"
fi

echo ""
echo "Creating catalog 'cybersec' with storage configuration..."
curl -v -X POST "http://localhost:8181/api/management/v1/catalogs" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d '{
    "catalog": {
      "name": "cybersec",
      "type": "INTERNAL",
      "storageConfigInfo": {
        "storageType": "S3",
        "endpoint": "http://localhost:9010",
        "pathStyleAccess": true,
        "allowedLocations": [
          "s3://cybersec",
          "s3://cybersec/iceberg/warehouse"
        ]
      },
      "properties": {
        "default-base-location": "s3://cybersec/iceberg/warehouse"
      }
    }
  }'

echo ""
echo ""
echo "=== Granting Permissions to admin principal ==="

# Re-use token from above (or get it if we didn't have it)
if [ -z "$TOKEN" ]; then
  echo "Getting OAuth token..."
  TOKEN=$(curl -s -X POST http://localhost:8181/api/catalog/v1/oauth/tokens \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=client_credentials&client_id=admin&client_secret=admin&scope=PRINCIPAL_ROLE:ALL" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null)
fi

if [ -z "$TOKEN" ]; then
  echo "Warning: Could not get OAuth token. Skipping grant configuration."
  echo "You may need to configure grants manually."
else
  echo "Got OAuth token"

  # Create a simple catalog role with all necessary permissions
  echo "Creating catalog role 'data_access' in cybersec catalog..."
  curl -v -X POST "http://localhost:8181/api/management/v1/catalogs/cybersec/catalog-roles" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
      "catalogRole": {
        "name": "data_access",
        "properties": {}
      }
    }'

  echo ""
  echo "Granting all necessary privileges to data_access catalog role..."
  
  for privilege in "CATALOG_MANAGE_CONTENT" "CATALOG_MANAGE_ACCESS" "TABLE_READ_DATA" "TABLE_WRITE_DATA" "NAMESPACE_FULL_METADATA" "TABLE_FULL_METADATA"; do
    echo "  - Granting $privilege..."
    curl -s -X PUT "http://localhost:8181/api/management/v1/catalogs/cybersec/catalog-roles/data_access/grants" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d "{
        \"grant\": {
          \"type\": \"catalog\",
          \"privilege\": \"$privilege\"
        }
      }" > /dev/null
  done

  echo ""
  echo "Assigning data_access role to service_admin principal role..."
  curl -v -X PUT "http://localhost:8181/api/management/v1/principal-roles/service_admin/catalog-roles/cybersec" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
      "catalogRole": {
        "name": "data_access"
      }
    }'
fi

echo ""
echo ""
echo "=== Setup Complete ==="
echo "Catalog: cybersec"
echo "Base location: s3://cybersec/iceberg/warehouse"
echo "Permissions: admin has CATALOG_MANAGE_CONTENT and CATALOG_MANAGE_ACCESS"
echo ""
echo "Verify catalog:"
echo "  curl -u 'admin:admin' http://localhost:8181/api/management/v1/catalogs"
echo ""
echo "Verify grants:"
echo "  curl -u 'admin:admin' http://localhost:8181/api/management/v1/catalogs/cybersec/catalog-roles/admin/grants"

