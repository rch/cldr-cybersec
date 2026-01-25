"""HOCON configuration loader with environment variable substitution.

Uses pyhocon to parse HOCON files and substitute environment variables.
"""

import json
import os
from pathlib import Path
from typing import Any

from pyhocon import ConfigFactory, ConfigTree, HOCONConverter


# Cache for loaded config
_config_cache: dict[str, ConfigTree] = {}


def load_config(
    environment: str = "local",
    config_dir: Path | None = None,
) -> ConfigTree:
    """Load HOCON configuration for the specified environment.

    Args:
        environment: Environment name (local, ci, prod). Loads from
                    config/environments/<environment>.conf
        config_dir: Config directory. Defaults to config/ in project root.

    Returns:
        Parsed and resolved ConfigTree
    """
    cache_key = f"{environment}:{config_dir}"
    if cache_key in _config_cache:
        return _config_cache[cache_key]

    if config_dir is None:
        # Find config dir relative to this file or from env
        config_dir = _find_config_dir()

    env_conf = config_dir / "environments" / f"{environment}.conf"

    if env_conf.exists():
        # Load environment-specific config (which includes reference.conf)
        config = ConfigFactory.parse_file(str(env_conf), resolve=True)
    else:
        # Fall back to reference.conf only
        ref_conf = config_dir / "reference.conf"
        if not ref_conf.exists():
            raise FileNotFoundError(f"Configuration not found: {ref_conf}")
        config = ConfigFactory.parse_file(str(ref_conf), resolve=True)

    _config_cache[cache_key] = config
    return config


def _find_config_dir() -> Path:
    """Find the config directory."""
    # Try DEVENV_ROOT first
    devenv_root = os.environ.get("DEVENV_ROOT")
    if devenv_root:
        config_dir = Path(devenv_root) / "config"
        if config_dir.exists():
            return config_dir

    # Try relative to current working directory
    cwd_config = Path.cwd() / "config"
    if cwd_config.exists():
        return cwd_config

    # Try relative to this file (for installed package)
    pkg_config = Path(__file__).parent.parent.parent.parent / "config"
    if pkg_config.exists():
        return pkg_config

    raise FileNotFoundError("Cannot find config directory")


def hydrate_config(
    config: ConfigTree,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Hydrate config to JSON with all values resolved.

    Resolves all HOCON references and environment variable substitutions,
    then outputs a flat JSON file suitable for conftest validation.

    Args:
        config: Parsed ConfigTree
        output_path: Output file path. If None, returns dict without writing.

    Returns:
        Hydrated configuration as dict
    """
    # Convert to JSON (resolves all references)
    json_str = HOCONConverter.to_json(config)
    hydrated = json.loads(json_str)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(hydrated, f, indent=2)

    return hydrated


def get_config(
    path: str,
    environment: str = "local",
    default: Any = None,
) -> Any:
    """Get a specific configuration value by path.

    Args:
        path: Dot-separated config path (e.g., "cybersec.services.postgres.port")
        environment: Environment to load config for
        default: Default value if path not found

    Returns:
        Configuration value or default
    """
    config = load_config(environment)
    try:
        return config.get(path, default)
    except Exception:
        return default


def clear_cache():
    """Clear the configuration cache."""
    _config_cache.clear()
