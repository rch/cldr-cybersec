# Apache Polaris Setup and Permissions

## Overview

Apache Polaris is the REST catalog service that manages Iceberg table metadata and access control. It uses PostgreSQL for persistence, ensuring metadata survives restarts.

## Architecture

```
┌─────────────────┐      ┌──────────────────┐      ┌─────────────┐
│  PyIceberg      │─────▶│  Polaris REST    │─────▶│ PostgreSQL  │
│  (Iceberg       │      │  Catalog         │      │ (Metadata)  │
│   Browser)      │      │  (Port 8181)     │      │ (Port 5438) │
└─────────────────┘      └──────────────────┘      └─────────────┘
                                │
                                │
                                ▼
                         ┌─────────────┐
                         │   MinIO     │
                         │   (Data)    │
                         │ (Port 9010) │
                         └─────────────┘
```

## Persistence

- **PostgreSQL Database**: `iceberg` (port 5438)
- **Schema**: `polaris_schema`
- **Data Directory**: `$DEVENV_STATE/postgres`
- **Key Tables**:
  - `polaris_schema.entities` - Catalog entities (catalogs, namespaces, tables)
  - `polaris_schema.grant_records` - Permission grants
  - `polaris_schema.principal_authentication_data` - Authentication credentials

## RBAC Model

Polaris uses a fine-grained Role-Based Access Control (RBAC) model:

### Principal Roles (Global)
- `service_admin` - Full system administration
- `catalog_admin` - Catalog management across all catalogs

### Catalog Roles (Per-Catalog)
- Assigned within a specific catalog
- Can have privileges like:
  - `CATALOG_MANAGE_CONTENT` - Create/drop tables and namespaces
  - `CATALOG_MANAGE_ACCESS` - Manage permissions
  - `TABLE_READ_DATA` - Read table data
  - `TABLE_WRITE_DATA` - Write table data
  - `TABLE_FULL_METADATA` - Full table metadata access
  - `NAMESPACE_FULL_METADATA` - Full namespace metadata access

### Important Distinction
🔴 **Catalog management privileges ≠ Data access privileges**

The bootstrap `service_admin` role can create catalogs and tables but cannot read/write data without explicit `TABLE_READ_DATA` and `TABLE_WRITE_DATA` grants.

## Initial Setup

### Start the Stack

```bash
devenv up
```

**That's all!** The following happens automatically:

1. **PostgreSQL** starts with persistent storage in `$DEVENV_STATE/postgres`
2. **Polaris** waits for PostgreSQL, then starts
3. **polaris-init** process automatically:
   - Waits for Polaris to be healthy
   - Checks if catalog already exists (idempotent)
   - Creates the `cybersec` catalog if needed
   - Creates a `data_access` catalog role with full permissions
   - Grants all necessary privileges (CATALOG_MANAGE_CONTENT, CATALOG_MANAGE_ACCESS, TABLE_READ_DATA, TABLE_WRITE_DATA, etc.)
   - Exits after successful initialization
4. **MinIO, Flink, and Iceberg Browser** start

### Verify Setup

```bash
devenv tasks run polaris:check
```

Expected output:
```
✅ Catalog 'cybersec' exists
✅ Polaris is properly configured
```

## After Restarts

### Normal Restart (Data Persists)

```bash
devenv up
```

PostgreSQL data persists in `$DEVENV_STATE/postgres`, so Polaris metadata survives normal restarts. The `polaris-init` process checks if the catalog exists and **skips initialization if already configured**.

### Clean Restart (Data Cleared)

```bash
devenv tasks run restart:clean
```

This kills all processes but **preserves PostgreSQL data**. After restart:

```bash
devenv up  # Automatic initialization checks and skips if catalog exists
```

The `polaris-init` process automatically detects existing configuration and only initializes if needed.

### Full Database Reset

If you need to completely reset Polaris metadata:

```bash
# Stop everything
devenv tasks run restart:clean

# Delete PostgreSQL data
rm -rf $DEVENV_STATE/postgres

# Start fresh
devenv up

# Initialize Polaris
devenv tasks run polaris:init
```

## Troubleshooting

### Error: "relation polaris_schema.entities does not exist"

**Cause**: Polaris database schema not initialized

**Solution**:
```bash
devenv tasks run polaris:init
```

### Error: "Principal 'root' is not authorized for op LOAD_TABLE_WITH_READ_DELEGATION"

**Cause**: Missing TABLE_READ_DATA privilege

**Solution**:
```bash
# Re-run initialization to grant permissions
devenv tasks run polaris:init
```

### Error: "Catalog 'cybersec' not found"

**Cause**: Catalog not created or PostgreSQL data was wiped

**Solution**:
```bash
devenv tasks run polaris:init
```

### Check Polaris Health

```bash
curl http://localhost:8182/q/health/ready
```

Expected output:
```json
{
  "status": "UP",
  "checks": [
    {
      "name": "Database connections health check",
      "status": "UP"
    }
  ]
}
```

### Check Catalog via API

```bash
curl -u "admin:admin" http://localhost:8181/api/management/v1/catalogs | jq
```

## Configuration Files

- **devenv.nix**: Process definitions and dependencies
- **setup_polaris_catalog.sh**: Initialization script
- **thirdparty/polaris/polaris-bin-1.3.0-incubating/conf/application.properties**: Polaris configuration

## Bootstrap Credentials

- **Realm**: `POLARIS`
- **Client ID**: `admin`
- **Client Secret**: `admin`

These are set via `POLARIS_BOOTSTRAP_CREDENTIALS` environment variable and provide initial service_admin access.

## Development Workflow

1. **First Time Setup**:
   ```bash
   devenv up  # Everything configures automatically
   ```

2. **Daily Development**:
   ```bash
   devenv up  # Data persists, initialization skipped
   ```

3. **After Problems**:
   ```bash
   devenv tasks run restart:clean
   devenv up  # Auto-initialization checks and fixes if needed
   ```

4. **Complete Reset**:
   ```bash
   devenv tasks run restart:clean
   rm -rf $DEVENV_STATE/postgres
   devenv up  # Fresh initialization happens automatically
   ```

5. **Manual Re-initialization** (if automatic init failed):
   ```bash
   devenv tasks run polaris:init
   ```

## API Endpoints

- **Catalog API**: http://localhost:8181/api/catalog
- **Management API**: http://localhost:8181/api/management/v1
- **Health/Admin**: http://localhost:8182/q/health
- **OAuth Tokens**: http://localhost:8181/api/catalog/v1/oauth/tokens

## References

- [Apache Polaris Documentation](https://polaris.apache.org/)
- [Iceberg REST Catalog Spec](https://iceberg.apache.org/docs/latest/rest-catalog/)
- [Polaris RBAC Model](https://polaris.apache.org/docs/security/)
