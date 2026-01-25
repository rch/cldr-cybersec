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

# Use effective config (merged static + runtime)
eff := input.effective

# Deny if FLINK_HOME is not set or doesn't exist
deny contains msg if {
    not eff.flink.home_exists
    msg := "FLINK_HOME directory does not exist. Run: devenv tasks run restart:clean"
}

# Deny if Flink binary is missing
deny contains msg if {
    eff.flink.home_exists
    not eff.flink.binary_exists
    msg := "Flink binary not found. Flink may not be built. Run: devenv tasks run restart:clean"
}

# Deny if PyFlink is not installed
deny contains msg if {
    not eff.python.pyflink_installed
    msg := "PyFlink not installed. Run: uv pip install apache-flink"
}

# Deny if Python path mismatch on macOS without config
deny contains msg if {
    eff.platform.is_macos
    eff.flink.home_exists
    not eff.flink.python_configured
    msg := "macOS detected but Python not configured in flink-conf.yaml. Run: cybersec --cmd '/health fix pyflink'"
}

# Deny if configured Python path doesn't exist
deny contains msg if {
    eff.flink.configured_python_path != ""
    not eff.flink.configured_python_exists
    msg := sprintf("Configured Python path does not exist: %s", [eff.flink.configured_python_path])
}

# Deny if Flink cluster is stale (config newer than process)
deny contains msg if {
    eff.flink.process_stale
    msg := "Flink cluster running with stale config. Restart cluster: devenv tasks run restart:clean"
}

# Warn if FLINK_HOME env var not exported
warn contains msg if {
    eff.flink.home_exists
    not eff.flink.home_env_set
    msg := "FLINK_HOME not exported in shell. Re-enter devenv shell or run: direnv reload"
}

# Warn if kafka-python not installed
warn contains msg if {
    not eff.python.kafka_installed
    msg := "kafka-python not installed. Kafka connectors may fail. Run: uv pip install kafka-python"
}

# Info: Show configured Python path
info contains msg if {
    eff.flink.python_configured
    msg := sprintf("Python configured: %s", [eff.flink.configured_python_path])
}
