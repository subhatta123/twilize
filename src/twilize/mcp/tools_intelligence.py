"""Intelligence MCP tools — Brain 1-4 + Admin exposed as agent-callable tools.

These tools give the AI agent access to the four brains:
  1. Rules Engine — get_active_rules(), set_rule(), reset_rules(), export_rules()
  2. Data Profiler — profile_data_source()
  3. Template Decider — recommend_template()
  4. Template Gallery — list_gallery_templates()
"""

from __future__ import annotations

from typing import Optional

from .app import server
from .state import get_editor


# =====================================================================
# BRAIN 1: Rules Engine
# =====================================================================

@server.tool()
def get_active_rules() -> str:
    """Return the active dashboard creation rules.

    Call this at the start of a session to understand what constraints
    are enforced.  The rules engine validates every configure_chart and
    add_dashboard call — violations are returned as errors or warnings.

    Returns:
        Human-readable summary of all active rules.
    """
    from ..rules_engine import get_rules_engine

    engine = get_rules_engine()
    return engine.summarize()


@server.tool()
def set_rule(
    section: str,
    key: str,
    value: str,
) -> str:
    """Set a specific rule value in the active dashboard rules.

    This allows admins to modify rules at runtime without editing YAML files.
    Changes persist for the current session and can be exported with export_rules().

    Args:
        section: Rule section name. Options:
            "kpi" — KPI formatting (font_size, font_color, bold, row_height, max_kpis, default_format)
            "charts" — Chart defaults (max_charts, theme, bar_top_n, pie_max_slices)
            "layout" — Layout settings (width, height, background_color, card_background)
            "bar_chart_rules" — Bar chart enforcement
            "theme_rules" — Theme enforcement
            "map_rules" — Map chart enforcement
        key: The specific setting to change.
            Examples: "font_size", "max_charts", "theme", "background_color", "default_format"
        value: New value (string — will be auto-parsed to int/float/bool as needed).
            Examples: "28", "modern-dark", "#2D2D2D", "true", "$#,##0.00"

    Returns:
        Confirmation of the change or error message.
    """
    from ..rules_engine import get_rules_engine

    engine = get_rules_engine()
    section_data = engine._r(section)
    if not section_data and section not in engine.rules:
        return (
            f"Unknown section '{section}'. Available sections: "
            f"{', '.join(k for k in engine.rules.keys() if k != 'version')}"
        )

    # Auto-parse the value
    parsed = _parse_rule_value(value)

    # Get or create the section in the rules dict
    # Handle alias resolution: find the actual key in the rules dict
    actual_section = section
    if section not in engine.rules:
        _ALIASES = {
            "kpi_rules": "kpi", "chart_count_rules": "charts", "layout_rules": "layout",
            "kpi": "kpi_rules", "charts": "chart_count_rules", "layout": "layout_rules",
        }
        alias = _ALIASES.get(section, "")
        if alias and alias in engine.rules:
            actual_section = alias
        else:
            engine.rules[section] = {}
            actual_section = section

    if actual_section not in engine.rules:
        engine.rules[actual_section] = {}

    old_value = engine.rules[actual_section].get(key, "<not set>")
    engine.rules[actual_section][key] = parsed

    return (
        f"Updated [{actual_section}].{key}: {old_value} → {parsed}\n"
        f"Use export_rules() to save this configuration to a YAML file."
    )


@server.tool()
def reset_rules() -> str:
    """Reset all rules to the built-in defaults.

    Discards any runtime changes made via set_rule() and reloads
    the default rules from the package YAML file.

    Returns:
        Confirmation message.
    """
    from ..rules_engine import reset_rules_engine, get_rules_engine

    reset_rules_engine()
    engine = get_rules_engine()
    return f"Rules reset to defaults. {len(engine.rules)} sections loaded from {engine._path}"


@server.tool()
def export_rules(output_path: str = "") -> str:
    """Export the current active rules to a YAML file.

    Saves the complete rules (including any runtime changes from set_rule())
    to a YAML file that can be placed next to data files or in the
    working directory for future sessions.

    Args:
        output_path: Path to save the YAML file. Defaults to
            ./dashboard_rules.yaml in the current working directory.

    Returns:
        Path to the saved file and summary.
    """
    import yaml
    from pathlib import Path
    from ..rules_engine import get_rules_engine

    engine = get_rules_engine()

    if not output_path:
        output_path = str(Path.cwd() / "dashboard_rules.yaml")

    # Filter out internal keys (prefixed with _)
    export_data = {
        k: v for k, v in engine.rules.items()
        if not k.startswith("_")
    }

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# Twilize Dashboard Rules — exported from active session\n")
        f.write("# Place this file next to your data or in the working directory.\n\n")
        yaml.dump(export_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    section_count = len(export_data)
    return f"Exported {section_count} rule sections to: {output_path}"


def _parse_rule_value(value: str):
    """Auto-parse a string value to the appropriate Python type."""
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lower() in ("null", "none", "~"):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


# =====================================================================
# BRAIN 2: Data Profiler
# =====================================================================

@server.tool()
def profile_data_source(source_type: str = "auto") -> str:
    """Profile the currently connected data source.

    Works for ANY connection type: CSV extract, Hyper, MySQL, Tableau
    Server, Excel — anything that has fields in the workbook.  The
    profile includes dimension/measure classification, semantic types,
    domain hints, and boolean signals that guide chart and template
    selection.

    Args:
        source_type: Override source detection.  Usually "auto" which
                     inspects the workbook fields.  Other options:
                     "csv", "hyper" (requires separate file path tools).

    Returns:
        Human-readable DataProfile with signals for template/chart decisions.
    """
    from ..data_profiler import format_profile, from_workbook_fields

    editor = get_editor()
    profile = from_workbook_fields(editor)
    return format_profile(profile)


@server.tool()
def profile_csv(csv_path: str, sample_rows: int = 1000) -> str:
    """Profile a CSV file before connecting it.

    Unlike profile_data_source (which needs an active workbook),
    this tool profiles a raw CSV file directly.

    Args:
        csv_path: Path to the CSV file.
        sample_rows: Number of rows to sample for type inference.

    Returns:
        Human-readable DataProfile.
    """
    from ..data_profiler import format_profile, from_csv

    profile = from_csv(csv_path, sample_rows=sample_rows)
    return format_profile(profile)


# =====================================================================
# BRAIN 3: Template Decider
# =====================================================================

@server.tool()
def recommend_template(
    chart_types: Optional[str] = None,
    kpi_count: Optional[int] = None,
) -> str:
    """Score all gallery templates and recommend the best fit.

    Call this AFTER profiling the data source and deciding on chart
    types, but BEFORE creating the dashboard layout.  The decider
    evaluates every template in the gallery against the data profile
    and chart mix, returning a ranked list with reasoning.

    Args:
        chart_types: Comma-separated list of chart mark_types being built.
                     Example: "Bar,Line,Text,Text,Map"
                     If omitted, uses only the data profile signals.
        kpi_count: Override KPI count (else derived from chart_types).

    Returns:
        Ranked template recommendations with scores and reasoning.
    """
    from ..data_profiler import from_workbook_fields
    from ..template_decider import format_recommendation, get_decider

    editor = get_editor()
    profile = from_workbook_fields(editor)

    types_list: list[str] = []
    if chart_types:
        types_list = [t.strip() for t in chart_types.split(",") if t.strip()]

    decider = get_decider()
    scores = decider.decide(profile, chart_types=types_list, kpi_count=kpi_count)
    return format_recommendation(scores)


@server.tool()
def recommend_template_for_csv(
    csv_path: str,
    chart_types: Optional[str] = None,
    sample_rows: int = 1000,
) -> str:
    """Recommend templates for a CSV file (no active workbook needed).

    Args:
        csv_path: Path to the CSV file.
        chart_types: Comma-separated chart types (optional).
        sample_rows: Rows to sample for inference.

    Returns:
        Ranked template recommendations.
    """
    from ..data_profiler import from_csv
    from ..template_decider import format_recommendation, get_decider

    profile = from_csv(csv_path, sample_rows=sample_rows)

    types_list: list[str] = []
    if chart_types:
        types_list = [t.strip() for t in chart_types.split(",") if t.strip()]

    decider = get_decider()
    scores = decider.decide(profile, chart_types=types_list)
    return format_recommendation(scores)


# =====================================================================
# BRAIN 4: Template Gallery
# =====================================================================

@server.tool()
def list_gallery_templates() -> str:
    """List all templates in the gallery with suitability rules.

    Templates are user-editable YAML files in ``templates/gallery/``.
    Each template defines layout zones, suitability criteria, recommended
    themes, and auto-wired interaction actions.

    Returns:
        Formatted listing of all gallery templates.
    """
    from ..template_decider import format_gallery_listing, get_gallery

    gallery = get_gallery()
    return format_gallery_listing(gallery)
