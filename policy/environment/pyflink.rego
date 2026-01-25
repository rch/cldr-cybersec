# PyFlink Environment Policy
#
# Validates that the PyFlink environment is correctly configured.
# Run with: conftest test build/environment.json --policy policy/environment/

package environment.pyflink

import rego.v1

# Deny if FLINK_HOME is not set or doesn't exist
deny contains msg if {
    not input.flink.home_exists
    msg := "FLINK_HOME directory does not exist. Run: devenv tasks run restart:clean"
}

# Deny if Flink binary is missing
deny contains msg if {
    input.flink.home_exists
    not input.flink.binary_exists
    msg := "Flink binary not found. Flink may not be built. Run: devenv tasks run restart:clean"
}

# Deny if PyFlink is not installed
deny contains msg if {
    not input.python.pyflink_installed
    msg := "PyFlink not installed. Run: uv pip install apache-flink"
}

# Deny if Python path mismatch on macOS without config
deny contains msg if {
    input.platform.is_macos
    input.flink.home_exists
    not input.flink.python_configured
    msg := "macOS detected but Python not configured in flink-conf.yaml. Run: cybersec --cmd '/health fix pyflink'"
}

# Deny if configured Python path doesn't exist
deny contains msg if {
    input.flink.configured_python_path != ""
    not input.flink.configured_python_exists
    msg := sprintf("Configured Python path does not exist: %s", [input.flink.configured_python_path])
}

# Deny if Flink cluster is stale (config newer than process)
deny contains msg if {
    input.flink.process_stale
    msg := "Flink cluster running with stale config. Restart cluster: devenv tasks run restart:clean"
}

# Warn if FLINK_HOME env var not exported
warn contains msg if {
    input.flink.home_exists
    not input.flink.home_env_set
    msg := "FLINK_HOME not exported in shell. Re-enter devenv shell or run: direnv reload"
}

# Warn if kafka-python not installed
warn contains msg if {
    not input.python.kafka_installed
    msg := "kafka-python not installed. Kafka connectors may fail. Run: uv pip install kafka-python"
}

# Info: Show configured Python path
info contains msg if {
    input.flink.python_configured
    msg := sprintf("Python configured: %s", [input.flink.configured_python_path])
}
