# Services Environment Policy
#
# Validates that required services are available.
# Run with: conftest test build/config.json --policy policy/environment/
#
# Config structure:
#   input.effective.services - Service health status
#   input.effective.flink - Flink configuration
#   input.static.cybersec.services - Static service configuration

package environment.services

import rego.v1

# Use effective config (merged static + runtime)
eff := input.effective
svc := eff.services

# Deny if PostgreSQL is not reachable
deny contains msg if {
    not svc.postgres.healthy
    port := object.get(svc.postgres, "port", 5438)
    msg := sprintf("PostgreSQL not healthy on port %d. Run: devenv up", [port])
}

# Deny if MinIO is not reachable
deny contains msg if {
    not svc.minio.healthy
    msg := "MinIO not healthy. Run: devenv up"
}

# Deny if Polaris is not reachable
deny contains msg if {
    not svc.polaris.healthy
    msg := "Polaris catalog not healthy. Run: devenv up"
}

# Deny if Flink JobManager is not reachable
deny contains msg if {
    eff.flink.home_exists
    not svc.flink.jobmanager_healthy
    msg := "Flink JobManager not reachable. Run: devenv tasks run restart:clean"
}

# Deny if Flink has no TaskManagers
deny contains msg if {
    svc.flink.jobmanager_healthy
    svc.flink.taskmanager_count == 0
    msg := "No Flink TaskManagers registered. Check TaskManager logs."
}

# Warn if Iceberg Browser is not running
warn contains msg if {
    not svc.iceberg_browser.healthy
    msg := "Iceberg Browser not running on port 5050"
}

# Warn if Kafka is not reachable
warn contains msg if {
    not svc.kafka.healthy
    msg := "Kafka not reachable on port 9092"
}

# Info: Show service slot availability
info contains msg if {
    svc.flink.jobmanager_healthy
    svc.flink.slots_available > 0
    msg := sprintf("Flink slots available: %d/%d", [svc.flink.slots_available, svc.flink.slots_total])
}
