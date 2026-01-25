# Services Environment Policy
#
# Validates that required services are available.
# Run with: conftest test build/environment.json --policy policy/environment/

package environment.services

import rego.v1

# Deny if PostgreSQL is not reachable
deny contains msg if {
    not input.services.postgres.healthy
    msg := sprintf("PostgreSQL not healthy on port %d. Run: devenv up", [input.services.postgres.port])
}

# Deny if MinIO is not reachable
deny contains msg if {
    not input.services.minio.healthy
    msg := "MinIO not healthy. Run: devenv up"
}

# Deny if Polaris is not reachable
deny contains msg if {
    not input.services.polaris.healthy
    msg := "Polaris catalog not healthy. Run: devenv up"
}

# Deny if Flink JobManager is not reachable
deny contains msg if {
    input.flink.home_exists
    not input.services.flink.jobmanager_healthy
    msg := "Flink JobManager not reachable. Run: devenv tasks run restart:clean"
}

# Deny if Flink has no TaskManagers
deny contains msg if {
    input.services.flink.jobmanager_healthy
    input.services.flink.taskmanager_count == 0
    msg := "No Flink TaskManagers registered. Check TaskManager logs."
}

# Warn if Iceberg Browser is not running
warn contains msg if {
    not input.services.iceberg_browser.healthy
    msg := "Iceberg Browser not running on port 5050"
}
