# Theme System

User-configurable color themes for the Iceberg Browser UI.

## File Format

Themes are defined as TOML files with three sections:

```toml
[meta]
name = "Theme Name"
author = "Author Name"
url = "https://theme-source.com"

[colors]
# CSS variable values (will be prefixed with --)
color-amber = "#hexvalue"
color-void = "#hexvalue"
# ... all color variables

[effects]
# CSS effect values
glow-amber = "none"
shadow-card = "0 2px 8px rgba(0, 0, 0, 0.3)"

[three]
# THREE.js hex integers for FSN visualization
fog = 0x002b36
grid-primary = 0xb58900
block-base = 0x073642
```

## CSS Variables

| Variable | Purpose |
|----------|---------|
| `--color-amber` | Primary accent color |
| `--color-gold` | Secondary accent |
| `--color-bronze` | Tertiary accent |
| `--color-void` | Darkest background |
| `--color-charcoal` | Dark background |
| `--color-containment` | Container background |
| `--color-grid` | Border/grid color |
| `--color-success` | Success status |
| `--color-info` | Info status |
| `--color-warning` | Warning status |
| `--color-error` | Error status |
| `--text-primary` | Main text |
| `--text-secondary` | Secondary text |
| `--text-muted` | Muted/disabled text |
| `--text-terminal` | Code/terminal text |

## THREE.js Colors

| Key | Purpose |
|-----|---------|
| `fog` | Scene fog color |
| `grid-primary` | Grid primary lines |
| `grid-secondary` | Grid secondary lines |
| `ambient-light` | Ambient light color |
| `directional-light` | Main directional light |
| `point-light-1` | First point light |
| `point-light-2` | Second point light |
| `block-base` | Block base color |
| `block-specular` | Block specular highlight |

## Adding New Themes

1. Create `themes/my-theme.toml`
2. Define all required color variables
3. Theme automatically appears in Settings dropdown

## Persistence

User theme selection is saved in `.cybersec/config.toml` and survives `restart:clean`.
