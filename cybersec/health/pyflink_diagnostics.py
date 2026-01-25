"""PyFlink diagnostic utilities.

Gathers diagnostic information when PyFlink jobs fail.
Used by both CLI and MCP interfaces.
"""

import os
import sys
import platform
import subprocess
import glob
from pathlib import Path
from typing import Any


async def gather_pyflink_diagnostics() -> dict[str, Any]:
    """Gather diagnostic information for PyFlink job failures.

    Collects:
    - Platform info (macOS vs Linux)
    - Python environment (version, paths, PYTHONPATH)
    - PyFlink/kafka-python installation status
    - Flink configuration
    - Job submission logs
    - TaskManager logs (last errors)

    Returns:
        Diagnostic report with environment, config, logs, and recommendations.
    """
    from ..bootstrap import BootstrapService

    diagnostics: dict[str, Any] = {
        "platform": {},
        "python_environment": {},
        "flink_config": {},
        "logs": {},
        "recommendations": [],
    }

    # Platform info
    diagnostics["platform"] = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "is_macos": platform.system() == "Darwin",
    }

    # Python environment
    diagnostics["python_environment"] = {
        "version": sys.version,
        "executable": sys.executable,
        "prefix": sys.prefix,
        "path": sys.path[:5],  # First 5 entries
        "pythonpath": os.environ.get("PYTHONPATH", "not set"),
    }

    # Check for pyflink
    try:
        import pyflink
        diagnostics["python_environment"]["pyflink_version"] = getattr(pyflink, "__version__", "unknown")
        diagnostics["python_environment"]["pyflink_location"] = pyflink.__file__
    except ImportError as e:
        diagnostics["python_environment"]["pyflink_error"] = str(e)
        diagnostics["recommendations"].append(
            "PyFlink not importable. Install with: uv pip install apache-flink"
        )

    # Check for kafka-python
    try:
        import kafka
        diagnostics["python_environment"]["kafka_python"] = "installed"
    except ImportError:
        diagnostics["python_environment"]["kafka_python"] = "missing"
        diagnostics["recommendations"].append(
            "kafka-python not installed. Install with: uv pip install kafka-python"
        )

    # Flink home and config
    service = BootstrapService()
    config = service.get_config()
    flink_home = config.get_flink_home()

    diagnostics["flink_config"] = {
        "flink_home": str(flink_home) if flink_home else "not set",
        "flink_home_exists": flink_home.exists() if flink_home else False,
    }

    if flink_home and flink_home.exists():
        flink_conf = flink_home / "conf" / "flink-conf.yaml"
        if flink_conf.exists():
            try:
                content = flink_conf.read_text()
                # Extract Python-related settings
                python_settings = [
                    line for line in content.split("\n")
                    if "python" in line.lower() and not line.strip().startswith("#")
                ]
                diagnostics["flink_config"]["python_settings"] = python_settings or ["none configured"]
            except Exception as e:
                diagnostics["flink_config"]["config_error"] = str(e)

        # Check for Flink Python binary
        flink_bin = flink_home / "bin" / "flink"
        diagnostics["flink_config"]["flink_binary_exists"] = flink_bin.exists()

    # Job submission log
    submit_log = Path("/tmp/cloudtrail_submit.log")
    if submit_log.exists():
        try:
            content = submit_log.read_text()
            lines = content.strip().split("\n")
            # Get last 30 lines, look for errors
            recent = lines[-30:] if len(lines) > 30 else lines
            errors = [l for l in recent if "error" in l.lower() or "exception" in l.lower() or "failed" in l.lower()]
            diagnostics["logs"]["submit_log"] = {
                "path": str(submit_log),
                "total_lines": len(lines),
                "recent_errors": errors[-10:] if errors else ["no errors found in recent lines"],
                "last_10_lines": lines[-10:],
            }
        except Exception as e:
            diagnostics["logs"]["submit_log_error"] = str(e)
    else:
        diagnostics["logs"]["submit_log"] = "not found at /tmp/cloudtrail_submit.log"

    # TaskManager logs
    if flink_home and flink_home.exists():
        log_dir = flink_home / "log"
        if log_dir.exists():
            tm_logs = sorted(glob.glob(str(log_dir / "*taskmanager*.log")), key=os.path.getmtime, reverse=True)
            if tm_logs:
                try:
                    latest_tm = Path(tm_logs[0])
                    content = latest_tm.read_text()
                    lines = content.strip().split("\n")
                    # Look for Python-related errors
                    python_errors = [
                        l for l in lines[-100:]
                        if "python" in l.lower() or "pyflink" in l.lower() or "PythonDriver" in l
                    ]
                    diagnostics["logs"]["taskmanager"] = {
                        "path": str(latest_tm),
                        "python_related_lines": python_errors[-20:] if python_errors else ["no python-related entries"],
                    }
                except Exception as e:
                    diagnostics["logs"]["taskmanager_error"] = str(e)

            # JobManager logs
            jm_logs = sorted(glob.glob(str(log_dir / "*jobmanager*.log")), key=os.path.getmtime, reverse=True)
            if jm_logs:
                try:
                    latest_jm = Path(jm_logs[0])
                    content = latest_jm.read_text()
                    lines = content.strip().split("\n")
                    python_errors = [
                        l for l in lines[-100:]
                        if "python" in l.lower() or "pyflink" in l.lower() or "PythonDriver" in l
                    ]
                    diagnostics["logs"]["jobmanager"] = {
                        "path": str(latest_jm),
                        "python_related_lines": python_errors[-20:] if python_errors else ["no python-related entries"],
                    }
                except Exception as e:
                    diagnostics["logs"]["jobmanager_error"] = str(e)

    # Check which python Flink would use
    try:
        result = subprocess.run(
            ["which", "python3"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        diagnostics["python_environment"]["system_python3"] = result.stdout.strip() or "not found"
    except Exception:
        diagnostics["python_environment"]["system_python3"] = "check failed"

    # macOS-specific checks
    if platform.system() == "Darwin":
        diagnostics["recommendations"].append(
            "On macOS, ensure Flink uses the correct Python. "
            "Set PYFLINK_CLIENT_EXECUTABLE in your environment or flink-conf.yaml"
        )

        # Check if running in devenv
        if os.environ.get("DEVENV_ROOT"):
            devenv_python = os.environ.get("DEVENV_ROOT", "") + "/.devenv/profile/bin/python3"
            diagnostics["python_environment"]["devenv_python"] = devenv_python
            diagnostics["python_environment"]["devenv_python_exists"] = Path(devenv_python).exists()

    # Generate recommendations based on findings
    if not diagnostics["flink_config"].get("flink_home"):
        diagnostics["recommendations"].append(
            "FLINK_HOME not set. Run bootstrap or set manually."
        )

    if diagnostics["python_environment"].get("pyflink_error"):
        diagnostics["recommendations"].append(
            "PyFlink import failed - check Python environment matches Flink's expected Python"
        )

    return diagnostics
