"""Named theme presets for dashboard styling.

Each preset defines a color palette, background, font, and border
configuration. Use ``apply_theme_to_editor()`` to apply a preset
to a workbook after dashboard creation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ThemePreset:
    """A complete dashboard theme configuration."""

    name: str
    description: str
    palette_colors: list[str]
    background_color: str  # dashboard zone background
    font_family: str  # e.g. "Tableau Book"
    title_font_size: str  # e.g. "14"
    title_color: str  # hex color for title text
    border_color: str  # hex or "" for none
    border_style: str  # "none" or "solid"


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

_PRESETS: dict[str, ThemePreset] = {
    "modern-light": ThemePreset(
        name="modern-light",
        description="Clean white/light gray with Tableau 10 palette",
        palette_colors=[
            "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
            "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC",
        ],
        background_color="#F5F5F5",
        font_family="Tableau Book",
        title_font_size="14",
        title_color="#333333",
        border_color="",
        border_style="none",
    ),
    "corporate-blue": ThemePreset(
        name="corporate-blue",
        description="Professional blue-gray tones with blue palette",
        palette_colors=[
            "#1F77B4", "#4A90D9", "#7FB3E0", "#A6CEE3", "#2C5F8A",
            "#3E7CB1", "#5A9BD4", "#8CB4D9", "#B8D4E8", "#1A4A6E",
        ],
        background_color="#E8EEF2",
        font_family="Arial",
        title_font_size="14",
        title_color="#1A3A5C",
        border_color="#C8D6E0",
        border_style="solid",
    ),
    "dark": ThemePreset(
        name="dark",
        description="Dark background with bright accent colors",
        palette_colors=[
            "#FF6F61", "#6EC4DB", "#F7DC6F", "#82E0AA", "#C39BD3",
            "#F0B27A", "#85C1E9", "#F1948A", "#73C6B6", "#D7BDE2",
        ],
        background_color="#2D2D2D",
        font_family="Tableau Book",
        title_font_size="14",
        title_color="#EEEEEE",
        border_color="",
        border_style="none",
    ),
    "minimal": ThemePreset(
        name="minimal",
        description="Pure white with muted greens and thin borders",
        palette_colors=[
            "#7CB342", "#AED581", "#C5E1A5", "#558B2F", "#33691E",
            "#9E9D24", "#C0CA33", "#D4E157", "#827717", "#F9A825",
        ],
        background_color="#FFFFFF",
        font_family="Tableau Light",
        title_font_size="12",
        title_color="#555555",
        border_color="#E0E0E0",
        border_style="solid",
    ),
    "vibrant": ThemePreset(
        name="vibrant",
        description="White background with Tableau 20 and bold titles",
        palette_colors=[
            "#4E79A7", "#A0CBE8", "#F28E2B", "#FFBE7D", "#59A14F",
            "#8CD17D", "#B6992D", "#F1CE63", "#499894", "#86BCB6",
            "#E15759", "#FF9D9A", "#79706E", "#BAB0AC", "#D37295",
            "#FABFD2", "#B07AA1", "#D4A6C8", "#9D7660", "#D7B5A6",
        ],
        background_color="#FFFFFF",
        font_family="Tableau Bold",
        title_font_size="16",
        title_color="#333333",
        border_color="#4E79A7",
        border_style="solid",
    ),
}

# Aliases map common alternative names to canonical presets.
_ALIASES: dict[str, str] = {
    "modern-dark": "dark",
    "classic": "corporate-blue",
}

THEME_NAMES: list[str] = list(_PRESETS.keys()) + list(_ALIASES.keys())


def get_theme(name: str) -> ThemePreset:
    """Get a theme preset by name (supports aliases like ``modern-dark``).

    Raises:
        ValueError: If the theme name is not recognized.
    """
    # Resolve alias first
    canonical = _ALIASES.get(name, name)
    preset = _PRESETS.get(canonical)
    if preset is None:
        raise ValueError(
            f"Unknown theme '{name}'. Available: {', '.join(THEME_NAMES)}"
        )
    return preset


def list_themes() -> list[dict[str, str]]:
    """List available themes with descriptions."""
    return [
        {"name": p.name, "description": p.description}
        for p in _PRESETS.values()
    ]


def apply_theme_to_editor(
    editor,
    theme_name: str,
    dashboard_name: str,
    *,
    custom_colors: list[str] | None = None,
) -> str:
    """Apply a named theme to a workbook and dashboard.

    Args:
        editor: TWBEditor instance.
        theme_name: Name of the preset (or "custom" with custom_colors).
        dashboard_name: Dashboard to style.
        custom_colors: Optional custom palette colors (overrides preset).

    Returns:
        Summary of what was applied.
    """
    if theme_name == "custom" and custom_colors:
        # Create an ad-hoc preset from image-extracted colors
        preset = ThemePreset(
            name="custom",
            description="Custom theme from image",
            palette_colors=custom_colors,
            background_color="#FFFFFF",
            font_family="Tableau Book",
            title_font_size="14",
            title_color="#333333",
            border_color="",
            border_style="none",
        )
    else:
        preset = get_theme(theme_name)

    applied: list[str] = []

    # Apply color palette
    try:
        editor.apply_color_palette(colors=preset.palette_colors, custom_name=preset.name)
        applied.append(f"palette ({preset.name})")
    except Exception as exc:
        applied.append(f"palette skipped: {exc}")

    # Apply dashboard theme (background, font, etc.)
    try:
        count = editor.apply_dashboard_theme(
            dashboard_name=dashboard_name,
            background_color=preset.background_color,
            font_family=preset.font_family,
            title_font_size=preset.title_font_size,
        )
        applied.append(f"theme on {count} zones")
    except Exception as exc:
        applied.append(f"theme skipped: {exc}")

    return f"Applied '{preset.name}': {', '.join(applied)}"
