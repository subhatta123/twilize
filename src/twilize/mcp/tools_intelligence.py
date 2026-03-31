"""Intelligence MCP tools — Brain 1-4 exposed as agent-callable tools.

These tools give the AI agent access to the four brains:
  1. Rules Engine — get_active_rules()
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
