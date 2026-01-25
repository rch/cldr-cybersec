#!/usr/bin/env bash
# Polaris Bootstrap Helper Functions
# Provides health checks, verification, and retry logic for Polaris initialization

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}ℹ${NC} $*"
}

log_success() {
    echo -e "${GREEN}✓${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}⚠${NC} $*"
}

log_error() {
    echo -e "${RED}✗${NC} $*"
}

# Wait for PostgreSQL to be ready and schema to exist
wait_for_postgres() {
    local max_attempts="${1:-15}"
    local sleep_seconds="${2:-2}"
    local attempt=0
    
    log_info "Waiting for PostgreSQL to be ready..."
    
    while [ $attempt -lt $max_attempts ]; do
        if psql "postgresql://cybersec:cybersec@localhost:5438/iceberg" \
               -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'polaris_schema' AND table_name = 'entities';" \
               2>/dev/null | grep -q "1"; then
            log_success "PostgreSQL is ready with Polaris schema"
            return 0
        fi
        
        attempt=$((attempt + 1))
        if [ $attempt -lt $max_attempts ]; then
            log_info "Attempt $attempt/$max_attempts - PostgreSQL not ready yet, waiting ${sleep_seconds}s..."
            sleep "$sleep_seconds"
        fi
    done
    
    log_error "PostgreSQL did not become ready after $((max_attempts * sleep_seconds)) seconds"
    return 1
}

# Wait for Polaris health endpoint to respond
wait_for_polaris() {
    local max_attempts="${1:-60}"
    local sleep_seconds="${2:-2}"
    local attempt=0
    
    log_info "Waiting for Polaris to be ready..."
    
    while [ $attempt -lt $max_attempts ]; do
        if curl -s -f http://localhost:8182/q/health/ready >/dev/null 2>&1; then
            local status=$(curl -s http://localhost:8182/q/health/ready | python3 -c "import sys,json; print(json.load(sys.stdin).get('status', 'UNKNOWN'))" 2>/dev/null || echo "ERROR")
            
            if [ "$status" = "UP" ]; then
                log_success "Polaris is ready and healthy"
                return 0
            fi
            
            log_warn "Polaris responding but status is: $status"
        fi
        
        attempt=$((attempt + 1))
        if [ $attempt -lt $max_attempts ]; then
            log_info "Attempt $attempt/$max_attempts - Polaris not ready yet, waiting ${sleep_seconds}s..."
            sleep "$sleep_seconds"
        fi
    done
    
    log_error "Polaris did not become ready after $((max_attempts * sleep_seconds)) seconds"
    return 1
}

# Verify bootstrap principal exists in database
verify_bootstrap() {
    log_info "Verifying bootstrap principal in database..."
    
    local count=$(psql "postgresql://cybersec:cybersec@localhost:5438/iceberg" \
                      -t -c "SELECT COUNT(*) FROM polaris_schema.principal_authentication_data WHERE principal_client_id = 'admin' AND realm_id = 'POLARIS';" \
                      2>/dev/null | xargs)
    
    if [ "$count" = "1" ]; then
        log_success "Bootstrap principal 'admin' exists in realm 'POLARIS'"
        return 0
    elif [ "$count" = "0" ]; then
        log_error "Bootstrap principal 'admin' NOT found in database"
        return 1
    else
        log_error "Unexpected count of admin principals: $count"
        return 1
    fi
}

# Verify catalog exists via Management API
verify_catalog() {
    local catalog_name="${1:-cybersec}"
    
    log_info "Verifying catalog '$catalog_name' exists..."
    
    # Get OAuth token
    local token=$(curl -s -X POST http://localhost:8181/api/catalog/v1/oauth/tokens \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "grant_type=client_credentials&client_id=admin&client_secret=admin&scope=PRINCIPAL_ROLE:ALL" \
        2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null)
    
    if [ -z "$token" ]; then
        log_error "Failed to get OAuth token for Management API"
        return 1
    fi
    
    local response=$(curl -s -H "Authorization: Bearer $token" http://localhost:8181/api/management/v1/catalogs 2>/dev/null)
    
    if [ -z "$response" ]; then
        log_error "Cannot reach Polaris Management API"
        return 1
    fi
    
    if echo "$response" | grep -q "\"name\":\"$catalog_name\""; then
        log_success "Catalog '$catalog_name' exists"
        return 0
    else
        log_warn "Catalog '$catalog_name' not found"
        return 1
    fi
}

# Trigger catalog initialization with retry logic
trigger_catalog_init() {
    local max_retries="${1:-3}"
    local script_path="${2:-./setup_polaris_catalog.sh}"
    local retry=0
    
    log_info "Triggering catalog initialization (max $max_retries attempts)..."
    
    while [ $retry -lt $max_retries ]; do
        if [ $retry -gt 0 ]; then
            local backoff=$((5 * (2 ** (retry - 1))))
            log_info "Retry $retry/$max_retries after ${backoff}s backoff..."
            sleep $backoff
        fi
        
        log_info "Running catalog setup script..."
        if bash "$script_path" > /tmp/polaris-catalog-init.log 2>&1; then
            log_success "Catalog initialization completed successfully"
            
            # Verify catalog was actually created
            sleep 2  # Brief pause for API consistency
            if verify_catalog; then
                return 0
            else
                log_warn "Catalog setup script succeeded but catalog verification failed"
            fi
        else
            log_error "Catalog setup script failed (exit code: $?)"
            if [ -f /tmp/polaris-catalog-init.log ]; then
                log_info "Last 10 lines of error log:"
                tail -10 /tmp/polaris-catalog-init.log | sed 's/^/  /'
            fi
        fi
        
        retry=$((retry + 1))
    done
    
    log_error "Catalog initialization failed after $max_retries attempts"
    return 1
}

# Verify all components are ready and bootstrapped
verify_all() {
    local all_ok=true
    
    log_info "=== Complete Bootstrap Verification ==="
    echo
    
    if wait_for_postgres 15 2; then
        echo
    else
        all_ok=false
    fi
    
    if wait_for_polaris 60 2; then
        echo
    else
        all_ok=false
    fi
    
    if verify_bootstrap; then
        echo
    else
        all_ok=false
    fi
    
    if verify_catalog; then
        echo
    else
        log_warn "Catalog verification failed - may need initialization"
        # Don't fail on catalog missing - it might be intentional
    fi
    
    echo
    if [ "$all_ok" = true ]; then
        log_success "=== All bootstrap verifications passed ==="
        return 0
    else
        log_error "=== Some bootstrap verifications failed ==="
        return 1
    fi
}

# Check if port is in use (portable - works on Linux and macOS)
is_port_in_use() {
    local port="$1"
    # Try lsof first (available on most systems including macOS)
    if command -v lsof &> /dev/null; then
        lsof -ti:"$port" >/dev/null 2>&1 && return 0
    fi
    # Fallback: try /dev/tcp (bash built-in, works on Linux)
    if (echo >/dev/tcp/localhost/"$port") 2>/dev/null; then
        return 0
    fi
    # Fallback: try nc/netcat
    if command -v nc &> /dev/null; then
        nc -z localhost "$port" 2>/dev/null && return 0
    fi
    return 1
}

# Wait for port to be released
wait_for_port_release() {
    local port="$1"
    local max_attempts="${2:-5}"
    local sleep_seconds="${3:-2}"
    local attempt=0

    while [ $attempt -lt $max_attempts ]; do
        if ! is_port_in_use "$port"; then
            return 0
        fi

        attempt=$((attempt + 1))
        if [ $attempt -lt $max_attempts ]; then
            sleep "$sleep_seconds"
        fi
    done

    log_warn "Port $port still in use after $((max_attempts * sleep_seconds)) seconds"
    return 1
}

# Wait for Flink cluster to be ready
wait_for_flink() {
    local max_attempts="${1:-30}"
    local sleep_seconds="${2:-2}"
    
    log_info "Waiting for Flink cluster to be ready..."
    
    for ((i=1; i<=max_attempts; i++)); do
        local response=$(curl -s http://localhost:8081/overview 2>/dev/null)
        
        if echo "$response" | grep -q '"taskmanagers"'; then
            log_success "Flink cluster is ready"
            return 0
        fi
        
        if [ $i -lt $max_attempts ]; then
            sleep $sleep_seconds
        fi
    done
    
    log_error "Flink cluster did not become ready after $((max_attempts * sleep_seconds)) seconds"
    return 1
}

# Start CloudTrail DataGen Flink job
start_datagen() {
    log_info "Starting CloudTrail DataGen Flink job..."
    
    # Check if Flink is ready
    if ! curl -s http://localhost:8081/overview >/dev/null 2>&1; then
        log_error "Flink is not running or not ready"
        return 1
    fi
    
    # Check if job is already running
    local running_jobs=$(curl -s http://localhost:8081/jobs/overview 2>/dev/null | grep -c '"state":"RUNNING"' || echo "0")
    if [ "$running_jobs" -gt "0" ]; then
        log_success "CloudTrail DataGen job already running"
        return 0
    fi
    
    # Set up Flink environment
    export FLINK_HOME="$PWD/thirdparty/flink/flink-dist/target/flink-1.20.1-bin/flink-1.20.1"
    
    if [ ! -f "$FLINK_HOME/bin/flink" ]; then
        log_error "Flink binary not found at $FLINK_HOME/bin/flink"
        return 1
    fi
    
    if [ ! -f "flink_jobs/cloudtrail_datagen.py" ]; then
        log_error "CloudTrail DataGen script not found at flink_jobs/cloudtrail_datagen.py"
        return 1
    fi
    
    # Submit the job in background
    log_info "Submitting CloudTrail DataGen job to Flink..."
    "$FLINK_HOME/bin/flink" run -pyclientexec python -py flink_jobs/cloudtrail_datagen.py > /tmp/cloudtrail.log 2>&1 &
    local submit_pid=$!
    echo $submit_pid > /tmp/cloudtrail.pid

    # Wait for job to be submitted and start running (PyFlink takes ~10-15 seconds to initialize)
    local max_attempts=12
    local attempt=1
    while [ $attempt -le $max_attempts ]; do
        sleep 5
        running_jobs=$(curl -s http://localhost:8081/jobs/overview 2>/dev/null | jq '[.jobs[] | select(.state == "RUNNING")] | length' 2>/dev/null || echo "0")
        if [ "$running_jobs" -gt "0" ]; then
            log_success "CloudTrail DataGen job started successfully"
            return 0
        fi
        log_info "Attempt $attempt/$max_attempts - Waiting for job to start..."
        attempt=$((attempt + 1))
    done

    log_error "Failed to start CloudTrail DataGen job after $max_attempts attempts"
    if [ -f /tmp/cloudtrail.log ]; then
        log_info "Last 10 lines of job log:"
        tail -10 /tmp/cloudtrail.log | sed 's/^/  /'
    fi
    return 1
}

# Verify events are being written to Iceberg Browser
verify_events() {
    local min_events="${1:-1}"
    local max_wait="${2:-60}"
    
    log_info "Verifying events in Iceberg Browser (minimum: $min_events, timeout: ${max_wait}s)..."
    
    local elapsed=0
    while [ $elapsed -lt $max_wait ]; do
        local response=$(curl -s "http://localhost:5050/api/events?limit=1" 2>/dev/null)
        
        if [ -z "$response" ]; then
            if [ $elapsed -eq 0 ]; then
                log_info "Waiting for Iceberg Browser to be ready..."
            fi
            sleep 5
            elapsed=$((elapsed + 5))
            continue
        fi
        
        # Check if we have a valid response with events
        if echo "$response" | grep -q '"events":\s*\['; then
            # Try to extract total count
            local total=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total', 0))" 2>/dev/null || echo "0")
            
            if [ "$total" -ge "$min_events" ]; then
                log_success "Events verified: $total events in Iceberg Browser"
                return 0
            elif [ $elapsed -eq 0 ]; then
                log_info "Waiting for events to be written (current: $total, target: $min_events)..."
            fi
        fi
        
        sleep 5
        elapsed=$((elapsed + 5))
    done
    
    log_error "Event verification failed: did not reach $min_events events within ${max_wait}s"
    return 1
}

# Complete end-to-end verification including datagen and events
verify_e2e() {
    local all_ok=true
    
    log_info "=== Complete E2E Verification ==="
    echo
    
    # Basic infrastructure checks
    if wait_for_postgres 15 2; then
        echo
    else
        all_ok=false
    fi
    
    if wait_for_polaris 60 2; then
        echo
    else
        all_ok=false
    fi
    
    if verify_bootstrap; then
        echo
    else
        all_ok=false
    fi
    
    if verify_catalog; then
        echo
    else
        log_warn "Catalog not found - triggering initialization..."
        if trigger_catalog_init 3 "./setup_polaris_catalog.sh"; then
            echo
        else
            log_error "Catalog initialization failed"
            all_ok=false
        fi
    fi
    
    # Flink and DataGen checks
    if wait_for_flink 30 2; then
        echo
    else
        log_warn "Flink not ready - skipping datagen checks"
        all_ok=false
    fi
    
    if $all_ok; then
        if start_datagen; then
            echo
            if verify_events 1 60; then
                echo
            else
                log_warn "Event verification failed - data may not be flowing yet"
                all_ok=false
            fi
        else
            log_warn "DataGen failed to start - manual start may be required"
            all_ok=false
        fi
    fi
    
    echo
    if $all_ok; then
        log_success "=== E2E verification completed successfully ==="
        return 0
    else
        log_error "=== Some E2E verifications failed ==="
        return 1
    fi
}

# Main execution when script is called directly
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    case "${1:-}" in
        wait-postgres)
            wait_for_postgres "${2:-15}" "${3:-2}"
            ;;
        wait-polaris)
            wait_for_polaris "${2:-60}" "${3:-2}"
            ;;
        wait-flink)
            wait_for_flink "${2:-30}" "${3:-2}"
            ;;
        verify-bootstrap)
            verify_bootstrap
            ;;
        verify-catalog)
            verify_catalog "${2:-cybersec}"
            ;;
        verify-events)
            verify_events "${2:-1}" "${3:-60}"
            ;;
        verify-all)
            verify_all
            ;;
        verify-e2e)
            verify_e2e
            ;;
        start-datagen)
            start_datagen
            ;;
        trigger-init)
            trigger_catalog_init "${2:-3}" "${3:-./setup_polaris_catalog.sh}"
            ;;
        *)
            echo "Usage: $0 {wait-postgres|wait-polaris|wait-flink|verify-bootstrap|verify-catalog|verify-events|verify-all|verify-e2e|start-datagen|trigger-init} [args...]"
            echo
            echo "Commands:"
            echo "  wait-postgres [max_attempts] [sleep_seconds]   - Wait for PostgreSQL with schema"
            echo "  wait-polaris [max_attempts] [sleep_seconds]    - Wait for Polaris health endpoint"
            echo "  verify-bootstrap                                 - Verify admin principal exists"
            echo "  verify-catalog [catalog_name]                   - Verify catalog exists via API"
            echo "  verify-all                                       - Run all verifications"
            echo "  trigger-init [max_retries] [script_path]       - Run catalog init with retry"
            exit 1
            ;;
    esac
fi
