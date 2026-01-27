"""Theme system for Iceberg Browser UI.

Loads theme definitions from themes/*.toml and provides
CSS variables and THREE.js colors for the UI.
"""

import tomllib
from pathlib import Path
from typing import Any

# Themes directory is at project root
THEMES_DIR = Path(__file__).parent.parent.parent / "themes"


def list_themes() -> list[dict[str, str]]:
    """Return list of available themes with metadata.

    Returns:
        List of dicts with 'id', 'name', and 'author' keys.
    """
    themes = []
    for f in THEMES_DIR.glob("*.toml"):
        try:
            with open(f, "rb") as fp:
                data = tomllib.load(fp)
                meta = data.get("meta", {})
                themes.append({
                    "id": f.stem,
                    "name": meta.get("name", f.stem),
                    "author": meta.get("author", ""),
                    "url": meta.get("url", ""),
                })
        except Exception:
            # Skip malformed theme files
            continue
    return sorted(themes, key=lambda t: t["name"])


def get_theme(theme_id: str) -> dict[str, Any]:
    """Load theme by ID, return CSS variables and THREE.js colors.

    Args:
        theme_id: Theme filename without extension (e.g., 'solarized-dark')

    Returns:
        Dict with 'meta', 'variables' (CSS), and 'three' (THREE.js) keys.
    """
    path = THEMES_DIR / f"{theme_id}.toml"
    if not path.exists():
        # Fallback to default theme
        path = THEMES_DIR / "nord.toml"

    with open(path, "rb") as fp:
        data = tomllib.load(fp)

    # Convert colors and effects to CSS variable format
    variables = {}
    for key, value in data.get("colors", {}).items():
        variables[f"--{key}"] = value
    for key, value in data.get("effects", {}).items():
        variables[f"--{key}"] = value

    return {
        "id": theme_id,
        "meta": data.get("meta", {}),
        "variables": variables,
        "three": data.get("three", {}),
    }


def get_default_theme() -> str:
    """Return the default theme ID."""
    return "nord"
