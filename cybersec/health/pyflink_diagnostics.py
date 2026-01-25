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

    # Check if FLINK_HOME env var is set (separate from bootstrap config)
    flink_home_env = os.environ.get("FLINK_HOME")
    flink_home_env_set = flink_home_env is not None and flink_home_env != ""

    flink_home_exists = flink_home.exists() if flink_home else False
    flink_binary_exists = False
    python_settings_configured = False

    diagnostics["flink_config"] = {
        "flink_home": str(flink_home) if flink_home else "not set",
        "flink_home_exists": flink_home_exists,
        "flink_home_env": flink_home_env or "not set",
        "flink_home_env_set": flink_home_env_set,
    }

    configured_python_path = None
    configured_python_exists = False
    flink_conf_mtime = None

    if flink_home and flink_home_exists:
        flink_bin = flink_home / "bin" / "flink"
        flink_binary_exists = flink_bin.exists()
        diagnostics["flink_config"]["flink_binary_exists"] = flink_binary_exists

        flink_conf = flink_home / "conf" / "flink-conf.yaml"
        if flink_conf.exists():
            try:
                flink_conf_mtime = os.path.getmtime(flink_conf)
                diagnostics["flink_config"]["config_mtime"] = flink_conf_mtime

                content = flink_conf.read_text()
                python_settings = [
                    line.strip() for line in content.split("\n")
                    if ("python.executable" in line.lower() or "python.client.executable" in line.lower())
                    and not line.strip().startswith("#")
                ]
                diagnostics["flink_config"]["python_settings"] = python_settings or ["none configured"]
                python_settings_configured = len(python_settings) > 0

                # Extract the configured Python path
                for setting in python_settings:
                    if "python.executable:" in setting and "client" not in setting:
                        configured_python_path = setting.split(":", 1)[1].strip()
                        break
                    elif "python.client.executable:" in setting and not configured_python_path:
                        configured_python_path = setting.split(":", 1)[1].strip()

                if configured_python_path:
                    configured_python_exists = Path(configured_python_path).exists()
                    diagnostics["flink_config"]["configured_python_path"] = configured_python_path
                    diagnostics["flink_config"]["configured_python_exists"] = configured_python_exists

            except Exception as e:
                diagnostics["flink_config"]["config_error"] = str(e)

    # Check Flink process start times vs config mtime
    flink_process_stale = False
    taskmanager_start_time = None
    try:
        result = subprocess.run(
            ["pgrep", "-f", "TaskManager"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            tm_pid = result.stdout.strip().split()[0]
            # Get process start time
            stat_result = subprocess.run(
                ["ps", "-o", "lstart=", "-p", tm_pid],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if stat_result.returncode == 0:
                diagnostics["flink_config"]["taskmanager_pid"] = tm_pid
                diagnostics["flink_config"]["taskmanager_start"] = stat_result.stdout.strip()

                # Parse and compare times (approximate check)
                # If config was modified after TM started, cluster is stale
                if flink_conf_mtime:
                    # Use /proc on Linux for more precise timing
                    proc_stat = Path(f"/proc/{tm_pid}/stat")
                    if proc_stat.exists():
                        # Process start time from /proc
                        import time
                        boot_time = None
                        try:
                            with open("/proc/stat") as f:
                                for line in f:
                                    if line.startswith("btime"):
                                        boot_time = int(line.split()[1])
                                        break
                            with open(proc_stat) as f:
                                stat_fields = f.read().split()
                                # Field 22 is starttime in clock ticks since boot
                                starttime_ticks = int(stat_fields[21])
                                clk_tck = os.sysconf(os.sysconf_names['SC_CLK_TCK'])
                                taskmanager_start_time = boot_time + (starttime_ticks / clk_tck)
                                diagnostics["flink_config"]["taskmanager_start_epoch"] = taskmanager_start_time

                                if flink_conf_mtime > taskmanager_start_time:
                                    flink_process_stale = True
                                    diagnostics["flink_config"]["process_stale"] = True
                                    diagnostics["flink_config"]["config_newer_than_process"] = True
                        except Exception:
                            pass
    except Exception as e:
        diagnostics["flink_config"]["process_check_error"] = str(e)

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

    # PYFLINK_007: Config Written But Not Applied
    # Detect: config has python settings, but still getting "Python process exits with code: 1"
    python_exit_error = any("Python process exits with code: 1" in err for err in submit_log_errors)
    if python_settings_configured and python_exit_error:
        fm = get_failure_mode("PYFLINK_007")
        if fm:
            rpn = fm.calculate_rpn()
            detected_issues.append({
                "failure_mode_id": "PYFLINK_007",
                "name": fm.name,
                "severity": "critical",
                "symptom": fm.symptom,
                "rpn": rpn.rpn,
                "remediation": fm.remediation_steps,
                "details": ["Config has Python settings but error persists - cluster likely not restarted"],
            })
            recommendations.insert(0, "Restart Flink cluster: devenv tasks run restart:clean")

    # PYFLINK_008: Flink Cluster Stale After Config Change
    if flink_process_stale:
        fm = get_failure_mode("PYFLINK_008")
        if fm:
            rpn = fm.calculate_rpn()
            detected_issues.append({
                "failure_mode_id": "PYFLINK_008",
                "name": fm.name,
                "severity": "critical",
                "symptom": fm.symptom,
                "rpn": rpn.rpn,
                "remediation": fm.remediation_steps,
                "details": ["flink-conf.yaml modified after TaskManager started"],
            })
            recommendations.insert(0, "Restart Flink cluster to apply config: devenv tasks run restart:clean")

    # PYFLINK_009: Python Executable Not Found by Flink
    if configured_python_path and not configured_python_exists:
        fm = get_failure_mode("PYFLINK_009")
        if fm:
            rpn = fm.calculate_rpn()
            detected_issues.append({
                "failure_mode_id": "PYFLINK_009",
                "name": fm.name,
                "severity": "critical",
                "symptom": fm.symptom,
                "rpn": rpn.rpn,
                "remediation": fm.remediation_steps,
                "details": [f"Configured path does not exist: {configured_python_path}"],
            })
            recommendations.insert(0, f"Fix Python path in flink-conf.yaml: {configured_python_path} not found")

    # PYFLINK_010: FLINK_HOME Not Exported
    if flink_home_exists and not flink_home_env_set:
        fm = get_failure_mode("PYFLINK_010")
        if fm:
            rpn = fm.calculate_rpn()
            detected_issues.append({
                "failure_mode_id": "PYFLINK_010",
                "name": fm.name,
                "severity": "info",
                "symptom": fm.symptom,
                "rpn": rpn.rpn,
                "remediation": fm.remediation_steps,
                "details": [f"Bootstrap config has flink_home={flink_home}, but $FLINK_HOME not set in shell"],
            })
            recommendations.append(f"Export FLINK_HOME: export FLINK_HOME={flink_home}")

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
