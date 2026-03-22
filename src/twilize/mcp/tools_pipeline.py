"""MCP tools for the CSV-to-dashboard pipeline.

Exposes CSV inspection, chart suggestion, Hyper conversion,
and end-to-end dashboard generation as MCP tools.
"""

from __future__ import annotations

from twilize.chart_suggester import format_suggestions, suggest_charts
from twilize.csv_to_hyper import (
    classify_columns,
    csv_to_hyper as _csv_to_hyper_impl,
    format_schema_summary,
    infer_csv_schema,
)
from twilize.mcp.app import server
from twilize.mcp.state import get_editor, set_editor
from twilize.mcp.tools_workbook import _snapshot
from twilize.pipeline import build_dashboard_from_csv
from twilize.twb_editor import TWBEditor


@server.tool()
def inspect_csv(
    csv_path: str,
    sample_rows: int = 1000,
    encoding: str = "utf-8",
) -> str:
    """Inspect a CSV file and return its inferred schema with column classification.

    Reads the CSV, infers column types (integer, float, date, boolean, string),
    classifies columns as dimensions or measures with semantic types
    (categorical, temporal, geographic, numeric), and returns a summary.

    Args:
        csv_path: Path to the CSV file.
        sample_rows: Number of rows to sample for type inference.
        encoding: File encoding (default utf-8).

    Returns:
        Human-readable schema summary with dimensions, measures, and types.
    """
    schema = infer_csv_schema(csv_path, sample_rows=sample_rows, encoding=encoding)
    classified = classify_columns(schema)
    return format_schema_summary(classified)


@server.tool()
def suggest_charts_for_csv(
    csv_path: str,
    max_charts: int = 6,
    sample_rows: int = 1000,
) -> str:
    """Suggest chart types and configurations for a CSV file.

    Analyzes the data shape (dimensions, measures, temporal fields, etc.)
    and returns prioritized chart suggestions with shelf assignments.

    Args:
        csv_path: Path to the CSV file.
        max_charts: Maximum number of charts to suggest.
        sample_rows: Rows to sample for inference.

    Returns:
        Formatted suggestion list with chart types, shelf assignments, and reasoning.
    """
    schema = infer_csv_schema(csv_path, sample_rows=sample_rows)
    classified = classify_columns(schema)
    suggestion = suggest_charts(classified, max_charts=max_charts)
    return format_suggestions(suggestion)


@server.tool()
def csv_to_hyper(
    csv_path: str,
    hyper_path: str,
    table_name: str = "Extract",
    sample_rows: int = 1000,
) -> str:
    """Convert a CSV file to a Tableau Hyper extract.

    Infers column types and creates a .hyper file that can be used
    as a data source in Tableau workbooks.

    Requires tableauhyperapi (pip install tableauhyperapi).

    Args:
        csv_path: Path to the source CSV file.
        hyper_path: Output path for the .hyper file.
        table_name: Table name inside the Hyper file.
        sample_rows: Rows to sample for type inference.

    Returns:
        Confirmation with row and column counts.
    """
    schema = infer_csv_schema(csv_path, sample_rows=sample_rows)
    return _csv_to_hyper_impl(csv_path, hyper_path, schema=schema, table_name=table_name)


@server.tool()
def csv_to_dashboard(
    csv_path: str,
    output_path: str = "",
    dashboard_title: str = "",
    max_charts: int = 6,
    template_path: str = "",
) -> str:
    """Build a complete Tableau dashboard from a CSV file (end-to-end).

    Pipeline: CSV → schema inference → chart suggestion → Hyper extract →
    workbook creation → chart configuration → dashboard layout → .twbx output.

    Args:
        csv_path: Path to the source CSV file.
        output_path: Output .twbx path (defaults to <csv_stem>_dashboard.twbx).
        dashboard_title: Dashboard title (derived from filename if empty).
        max_charts: Maximum number of charts to include.
        template_path: TWB template path (empty for default template).

    Returns:
        Summary of the created dashboard with file path.
    """
    return build_dashboard_from_csv(
        csv_path=csv_path,
        output_path=output_path,
        dashboard_title=dashboard_title,
        max_charts=max_charts,
        template_path=template_path,
    )
