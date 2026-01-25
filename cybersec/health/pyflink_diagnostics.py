"""PyFlink diagnostic utilities.

Gathers diagnostic information when PyFlink jobs fail and provides
FMEA-based remediation recommendations.

Used by both CLI and MCP interfaces.
"""

import os
import sys
import platform
import subprocess
import glob
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .catalog import get_failure_mode, get_category_modes


@dataclass
class DiagnosticCheck:
    """Result of a diagnostic check."""
    failure_mode_id: str
    detected: bool
    details: str
    severity: str  # "critical", "warning", "info"


async def gather_pyflink_diagnostics() -> dict[str, Any]:
    """Gather diagnostic information for PyFlink job failures.

    Runs FMEA-based checks and returns detected issues with remediation steps.

    Returns:
        Diagnostic report with:
        - platform: System info
        - python_environment: Python paths and packages
        - flink_config: Flink settings
        - logs: Recent log entries
        - issues: Detected failure modes with remediation
        - recommendations: Prioritized action items
    """
    from ..bootstrap import BootstrapService

    diagnostics: dict[str, Any] = {
        "platform": {},
        "python_environment": {},
        "flink_config": {},
        "logs": {},
        "issues": [],
        "recommendations": [],
    }

    # === Gather diagnostic data ===

    # Platform info
    is_macos = platform.system() == "Darwin"
    diagnostics["platform"] = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "is_macos": is_macos,
    }

    # Python environment
    diagnostics["python_environment"] = {
        "version": sys.version,
        "executable": sys.executable,
        "prefix": sys.prefix,
        "path": sys.path[:5],
        "pythonpath": os.environ.get("PYTHONPATH", "not set"),
    }

    # Check PyFlink
    pyflink_installed = False
    try:
        import pyflink
        diagnostics["python_environment"]["pyflink_version"] = getattr(pyflink, "__version__", "unknown")
        diagnostics["python_environment"]["pyflink_location"] = pyflink.__file__
        pyflink_installed = True
    except ImportError as e:
        diagnostics["python_environment"]["pyflink_error"] = str(e)

    # Check kafka-python
    kafka_installed = False
    try:
        import kafka
        diagnostics["python_environment"]["kafka_python"] = "installed"
        kafka_installed = True
    except ImportError:
        diagnostics["python_environment"]["kafka_python"] = "missing"

    # System python3
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

    # Devenv python (macOS)
    devenv_python_exists = False
    if os.environ.get("DEVENV_ROOT"):
        devenv_python = os.environ.get("DEVENV_ROOT", "") + "/.devenv/profile/bin/python3"
        diagnostics["python_environment"]["devenv_python"] = devenv_python
        devenv_python_exists = Path(devenv_python).exists()
        diagnostics["python_environment"]["devenv_python_exists"] = devenv_python_exists

    # Flink configuration
    service = BootstrapService()
    config = service.get_config()
    flink_home = config.get_flink_home()

    flink_home_exists = flink_home.exists() if flink_home else False
    flink_binary_exists = False
    python_settings_configured = False

    diagnostics["flink_config"] = {
        "flink_home": str(flink_home) if flink_home else "not set",
        "flink_home_exists": flink_home_exists,
    }

    if flink_home and flink_home_exists:
        flink_bin = flink_home / "bin" / "flink"
        flink_binary_exists = flink_bin.exists()
        diagnostics["flink_config"]["flink_binary_exists"] = flink_binary_exists

        flink_conf = flink_home / "conf" / "flink-conf.yaml"
        if flink_conf.exists():
            try:
                content = flink_conf.read_text()
                python_settings = [
                    line.strip() for line in content.split("\n")
                    if ("python.executable" in line.lower() or "python.client.executable" in line.lower())
                    and not line.strip().startswith("#")
                ]
                diagnostics["flink_config"]["python_settings"] = python_settings or ["none configured"]
                python_settings_configured = len(python_settings) > 0
            except Exception as e:
                diagnostics["flink_config"]["config_error"] = str(e)

    # Logs
    submit_log_errors = []
    submit_log = Path("/tmp/cloudtrail_submit.log")
    if submit_log.exists():
        try:
            content = submit_log.read_text()
            lines = content.strip().split("\n")
            recent = lines[-30:] if len(lines) > 30 else lines
            submit_log_errors = [
                l for l in recent
                if "error" in l.lower() or "exception" in l.lower() or "failed" in l.lower()
            ]
            diagnostics["logs"]["submit_log"] = {
                "path": str(submit_log),
                "total_lines": len(lines),
                "recent_errors": submit_log_errors[-10:] if submit_log_errors else ["no errors found"],
                "last_10_lines": lines[-10:],
            }
        except Exception as e:
            diagnostics["logs"]["submit_log_error"] = str(e)
    else:
        diagnostics["logs"]["submit_log"] = "not found at /tmp/cloudtrail_submit.log"

    # TaskManager logs
    if flink_home and flink_home_exists:
        log_dir = flink_home / "log"
        if log_dir.exists():
            tm_logs = sorted(glob.glob(str(log_dir / "*taskmanager*.log")), key=os.path.getmtime, reverse=True)
            if tm_logs:
                try:
                    latest_tm = Path(tm_logs[0])
                    content = latest_tm.read_text()
                    lines = content.strip().split("\n")
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

    # === Run FMEA-based checks ===

    detected_issues: list[dict] = []
    recommendations: list[str] = []

    # PYFLINK_001: PyFlink Not Installed
    if not pyflink_installed:
        fm = get_failure_mode("PYFLINK_001")
        if fm:
            rpn = fm.calculate_rpn()
            detected_issues.append({
                "failure_mode_id": "PYFLINK_001",
                "name": fm.name,
                "severity": "critical",
                "symptom": fm.symptom,
                "rpn": rpn.rpn,
                "remediation": fm.remediation_steps,
            })
            recommendations.extend(fm.remediation_steps)

    # PYFLINK_002: Python Path Mismatch (especially on macOS)
    if pyflink_installed and is_macos and not python_settings_configured:
        fm = get_failure_mode("PYFLINK_002")
        if fm:
            rpn = fm.calculate_rpn()
            detected_issues.append({
                "failure_mode_id": "PYFLINK_002",
                "name": fm.name,
                "severity": "critical" if is_macos else "warning",
                "symptom": fm.symptom,
                "rpn": rpn.rpn,
                "remediation": fm.remediation_steps,
            })
            # Provide specific path for macOS
            if devenv_python_exists:
                devenv_path = diagnostics["python_environment"].get("devenv_python", "")
                recommendations.append(f"Add to $FLINK_HOME/conf/flink-conf.yaml:")
                recommendations.append(f"  python.client.executable: {devenv_path}")
                recommendations.append(f"  python.executable: {devenv_path}")
            else:
                recommendations.extend(fm.remediation_steps)

    # PYFLINK_003: kafka-python Missing
    if not kafka_installed:
        fm = get_failure_mode("PYFLINK_003")
        if fm:
            rpn = fm.calculate_rpn()
            detected_issues.append({
                "failure_mode_id": "PYFLINK_003",
                "name": fm.name,
                "severity": "warning",
                "symptom": fm.symptom,
                "rpn": rpn.rpn,
                "remediation": fm.remediation_steps,
            })
            recommendations.extend(fm.remediation_steps)

    # PYFLINK_004: FLINK_HOME Not Set
    if not flink_home or not flink_home_exists:
        fm = get_failure_mode("PYFLINK_004")
        if fm:
            rpn = fm.calculate_rpn()
            detected_issues.append({
                "failure_mode_id": "PYFLINK_004",
                "name": fm.name,
                "severity": "critical",
                "symptom": fm.symptom,
                "rpn": rpn.rpn,
                "remediation": fm.remediation_steps,
            })
            recommendations.extend(fm.remediation_steps)

    # PYFLINK_005: macOS Python Configuration
    if is_macos and flink_home_exists and not python_settings_configured:
        fm = get_failure_mode("PYFLINK_005")
        if fm:
            rpn = fm.calculate_rpn()
            detected_issues.append({
                "failure_mode_id": "PYFLINK_005",
                "name": fm.name,
                "severity": "critical",
                "symptom": fm.symptom,
                "rpn": rpn.rpn,
                "remediation": fm.remediation_steps,
            })
            # Don't duplicate - PYFLINK_002 already added specific recommendations

    # PYFLINK_006: Job Submission Log Errors
    if submit_log_errors:
        fm = get_failure_mode("PYFLINK_006")
        if fm:
            rpn = fm.calculate_rpn()
            detected_issues.append({
                "failure_mode_id": "PYFLINK_006",
                "name": fm.name,
                "severity": "warning",
                "symptom": fm.symptom,
                "rpn": rpn.rpn,
                "remediation": fm.remediation_steps,
                "details": submit_log_errors[:3],  # Include first 3 errors
            })
            recommendations.append("Review /tmp/cloudtrail_submit.log for error details")

    # === Summary ===

    diagnostics["issues"] = detected_issues

    # Deduplicate and prioritize recommendations
    seen = set()
    unique_recommendations = []
    for rec in recommendations:
        if rec not in seen:
            seen.add(rec)
            unique_recommendations.append(rec)

    diagnostics["recommendations"] = unique_recommendations

    # Add status summary
    if detected_issues:
        critical_count = sum(1 for i in detected_issues if i["severity"] == "critical")
        warning_count = sum(1 for i in detected_issues if i["severity"] == "warning")
        diagnostics["status"] = {
            "healthy": False,
            "critical_issues": critical_count,
            "warning_issues": warning_count,
            "total_issues": len(detected_issues),
        }
    else:
        diagnostics["status"] = {
            "healthy": True,
            "critical_issues": 0,
            "warning_issues": 0,
            "total_issues": 0,
        }
        diagnostics["recommendations"].append("No issues detected. PyFlink environment appears healthy.")

    return diagnostics
