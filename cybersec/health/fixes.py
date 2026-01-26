"""Health fix implementations.

Provides automated fixes for detected health issues across all categories:
- Flink/PyFlink environment issues
- Infrastructure issues (shared memory, etc.)

Uses FMEA tier system to determine which fixes can be auto-applied.
"""

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


async def _find_python_with_pyflink() -> str | None:
    """Find a Python interpreter that has pyflink installed.

    Checks multiple candidate paths in order of preference:
    1. UV virtualenv Python (.devenv/state/venv/bin/python3)
    2. Devenv profile Python (.devenv/profile/bin/python3)
    3. Current Python (sys.executable)

    Returns the first one that can import pyflink, or None if none found.
    """
    import sys

    devenv_root = os.environ.get("DEVENV_ROOT", os.getcwd())

    candidates = [
        # UV virtualenv - where uv sync installs packages
        Path(devenv_root) / ".devenv" / "state" / "venv" / "bin" / "python3",
        # Devenv profile Python
        Path(devenv_root) / ".devenv" / "profile" / "bin" / "python3",
        # Current Python
        Path(sys.executable),
    ]

    for python_path in candidates:
        if not python_path.exists():
            continue

        try:
            result = subprocess.run(
                [str(python_path), "-c", "import pyflink"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return str(python_path)
        except (subprocess.TimeoutExpired, Exception):
            continue

    return None


async def apply_fixes(diagnostics: dict, dry_run: bool = True) -> list[dict[str, Any]]:
    """Apply fixes for detected health issues.

    Args:
        diagnostics: Dict containing 'issues' list from health checks
        dry_run: If True, show what would be done without making changes

    Returns:
        List of fix results with status and details
    """
    from ..bootstrap import BootstrapService

    results: list[dict[str, Any]] = []
    issues = diagnostics.get("issues", [])

    if not issues:
        return results

    service = BootstrapService()
    config = service.get_config()
    flink_home = config.get_flink_home()

    for issue in issues:
        failure_mode_id = issue.get("failure_mode_id", "")

        if failure_mode_id == "PYFLINK_002":
            # Python path mismatch - fix flink-conf.yaml
            result = await _fix_python_path_mismatch(diagnostics, flink_home, dry_run)
            results.append(result)

        elif failure_mode_id == "PYFLINK_004":
            # FLINK_HOME not set - manual fix required
            results.append({
                "failure_mode_id": "PYFLINK_004",
                "action": "manual_required",
                "success": False,
                "message": "FLINK_HOME not set. Run: devenv tasks run restart:clean",
                "details": "This will build Flink from source if needed.",
            })

        elif failure_mode_id == "PYFLINK_005":
            # macOS Python configuration - same fix as PYFLINK_002
            # Already handled by PYFLINK_002, skip duplicate
            if not any(r.get("failure_mode_id") == "PYFLINK_002" for r in results):
                result = await _fix_python_path_mismatch(diagnostics, flink_home, dry_run)
                result["failure_mode_id"] = "PYFLINK_005"
                results.append(result)

        elif failure_mode_id == "PYFLINK_006":
            # Log errors - informational only
            results.append({
                "failure_mode_id": "PYFLINK_006",
                "action": "review_required",
                "success": True,
                "message": "Log errors detected - review recommended",
                "details": "Check /tmp/cloudtrail_submit.log for specific errors",
            })

        elif failure_mode_id == "PYFLINK_007":
            # Config written but not applied - need cluster restart
            result = await _fix_flink_cluster_restart(flink_home, dry_run)
            result["failure_mode_id"] = "PYFLINK_007"
            results.append(result)

        elif failure_mode_id == "PYFLINK_008":
            # Flink cluster stale - need cluster restart
            result = await _fix_flink_cluster_restart(flink_home, dry_run)
            result["failure_mode_id"] = "PYFLINK_008"
            results.append(result)

        elif failure_mode_id == "PYFLINK_009":
            # Python executable not found - re-run config fix
            result = await _fix_python_path_mismatch(diagnostics, flink_home, dry_run)
            result["failure_mode_id"] = "PYFLINK_009"
            result["message"] = "Re-detected Python path and updated flink-conf.yaml"
            results.append(result)

        elif failure_mode_id == "PYFLINK_011":
            # Iceberg AWS bundle missing - build and install
            result = await _fix_iceberg_jars_missing(flink_home, dry_run)
            result["failure_mode_id"] = "PYFLINK_011"
            results.append(result)

        elif failure_mode_id == "PYFLINK_012":
            # Iceberg Flink runtime missing - build and install
            # Same fix as PYFLINK_011 - builds both JARs
            if not any(r.get("failure_mode_id") == "PYFLINK_011" for r in results):
                result = await _fix_iceberg_jars_missing(flink_home, dry_run)
                result["failure_mode_id"] = "PYFLINK_012"
                results.append(result)

        elif failure_mode_id == "FLINK_004":
            # DataGen job finishes immediately - fix bounded source
            result = await _fix_datagen_bounded_source(dry_run)
            results.append(result)

        elif failure_mode_id == "FLINK_005":
            # Job stuck initializing - provide diagnostic guidance
            results.append({
                "failure_mode_id": "FLINK_005",
                "action": "review_required",
                "success": True,
                "message": "Job stuck in CREATED/INITIALIZING state - manual investigation required",
                "details": "Check Flink logs for errors, verify resources available, run /health pyflink",
            })

        elif failure_mode_id == "PYFLINK_014":
            # Iceberg JAR version mismatch - clean rebuild
            result = await _fix_iceberg_version_mismatch(flink_home, dry_run)
            results.append(result)

        elif failure_mode_id == "INFRA_004":
            # Shared memory exhaustion - clean up orphaned IPC segments
            result = await _fix_shared_memory_exhaustion(dry_run)
            results.append(result)

        elif failure_mode_id == "NIFI_001":
            # NiFi not installed - download and install
            result = await _fix_nifi_not_installed(dry_run)
            results.append(result)

    return results


async def _fix_python_path_mismatch(
    diagnostics: dict,
    flink_home: Path | None,
    dry_run: bool
) -> dict[str, Any]:
    """Fix: Update flink-conf.yaml with correct Python path."""

    result = {
        "failure_mode_id": "PYFLINK_002",
        "action": "update_flink_config",
    }

    if not flink_home or not flink_home.exists():
        result["success"] = False
        result["message"] = "FLINK_HOME not found - cannot update config"
        return result

    flink_conf_path = flink_home / "conf" / "flink-conf.yaml"
    if not flink_conf_path.exists():
        result["success"] = False
        result["message"] = f"flink-conf.yaml not found at {flink_conf_path}"
        return result

    # Determine the correct Python path - must have pyflink installed
    python_path = await _find_python_with_pyflink()

    if not python_path:
        result["success"] = False
        result["message"] = "Could not find a Python with pyflink installed. Run: uv sync"
        return result

    result["python_path"] = python_path
    result["config_file"] = str(flink_conf_path)

    # Lines to add
    lines_to_add = [
        f"python.client.executable: {python_path}",
        f"python.executable: {python_path}",
    ]
    result["lines_to_add"] = lines_to_add

    if dry_run:
        result["success"] = True
        result["dry_run"] = True
        result["message"] = f"Would add Python configuration to {flink_conf_path}"
        return result

    # Read existing config
    try:
        content = flink_conf_path.read_text()
        lines = content.split("\n")

        # Check if settings already exist
        has_client_exec = any("python.client.executable:" in line and not line.strip().startswith("#") for line in lines)
        has_exec = any("python.executable:" in line and "client" not in line and not line.strip().startswith("#") for line in lines)

        # Backup original
        backup_path = flink_conf_path.with_suffix(".yaml.bak")
        shutil.copy(flink_conf_path, backup_path)
        result["backup"] = str(backup_path)

        # Update or append settings
        new_lines = []
        for line in lines:
            # Skip existing python executable settings (we'll add new ones)
            if "python.client.executable:" in line and not line.strip().startswith("#"):
                continue
            if "python.executable:" in line and "client" not in line and not line.strip().startswith("#"):
                continue
            new_lines.append(line)

        # Add new settings at end (before any trailing empty lines)
        while new_lines and new_lines[-1].strip() == "":
            new_lines.pop()

        new_lines.append("")
        new_lines.append("# PyFlink Python configuration (added by cybersec health fix)")
        for line in lines_to_add:
            new_lines.append(line)
        new_lines.append("")

        # Write updated config
        flink_conf_path.write_text("\n".join(new_lines))

        result["success"] = True
        result["message"] = f"Updated {flink_conf_path} with Python configuration"
        result["restart_required"] = True
        result["restart_command"] = "devenv tasks run restart:clean"

    except Exception as e:
        result["success"] = False
        result["message"] = f"Error updating config: {e}"

    return result


async def _fix_iceberg_jars_missing(flink_home: Path | None, dry_run: bool) -> dict[str, Any]:
    """Fix: Build and install Iceberg JARs (Flink runtime + AWS bundle).

    This builds the iceberg-flink-runtime-1.20 and iceberg-aws-bundle JARs
    from the thirdparty/iceberg submodule and copies them to Flink's lib directory.
    """
    import subprocess

    result = {
        "action": "build_iceberg_jars",
        "command": "./gradlew :iceberg-flink:iceberg-flink-runtime-1.20:shadowJar :iceberg-aws-bundle:shadowJar",
    }

    if not flink_home or not flink_home.exists():
        result["success"] = False
        result["message"] = "FLINK_HOME not found - cannot install Iceberg JARs"
        return result

    # Determine iceberg directory
    devenv_root = os.environ.get("DEVENV_ROOT", os.getcwd())
    iceberg_dir = Path(devenv_root) / "thirdparty" / "iceberg"

    if not iceberg_dir.exists():
        result["success"] = False
        result["message"] = f"Iceberg source not found at {iceberg_dir}. Run: git submodule update --init --recursive"
        return result

    # Check if submodule is initialized (has gradlew)
    gradlew = iceberg_dir / "gradlew"
    if not gradlew.exists():
        result["success"] = False
        result["message"] = "Iceberg submodule not initialized. Run: git submodule update --init --recursive"
        result["command"] = "git submodule update --init --recursive"
        return result

    lib_dir = flink_home / "lib"

    if dry_run:
        result["success"] = True
        result["dry_run"] = True
        result["message"] = "Would build and install Iceberg JARs"
        result["steps"] = [
            f"cd {iceberg_dir}",
            "./gradlew -PflinkVersions=1.20 :iceberg-flink:iceberg-flink-runtime-1.20:shadowJar :iceberg-aws-bundle:shadowJar -x test",
            f"cp flink/v1.20/flink-runtime/build/libs/iceberg-flink-runtime-1.20-*.jar {lib_dir}/",
            f"cp aws-bundle/build/libs/iceberg-aws-bundle-*.jar {lib_dir}/",
        ]
        return result

    # Build the JARs
    try:
        result["build_output"] = []

        build_cmd = [
            "./gradlew",
            "-PflinkVersions=1.20",
            ":iceberg-flink:iceberg-flink-runtime-1.20:shadowJar",
            ":iceberg-aws-bundle:shadowJar",
            "-x", "test",
            "-x", "integrationTest",
            "-x", "generateGitProperties",
        ]

        result["build_command"] = " ".join(build_cmd)

        proc = subprocess.run(
            build_cmd,
            capture_output=True,
            text=True,
            timeout=900,  # 15 min timeout for build
            cwd=str(iceberg_dir),
        )

        if proc.returncode != 0:
            result["success"] = False
            result["message"] = "Gradle build failed"
            result["error"] = proc.stderr[-2000:] if len(proc.stderr) > 2000 else proc.stderr
            return result

        result["build_output"].append("Gradle build succeeded")

        # Copy the JARs
        copied_jars = []

        # Copy Flink runtime JAR
        flink_runtime_dir = iceberg_dir / "flink" / "v1.20" / "flink-runtime" / "build" / "libs"
        for jar in flink_runtime_dir.glob("iceberg-flink-runtime-1.20-*.jar"):
            if not jar.name.endswith("-sources.jar") and not jar.name.endswith("-javadoc.jar"):
                dest = lib_dir / jar.name
                shutil.copy(jar, dest)
                copied_jars.append(str(dest))

        # Copy AWS bundle JAR
        aws_bundle_dir = iceberg_dir / "aws-bundle" / "build" / "libs"
        for jar in aws_bundle_dir.glob("iceberg-aws-bundle-*.jar"):
            if not jar.name.endswith("-sources.jar") and not jar.name.endswith("-javadoc.jar"):
                dest = lib_dir / jar.name
                shutil.copy(jar, dest)
                copied_jars.append(str(dest))

        if copied_jars:
            result["success"] = True
            result["message"] = f"Built and installed {len(copied_jars)} Iceberg JAR(s)"
            result["installed_jars"] = copied_jars
            result["restart_required"] = True
            result["restart_command"] = "devenv tasks run restart:clean"
        else:
            result["success"] = False
            result["message"] = "Build succeeded but no JARs found to copy"
            result["searched"] = [str(flink_runtime_dir), str(aws_bundle_dir)]

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["message"] = "Gradle build timed out after 15 minutes"
    except Exception as e:
        result["success"] = False
        result["message"] = f"Error building/installing JARs: {e}"

    return result


async def _fix_iceberg_version_mismatch(flink_home: Path | None, dry_run: bool) -> dict[str, Any]:
    """Fix: Clean rebuild of Iceberg JARs to resolve version mismatch.

    This removes all existing Iceberg JARs and rebuilds from source to ensure
    consistent serialVersionUID across all Iceberg classes.
    """
    import subprocess

    result = {
        "failure_mode_id": "PYFLINK_014",
        "action": "rebuild_iceberg_jars",
    }

    if not flink_home or not flink_home.exists():
        result["success"] = False
        result["message"] = "FLINK_HOME not found - cannot fix Iceberg JARs"
        return result

    lib_dir = flink_home / "lib"
    devenv_root = os.environ.get("DEVENV_ROOT", os.getcwd())
    iceberg_dir = Path(devenv_root) / "thirdparty" / "iceberg"

    if not iceberg_dir.exists():
        result["success"] = False
        result["message"] = f"Iceberg source not found at {iceberg_dir}. Run: git submodule update --init --recursive"
        return result

    gradlew = iceberg_dir / "gradlew"
    if not gradlew.exists():
        result["success"] = False
        result["message"] = "Iceberg submodule not initialized. Run: git submodule update --init --recursive"
        return result

    # Find existing Iceberg JARs
    existing_jars = list(lib_dir.glob("iceberg-*.jar"))
    result["existing_jars"] = [str(j) for j in existing_jars]

    if dry_run:
        result["success"] = True
        result["dry_run"] = True
        result["message"] = "Would clean rebuild Iceberg JARs to fix version mismatch"
        result["steps"] = [
            f"Remove {len(existing_jars)} existing Iceberg JAR(s) from {lib_dir}",
            f"cd {iceberg_dir}",
            "./gradlew clean",
            "./gradlew -PflinkVersions=1.20 :iceberg-flink:iceberg-flink-runtime-1.20:shadowJar :iceberg-aws-bundle:shadowJar -x test",
            f"Copy new JARs to {lib_dir}",
            "Restart Flink cluster",
        ]
        return result

    try:
        # Step 1: Remove existing Iceberg JARs
        removed_jars = []
        for jar in existing_jars:
            jar.unlink()
            removed_jars.append(str(jar))
        result["removed_jars"] = removed_jars

        # Step 2: Clean build
        clean_cmd = ["./gradlew", "clean"]
        clean_proc = subprocess.run(
            clean_cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(iceberg_dir),
        )

        if clean_proc.returncode != 0:
            result["success"] = False
            result["message"] = "Gradle clean failed"
            result["error"] = clean_proc.stderr[-1000:] if len(clean_proc.stderr) > 1000 else clean_proc.stderr
            return result

        # Step 3: Build fresh JARs
        build_cmd = [
            "./gradlew",
            "-PflinkVersions=1.20",
            ":iceberg-flink:iceberg-flink-runtime-1.20:shadowJar",
            ":iceberg-aws-bundle:shadowJar",
            "-x", "test",
            "-x", "integrationTest",
            "-x", "generateGitProperties",
        ]

        build_proc = subprocess.run(
            build_cmd,
            capture_output=True,
            text=True,
            timeout=900,  # 15 min timeout
            cwd=str(iceberg_dir),
        )

        if build_proc.returncode != 0:
            result["success"] = False
            result["message"] = "Gradle build failed"
            result["error"] = build_proc.stderr[-2000:] if len(build_proc.stderr) > 2000 else build_proc.stderr
            return result

        # Step 4: Copy new JARs
        copied_jars = []

        flink_runtime_dir = iceberg_dir / "flink" / "v1.20" / "flink-runtime" / "build" / "libs"
        for jar in flink_runtime_dir.glob("iceberg-flink-runtime-1.20-*.jar"):
            if not jar.name.endswith("-sources.jar") and not jar.name.endswith("-javadoc.jar"):
                dest = lib_dir / jar.name
                shutil.copy(jar, dest)
                copied_jars.append(str(dest))

        aws_bundle_dir = iceberg_dir / "aws-bundle" / "build" / "libs"
        for jar in aws_bundle_dir.glob("iceberg-aws-bundle-*.jar"):
            if not jar.name.endswith("-sources.jar") and not jar.name.endswith("-javadoc.jar"):
                dest = lib_dir / jar.name
                shutil.copy(jar, dest)
                copied_jars.append(str(dest))

        if copied_jars:
            result["success"] = True
            result["message"] = f"Clean rebuilt and installed {len(copied_jars)} Iceberg JAR(s)"
            result["installed_jars"] = copied_jars
            result["restart_required"] = True
            result["restart_command"] = "devenv tasks run restart:clean"
        else:
            result["success"] = False
            result["message"] = "Build succeeded but no JARs found to copy"

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["message"] = "Gradle build timed out after 15 minutes"
    except Exception as e:
        result["success"] = False
        result["message"] = f"Error rebuilding JARs: {e}"

    return result


async def _fix_datagen_bounded_source(dry_run: bool) -> dict[str, Any]:
    """Fix: Remove bounded row configuration from DataGen source.

    The datagen connector has 'fields.event_id.end' which makes it bounded.
    For continuous streaming, we need to remove this or use unbounded mode.
    """
    result = {
        "failure_mode_id": "FLINK_004",
        "action": "update_datagen_source",
    }

    # Find the cloudtrail_datagen.py file
    devenv_root = os.environ.get("DEVENV_ROOT", os.getcwd())
    datagen_file = Path(devenv_root) / "flink_jobs" / "cloudtrail_datagen.py"

    if not datagen_file.exists():
        result["success"] = False
        result["message"] = f"DataGen file not found at {datagen_file}"
        return result

    result["file"] = str(datagen_file)

    try:
        content = datagen_file.read_text()
        original_content = content

        # Pattern to find the bounded field configuration
        # Look for: 'fields.event_id.end' = '1000000' (or any number)
        bounded_pattern = r"'fields\.event_id\.end'\s*=\s*'[^']+'"

        if not re.search(bounded_pattern, content):
            result["success"] = True
            result["message"] = "DataGen source already unbounded (no fields.event_id.end found)"
            result["already_fixed"] = True
            return result

        if dry_run:
            result["success"] = True
            result["dry_run"] = True
            result["message"] = "Would remove bounded row configuration from DataGen source"
            result["changes"] = [
                "Remove: 'fields.event_id.end' = '1000000'",
                "This makes the datagen source unbounded (continuous streaming)",
            ]
            return result

        # Backup original
        backup_path = datagen_file.with_suffix(".py.bak")
        shutil.copy(datagen_file, backup_path)
        result["backup"] = str(backup_path)

        # Remove the bounded field line
        # The line looks like: 'fields.event_id.end' = '1000000',
        # We need to remove the entire line including the comma
        lines = content.split("\n")
        new_lines = []
        removed_lines = []

        for line in lines:
            if re.search(bounded_pattern, line):
                removed_lines.append(line.strip())
                continue
            new_lines.append(line)

        # Write updated content
        datagen_file.write_text("\n".join(new_lines))

        result["success"] = True
        result["message"] = "Removed bounded row configuration from DataGen source"
        result["removed_lines"] = removed_lines
        result["restart_required"] = True
        result["restart_command"] = "devenv tasks run restart:clean"

    except Exception as e:
        result["success"] = False
        result["message"] = f"Error updating DataGen source: {e}"

    return result


async def _fix_flink_cluster_restart(flink_home: Path | None, dry_run: bool) -> dict[str, Any]:
    """Fix: Restart Flink cluster to apply configuration changes."""
    import subprocess

    result = {
        "action": "restart_flink_cluster",
        "command": "devenv tasks run restart:clean",
    }

    if not flink_home or not flink_home.exists():
        result["success"] = False
        result["message"] = "FLINK_HOME not found - cannot restart cluster"
        return result

    if dry_run:
        result["success"] = True
        result["dry_run"] = True
        result["message"] = "Would restart Flink cluster via devenv tasks"
        result["steps"] = [
            f"Stop cluster: {flink_home}/bin/stop-cluster.sh",
            f"Start cluster: {flink_home}/bin/start-cluster.sh",
            "Or: devenv tasks run restart:clean",
        ]
        return result

    # Execute restart using stop/start scripts directly for targeted restart
    try:
        stop_script = flink_home / "bin" / "stop-cluster.sh"
        start_script = flink_home / "bin" / "start-cluster.sh"

        if not stop_script.exists() or not start_script.exists():
            result["success"] = False
            result["message"] = "Flink cluster scripts not found"
            return result

        # Stop cluster
        stop_proc = subprocess.run(
            [str(stop_script)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(flink_home),
        )

        # Small delay to ensure clean shutdown
        import time
        time.sleep(2)

        # Start cluster
        start_proc = subprocess.run(
            [str(start_script)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(flink_home),
        )

        if start_proc.returncode == 0:
            result["success"] = True
            result["message"] = "Flink cluster restarted successfully"
            result["stop_output"] = stop_proc.stdout
            result["start_output"] = start_proc.stdout
        else:
            result["success"] = False
            result["message"] = "Failed to restart Flink cluster"
            result["error"] = start_proc.stderr

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["message"] = "Timeout restarting Flink cluster"
    except Exception as e:
        result["success"] = False
        result["message"] = f"Error restarting cluster: {e}"

    return result


async def _fix_shared_memory_exhaustion(dry_run: bool) -> dict[str, Any]:
    """Fix: Clean up orphaned shared memory segments using ipcrm.

    On macOS, orphaned IPC shared memory segments from previous devenv crashes
    can accumulate and exhaust system limits, preventing PostgreSQL from starting.
    """
    import getpass
    import subprocess

    result = {
        "failure_mode_id": "INFRA_004",
        "action": "cleanup_shared_memory",
    }

    # Check platform - this is primarily a macOS issue
    if platform.system() not in ("Darwin", "Linux"):
        result["success"] = True
        result["message"] = f"Platform {platform.system()} - shared memory cleanup not applicable"
        result["skipped"] = True
        return result

    # List current segments using ipcs -m
    try:
        list_proc = subprocess.run(
            ["ipcs", "-m"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if list_proc.returncode != 0:
            result["success"] = False
            result["message"] = f"Failed to list shared memory segments: {list_proc.stderr}"
            return result

        # Parse segments owned by current user
        current_user = getpass.getuser()
        segments_to_remove = []

        # ipcs -m output format varies by platform:
        # macOS: T ID KEY MODE OWNER GROUP
        # Linux: key shmid owner perms bytes nattch
        lines = list_proc.stdout.strip().split('\n')

        for line in lines:
            # Skip header lines
            if not line.strip() or line.startswith('---') or 'shmid' in line.lower() or 'key' in line.lower():
                continue

            parts = line.split()
            if len(parts) >= 3:
                # On macOS, format is: T ID KEY MODE OWNER GROUP
                # On Linux, format is: key shmid owner perms bytes nattch
                if current_user in line:
                    # Extract shmid - second field on macOS, second on Linux
                    if platform.system() == "Darwin":
                        # macOS: m 65536 0x00000000 --rw------- ryanhill staff
                        if len(parts) >= 2 and parts[0] in ('m', 's', 'q'):
                            shmid = parts[1]
                            segments_to_remove.append(shmid)
                    else:
                        # Linux: 0x00000000 65536 ryanhill 600 56 0
                        if len(parts) >= 2:
                            shmid = parts[1]
                            segments_to_remove.append(shmid)

        result["current_user"] = current_user
        result["segments_found"] = len(segments_to_remove)

        if not segments_to_remove:
            result["success"] = True
            result["message"] = "No orphaned shared memory segments found"
            return result

        result["segments"] = segments_to_remove

        if dry_run:
            result["success"] = True
            result["dry_run"] = True
            result["message"] = f"Would remove {len(segments_to_remove)} shared memory segment(s)"
            return result

        # Remove each segment
        removed = []
        failed = []

        for shmid in segments_to_remove:
            proc = subprocess.run(
                ["ipcrm", "-m", shmid],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0:
                removed.append(shmid)
            else:
                failed.append({"shmid": shmid, "error": proc.stderr.strip()})

        result["removed"] = removed
        result["failed"] = failed
        result["success"] = len(removed) > 0
        result["message"] = f"Removed {len(removed)}/{len(segments_to_remove)} shared memory segment(s)"

        if removed:
            result["restart_required"] = True
            result["restart_command"] = "devenv up"

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["message"] = "Timeout running ipcs/ipcrm commands"
    except FileNotFoundError:
        result["success"] = False
        result["message"] = "ipcs command not found - cannot list shared memory segments"
    except Exception as e:
        result["success"] = False
        result["message"] = f"Error cleaning up shared memory: {e}"

    return result


async def _fix_nifi_not_installed(dry_run: bool) -> dict[str, Any]:
    """Fix: Download and install NiFi binary.

    On macOS (Apple Silicon), NiFi must be downloaded manually since nixpkgs
    doesn't provide a native binary. This runs the setup script.
    """
    import subprocess

    result = {
        "failure_mode_id": "NIFI_001",
        "action": "install_nifi",
    }

    devenv_root = os.environ.get("DEVENV_ROOT", os.getcwd())
    setup_script = Path(devenv_root) / "scripts" / "setup_nifi_bin.sh"
    nifi_version = "2.0.0"

    if not setup_script.exists():
        result["success"] = False
        result["message"] = f"Setup script not found: {setup_script}"
        return result

    result["script"] = str(setup_script)
    result["version"] = nifi_version

    if dry_run:
        result["success"] = True
        result["dry_run"] = True
        result["message"] = f"Would download and install NiFi {nifi_version}"
        result["command"] = f"./scripts/setup_nifi_bin.sh {nifi_version}"
        return result

    # Execute the setup script
    try:
        proc = subprocess.run(
            [str(setup_script), nifi_version],
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout for download
            cwd=devenv_root,
        )

        if proc.returncode == 0:
            result["success"] = True
            result["message"] = f"Installed NiFi {nifi_version}"
            result["output"] = proc.stdout[-1000:] if len(proc.stdout) > 1000 else proc.stdout
            result["restart_required"] = True
            result["restart_command"] = "devenv up nifi"
        else:
            result["success"] = False
            result["message"] = f"NiFi setup failed with exit code {proc.returncode}"
            result["error"] = proc.stderr[-1000:] if len(proc.stderr) > 1000 else proc.stderr

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["message"] = "NiFi download timed out after 10 minutes"
    except Exception as e:
        result["success"] = False
        result["message"] = f"Error installing NiFi: {e}"

    return result
