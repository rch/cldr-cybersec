# PyFlink Environment Policy
#
# Validates that the PyFlink environment is correctly configured.
# Run with: conftest test build/config.json --policy policy/environment/
#
# Config structure:
#   input.effective.flink - Flink configuration with runtime state
#   input.effective.python - Python environment
#   input.effective.platform - Platform detection
#   input.static.cybersec - Static HOCON configuration

package environment.pyflink

import rego.v1

# Use input directly (environment.json places data at root level)
flink := input.flink
platform := input.platform
python := input.python

# Deny if FLINK_HOME is not set or doesn't exist
deny contains msg if {
    not flink.home_exists
    msg := "FLINK_HOME directory does not exist. Run: devenv tasks run restart:clean"
}

# Deny if Flink binary is missing
deny contains msg if {
    flink.home_exists
    not flink.binary_exists
    msg := "Flink binary not found. Flink may not be built. Run: devenv tasks run restart:clean"
}

# Deny if PyFlink is not installed
deny contains msg if {
    not python.pyflink_installed
    msg := "PyFlink not installed. Run: uv pip install apache-flink"
}

# Deny if Python path mismatch on macOS without config
deny contains msg if {
    platform.is_macos
    flink.home_exists
    not flink.python_configured
    msg := "macOS detected but Python not configured in flink-conf.yaml. Run: cybersec --cmd '/health fix pyflink'"
}

# Deny if configured Python path doesn't exist
deny contains msg if {
    flink.configured_python_path != ""
    not flink.configured_python_exists
    msg := sprintf("Configured Python path does not exist: %s", [flink.configured_python_path])
}

# Deny if Flink cluster is stale (config newer than process)
deny contains msg if {
    flink.process_stale
    msg := "Flink cluster running with stale config. Restart cluster: devenv tasks run restart:clean"
}

# Warn if FLINK_HOME env var not exported
warn contains msg if {
    flink.home_exists
    not flink.home_env_set
    msg := "FLINK_HOME not exported in shell. Re-enter devenv shell or run: direnv reload"
}

# Note: kafka-python check removed - current architecture uses DataGen -> Iceberg directly

# Info: Show configured Python path
info contains msg if {
    flink.python_configured
    msg := sprintf("Python configured: %s", [flink.configured_python_path])
}
