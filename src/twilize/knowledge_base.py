"""Knowledge base for Tableau dashboard best practices.

Encodes structured best practices from the Tableau Dashboard Best Practices
guide into actionable constants and helper functions. The pipeline consults
this module AFTER chart suggestion but BEFORE chart building to apply
best-practice formatting, styling, and layout decisions.

Key areas:
  - KPI visualization: font sizes, formatting, clean card layout
  - Chart type guidelines: mark settings, sort/label recommendations
  - Dashboard layout: Z-pattern, container hierarchy, sizing
  - Typography: font hierarchy, max 2 font types
  - Color: neutral background, limited palette, colorblind-safe
"""

from __future__ import annotations

from typing import Any


# ── Typography hierarchy (from KB: max 2 font types) ────────────────
FONT_FAMILY = "Tableau Book"

# KB: "titles 18pt, KPIs 24pt bold, annotations 12-14pt"
# Using 22pt for KPIs (within 20-24pt range, fits in zone widths)
FONT_SIZES = {
    "dashboard_title": 20,
    "kpi_value": 22,         # KB: 20-24pt bold
    "kpi_label": 12,         # KB: 12-14pt annotations
    "chart_title": 14,
    "axis_label": 10,
    "filter_label": 12,
    "tooltip": 10,
}

# ── KPI Card best practices ─────────────────────────────────────────
KPI_STYLE = {
    "font_size": 22,
    "font_weight": "bold",
    "font_family": FONT_FAMILY,
    "text_align": "center",
    "label_font_size": 12,
    "label_color": "#666666",
    # Header width: must accommodate formatted values at KPI font size.
    # At 22pt bold, "$1,234,567" ≈ 180px. Give 400px for safety.
    "header_width": 400,
    "header_minwidth": 200,
    # KB: "Remove gridlines and headers for a clean card"
    "hide_gridlines": True,
    "hide_dividers": True,
    "hide_axis_labels": True,
}

# ── Chart type blueprints ────────────────────────────────────────────
# Each entry describes KB-recommended build settings for a chart type.
# The pipeline applies these AFTER selecting chart type and shelves.
CHART_BLUEPRINTS: dict[str, dict[str, Any]] = {
    "Bar": {
        "sort_descending": True,       # KB: "Sort the bars descending"
        "prefer_horizontal": True,     # KB: "Horizontal bar for long labels"
        "add_labels": False,           # Labels via tooltip, not clutter
        "max_categories": 15,
        "mark_settings": {
            "mark_type": "Bar",
        },
    },
    "Line": {
        "continuous_date": True,       # KB: "ensure date is continuous (green)"
        "mark_settings": {
            "mark_type": "Line",
        },
        "hide_row_dividers": True,     # KB: "Hide row dividers for cleaner look"
    },
    "Scatterplot": {
        "mark_settings": {
            "mark_type": "Circle",     # KB: "mark type defaults to circles"
        },
        "min_data_points": 15,
    },
    "Pie": {
        "max_slices": 5,              # KB: "max ~5 values"
        "mark_settings": {
            "mark_type": "Pie",
        },
    },
    "Heatmap": {
        "mark_settings": {
            "mark_type": "Square",     # KB: "set Marks = Square"
        },
    },
    "Map": {
        "mark_settings": {
            "mark_type": "Map",
        },
    },
    "Text": {
        # KB: "Create a worksheet with no rows/columns, just put the measure
        # on Text. Increase font size (20-24pt) and make it bold."
        "font_size": 22,
        "bold": True,
        "hide_gridlines": True,
        "hide_headers": True,
    },
    "Tree Map": {
        "mark_settings": {
            "mark_type": "Automatic",
        },
    },
}

# ── Dashboard layout best practices ─────────────────────────────────
LAYOUT = {
    # KB: "Set the dashboard size to the target display (e.g. 1200x800 px)"
    "sizing_mode": "range",
    "min_width": 1200,
    "min_height": 600,
    "max_width": 1700,
    "max_height": 1000,

    # KB: "Z-pattern. Place high-level KPIs and summary charts at the top"
    "kpi_position": "top",
    "flow_pattern": "z-pattern",

    # KB: "Use neutral background and limited palette"
    "background_color": "#e6e6e6",
    "card_background": "#ffffff",
    "filter_bar_background": "#192f3e",

    # KB: "Use Dashboard containers (horizontal/vertical) to align sheets"
    "use_containers": True,

    # KB: "Employ adequate whitespace between charts"
    "card_margin": 4,
    "outer_margin": 8,
}

# ── Color best practices ────────────────────────────────────────────
COLOR = {
    # KB: "Use a neutral background and a limited palette"
    "background": "#e6e6e6",
    "card_background": "#ffffff",
    # KB: "Reserve bright colors for highlights or alerts"
    "highlight_positive": "#2ca02c",
    "highlight_negative": "#d62728",
    # KB: "Ensure all charts share the same color scheme for consistency"
    "require_consistent_palette": True,
    # KB: "colorblind-friendly (avoid red/green saturation)"
    "accessibility": "colorblind_safe",
}

# ── Number formatting best practices ────────────────────────────────
NUMBER_FORMATS = {
    "currency": "$#,##0",       # Whole dollars for KPI readability
    "percentage": "0.00%",      # 2 decimal places
    "integer": "#,##0",         # Comma-separated
    "decimal": "#,##0.00",      # 2 decimal places max
    "max_decimal_places": 2,    # KB: never show excessive decimals
}


# ── Helper functions ─────────────────────────────────────────────────

def get_kpi_font_size() -> int:
    """Return the KB-recommended KPI font size (22pt, within 20-24pt range)."""
    return KPI_STYLE["font_size"]


def get_chart_blueprint(chart_type: str) -> dict[str, Any]:
    """Return the KB blueprint for a given chart type, or empty dict."""
    return CHART_BLUEPRINTS.get(chart_type, {})


def get_kpi_style_attrs() -> list[tuple[str, str]]:
    """Return (attr, value) pairs for KPI cell style-rule formatting.

    These follow KB recommendations:
    - 22pt bold (within 20-24pt range), centered, Tableau Book font
    """
    return [
        ("text-align", KPI_STYLE["text_align"]),
        ("font-weight", KPI_STYLE["font_weight"]),
        ("font-size", str(KPI_STYLE["font_size"])),
        ("font-family", KPI_STYLE["font_family"]),
    ]


def get_kpi_header_attrs() -> list[tuple[str, str]]:
    """Return (attr, value) pairs for KPI header style-rule.

    Width is sized for 22pt bold formatted values to prevent ####.
    """
    return [
        ("width", str(KPI_STYLE["header_width"])),
        ("minwidth", str(KPI_STYLE["header_minwidth"])),
    ]


def get_kpi_label_attrs() -> list[tuple[str, str]]:
    """Return (attr, value) pairs for KPI label (subtitle) style-rule."""
    return [
        ("text-align", "center"),
        ("font-size", str(KPI_STYLE["label_font_size"])),
        ("font-family", KPI_STYLE["font_family"]),
        ("color", KPI_STYLE["label_color"]),
    ]


def apply_blueprint_to_rules(rules: dict[str, Any]) -> dict[str, Any]:
    """Merge KB best practices into the rules dict.

    This is called once during pipeline initialization to ensure
    KB recommendations are reflected in the rules. YAML user overrides
    still take precedence (they're loaded first).

    Updates:
    - kpi.font_size → 22 (if still at default 28)
    - layout sizing → KB values
    - charts.theme → accessibility
    """
    kpi = rules.get("kpi", {})
    # Only override font_size if it's at the old default (28)
    if kpi.get("font_size", 28) >= 28:
        kpi["font_size"] = KPI_STYLE["font_size"]
        rules["kpi"] = kpi

    return rules


# ── Enhanced KPI (comparison mode) ──────────────────────────────────

# Comparison KPI label_runs color for positive/negative change
KPI_CHANGE_COLOR_POS = "#2ca02c"   # Green for positive change
KPI_CHANGE_COLOR_NEG = "#d62728"   # Red for negative change
KPI_CHANGE_COLOR_NEUTRAL = "#888888"


def kpi_value_formula(measure_name: str, fmt_str: str, agg: str) -> str:
    """Generate a Tableau formula that pre-formats a KPI value as a string.

    This bypasses Tableau's text-format system (which is unreliable for
    programmatic TWB files) by computing the display text directly.

    Args:
        measure_name: Field name (e.g., "Sales").
        fmt_str: Number format string (e.g., "$#,##0", "0.00%").
        agg: Aggregation (e.g., "SUM", "AVG").

    Returns:
        Tableau calculation formula that produces a formatted string.
    """
    agg_expr = f"{agg}([{measure_name}])"

    if "%" in fmt_str:
        # Percentage: show as "15.9%"
        return f"STR(ROUND({agg_expr} * 100, 1)) + '%'"

    prefix = ""
    if "$" in fmt_str:
        prefix = "$"

    # Abbreviate large numbers: K for thousands, M for millions
    return (
        f"IF ABS({agg_expr}) >= 1000000 THEN "
        f"'{prefix}' + STR(ROUND({agg_expr} / 1000000, 1)) + 'M' "
        f"ELSEIF ABS({agg_expr}) >= 1000 THEN "
        f"'{prefix}' + STR(ROUND({agg_expr} / 1000, 1)) + 'K' "
        f"ELSE "
        f"'{prefix}' + STR(ROUND({agg_expr}, 0)) "
        f"END"
    )


def kpi_cy_formula(measure_name: str, date_field: str) -> str:
    """Formula for current-year value of a measure."""
    return (
        f"IF YEAR([{date_field}]) = "
        f"{{FIXED : MAX(YEAR([{date_field}]))}} "
        f"THEN [{measure_name}] END"
    )


def kpi_py_formula(measure_name: str, date_field: str) -> str:
    """Formula for prior-year value of a measure."""
    return (
        f"IF YEAR([{date_field}]) = "
        f"{{FIXED : MAX(YEAR([{date_field}]))}} - 1 "
        f"THEN [{measure_name}] END"
    )


def kpi_change_formula(measure_name: str, agg: str) -> str:
    """Formula for YoY change text with arrow indicator.

    References the CY and PY calculated fields created by
    ``kpi_cy_formula`` and ``kpi_py_formula``.

    Returns a string like "▲ 20.4%" or "▼ 5.2%".
    """
    cy_field = f"_kpi_{measure_name}_cy"
    py_field = f"_kpi_{measure_name}_py"
    cy_sum = f"{agg}([{cy_field}])"
    py_sum = f"{agg}([{py_field}])"

    return (
        f"IF {py_sum} <> 0 AND NOT ISNULL({py_sum}) THEN "
        f"IF {cy_sum} >= {py_sum} THEN "
        f"'▲ ' + STR(ROUND(ABS({cy_sum} - {py_sum}) / ABS({py_sum}) * 100, 1)) + '%' "
        f"ELSE "
        f"'▼ ' + STR(ROUND(ABS({cy_sum} - {py_sum}) / ABS({py_sum}) * 100, 1)) + '%' "
        f"END "
        f"ELSE '' END"
    )


def build_comparison_kpi_label_runs(
    measure_name: str,
    has_temporal: bool,
) -> list[dict]:
    """Build label_runs for an enhanced KPI card.

    With temporal data (comparison mode):
        METRIC NAME          (small, gray, uppercase)
        ▲ 20.4% vs PY       (small, green)
        $733.2K              (large, bold)

    Without temporal data (simple mode):
        METRIC NAME          (small, gray, uppercase)
        $733.2K              (large, bold)
    """
    val_field = f"_kpi_{measure_name}_val"
    display_name = measure_name.upper()

    runs: list[dict] = [
        {
            "text": display_name,
            "fontsize": 10,
            "fontcolor": "#888888",
            "fontname": FONT_FAMILY,
        },
        {"text": "\n"},
    ]

    if has_temporal:
        chg_field = f"_kpi_{measure_name}_chg"
        runs.extend([
            {
                "field": chg_field,
                "fontsize": 10,
                "fontcolor": KPI_CHANGE_COLOR_POS,
                "fontname": FONT_FAMILY,
            },
            {
                "text": " vs PY",
                "fontsize": 10,
                "fontcolor": "#888888",
                "fontname": FONT_FAMILY,
            },
            {"text": "\n"},
        ])

    runs.append({
        "field": val_field,
        "fontsize": KPI_STYLE["font_size"],
        "fontcolor": "#333333",
        "fontname": FONT_FAMILY,
        "bold": True,
    })

    return runs
