"""MCP tools for multi-source dashboard pipelines.

Exposes CSV/Hyper/MySQL/MSSQL inspection, chart suggestion,
Hyper conversion, and end-to-end dashboard generation as MCP tools.
"""

from __future__ import annotations

from twilize.chart_suggester import format_suggestions, suggest_charts
from twilize.csv_to_hyper import (
    classify_columns,
    csv_to_hyper as _csv_to_hyper_impl,
    format_schema_summary,
    infer_csv_schema,
)
from twilize.dashboard_rules import load_rules
from twilize.mcp.app import server
from twilize.mcp.state import get_editor, set_editor
from twilize.mcp.tools_workbook import _snapshot
from twilize.pipeline import (
    build_dashboard_from_csv,
    build_dashboard_from_hyper,
    build_dashboard_from_mysql,
    build_dashboard_from_mssql,
)
from twilize.twb_editor import TWBEditor


def _parse_rules_yaml(rules_yaml: str, csv_path: str = "") -> dict | None:
    """Parse an optional YAML rules string, falling back to file-based rules."""
    if rules_yaml:
        import yaml
        parsed = yaml.safe_load(rules_yaml)
        if isinstance(parsed, dict):
            # Merge user overrides on top of file-based rules
            base = load_rules(csv_path) if csv_path else load_rules()
            from twilize.dashboard_rules import _deep_merge
            return _deep_merge(base, parsed)
    return load_rules(csv_path) if csv_path else None


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
    try:
        schema = infer_csv_schema(csv_path, sample_rows=sample_rows, encoding=encoding)
        classified = classify_columns(schema)
        return format_schema_summary(classified)
    except Exception as exc:
        return f"Error inspecting CSV: {exc}"


@server.tool()
def suggest_charts_for_csv(
    csv_path: str,
    max_charts: int = 0,
    sample_rows: int = 1000,
    rules_yaml: str = "",
) -> str:
    """Suggest chart types and configurations for a CSV file.

    Analyzes the data shape (dimensions, measures, temporal fields, etc.)
    and returns prioritized chart suggestions with shelf assignments.

    Args:
        csv_path: Path to the CSV file.
        max_charts: Maximum number of charts to suggest (0 = use dashboard_rules.yaml default).
        sample_rows: Rows to sample for inference.
        rules_yaml: Optional YAML string with dashboard rules overrides (e.g. KPI formatting, chart limits).

    Returns:
        Formatted suggestion list with chart types, shelf assignments, and reasoning.
    """
    try:
        rules = _parse_rules_yaml(rules_yaml, csv_path)
        schema = infer_csv_schema(csv_path, sample_rows=sample_rows)
        classified = classify_columns(schema)
        suggestion = suggest_charts(classified, max_charts=max_charts, rules=rules)
        return format_suggestions(suggestion)
    except Exception as exc:
        return f"Error suggesting charts: {exc}"


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
    try:
        schema = infer_csv_schema(csv_path, sample_rows=sample_rows)
        return _csv_to_hyper_impl(csv_path, hyper_path, schema=schema, table_name=table_name)
    except Exception as exc:
        return f"Error converting CSV to Hyper: {exc}"


@server.tool()
def csv_to_dashboard(
    csv_path: str,
    output_path: str = "",
    dashboard_title: str = "",
    max_charts: int = 0,
    template_path: str = "",
    theme: str = "",
    rules_yaml: str = "",
) -> str:
    """Build a complete Tableau dashboard from a CSV file (end-to-end).

    Pipeline: CSV → schema inference → chart suggestion → Hyper extract →
    workbook creation → chart configuration → dashboard layout → .twbx output.

    Args:
        csv_path: Path to the source CSV file.
        output_path: Output .twbx path (defaults to <csv_stem>_dashboard.twbx).
        dashboard_title: Dashboard title (derived from filename if empty).
        max_charts: Maximum number of charts (0 = use dashboard_rules.yaml default).
        template_path: TWB template path (empty for default template).
        theme: Theme preset name (empty = use dashboard_rules.yaml default).
            Options: modern-light, modern-dark, classic, minimal, vibrant.
        rules_yaml: Optional YAML string with dashboard rules overrides.
            Example: "kpi:\\n  font_size: 32\\n  max_kpis: 3"

    Returns:
        Summary of the created dashboard with file path.
    """
    try:
        rules = _parse_rules_yaml(rules_yaml, csv_path)
        return build_dashboard_from_csv(
            csv_path=csv_path,
            output_path=output_path,
            dashboard_title=dashboard_title,
            max_charts=max_charts,
            template_path=template_path,
            theme=theme,
            rules=rules,
        )
    except Exception as exc:
        return f"Error building dashboard from CSV: {exc}"


@server.tool()
def inspect_hyper(
    hyper_path: str,
    table_name: str = "",
) -> str:
    """Inspect a Hyper extract file and return its schema with column classification.

    Reads the Hyper file, maps column types, classifies columns as
    dimensions or measures, and returns a summary.

    Requires tableauhyperapi (pip install tableauhyperapi).

    Args:
        hyper_path: Path to the .hyper file.
        table_name: Specific table to inspect (empty = first table).

    Returns:
        Human-readable schema summary with dimensions, measures, and types.
    """
    try:
        from twilize.schema_inference import infer_hyper_schema
        schema = infer_hyper_schema(hyper_path, table_name=table_name)
        classified = classify_columns(schema)
        return format_schema_summary(classified)
    except Exception as exc:
        return f"Error inspecting Hyper: {exc}"


@server.tool()
def hyper_to_dashboard(
    hyper_path: str,
    output_path: str = "",
    dashboard_title: str = "",
    max_charts: int = 0,
    template_path: str = "",
    table_name: str = "",
    theme: str = "",
    rules_yaml: str = "",
) -> str:
    """Build a complete Tableau dashboard from a Hyper extract file (end-to-end).

    Pipeline: Hyper → schema inference → chart suggestion →
    workbook creation → chart configuration → dashboard layout → .twbx output.

    Args:
        hyper_path: Path to the .hyper file.
        output_path: Output .twbx path (defaults to <hyper_stem>_dashboard.twbx).
        dashboard_title: Dashboard title (derived from filename if empty).
        max_charts: Maximum number of charts (0 = use rules default).
        template_path: TWB template path (empty for default template).
        table_name: Table name inside the Hyper file (empty = first table).
        theme: Theme preset name (empty = use rules default).
        rules_yaml: Optional YAML string with dashboard rules overrides.

    Returns:
        Summary of the created dashboard with file path.
    """
    try:
        rules = _parse_rules_yaml(rules_yaml)
        return build_dashboard_from_hyper(
            hyper_path=hyper_path,
            output_path=output_path,
            dashboard_title=dashboard_title,
            max_charts=max_charts,
            template_path=template_path,
            table_name=table_name,
            theme=theme,
            rules=rules,
        )
    except Exception as exc:
        return f"Error building dashboard from Hyper: {exc}"


@server.tool()
def mysql_to_dashboard(
    server_host: str,
    dbname: str,
    table_name: str,
    username: str,
    password: str = "",
    port: int = 3306,
    output_path: str = "",
    dashboard_title: str = "",
    max_charts: int = 0,
    template_path: str = "",
    theme: str = "",
    rules_yaml: str = "",
) -> str:
    """Build a Tableau dashboard from a MySQL table (end-to-end).

    Pipeline: MySQL → schema inference → chart suggestion →
    workbook creation → live MySQL connection → .twb output.

    Requires mysql-connector-python for schema inference.

    Args:
        server_host: MySQL server hostname.
        dbname: Database name.
        table_name: Table to visualize.
        username: Database username.
        password: Database password (used for schema inference only;
            not stored in the workbook).
        port: Server port (default 3306).
        output_path: Output .twb path (defaults to <table>_dashboard.twb).
        dashboard_title: Dashboard title.
        max_charts: Maximum charts (0 = use rules default).
        template_path: TWB template path.
        theme: Theme preset name.
        rules_yaml: Optional YAML string with dashboard rules overrides.

    Returns:
        Summary of the created dashboard with file path.
    """
    try:
        rules = _parse_rules_yaml(rules_yaml)
        return build_dashboard_from_mysql(
            server=server_host,
            dbname=dbname,
            table_name=table_name,
            username=username,
            password=password,
            port=port,
            output_path=output_path,
            dashboard_title=dashboard_title,
            max_charts=max_charts,
            template_path=template_path,
            theme=theme,
            rules=rules,
        )
    except Exception as exc:
        return f"Error building dashboard from MySQL: {exc}"


@server.tool()
def mssql_to_dashboard(
    server_host: str,
    dbname: str,
    table_name: str,
    username: str = "",
    password: str = "",
    port: int = 1433,
    trusted_connection: bool = False,
    output_path: str = "",
    dashboard_title: str = "",
    max_charts: int = 0,
    template_path: str = "",
    theme: str = "",
    rules_yaml: str = "",
) -> str:
    """Build a Tableau dashboard from a Microsoft SQL Server table (end-to-end).

    Pipeline: MSSQL → schema inference → chart suggestion →
    workbook creation → live MSSQL connection → .twb output.

    Requires pyodbc for schema inference and ODBC Driver 17 for SQL Server.

    Args:
        server_host: MSSQL server hostname.
        dbname: Database name.
        table_name: Table to visualize.
        username: Database username (ignored if trusted_connection=True).
        password: Database password (used for schema inference only).
        port: Server port (default 1433).
        trusted_connection: Use Windows Authentication instead of SQL auth.
        output_path: Output .twb path (defaults to <table>_dashboard.twb).
        dashboard_title: Dashboard title.
        max_charts: Maximum charts (0 = use rules default).
        template_path: TWB template path.
        theme: Theme preset name.
        rules_yaml: Optional YAML string with dashboard rules overrides.

    Returns:
        Summary of the created dashboard with file path.
    """
    try:
        rules = _parse_rules_yaml(rules_yaml)
        return build_dashboard_from_mssql(
            server=server_host,
            dbname=dbname,
            table_name=table_name,
            username=username,
            password=password,
            port=port,
            trusted_connection=trusted_connection,
            output_path=output_path,
            dashboard_title=dashboard_title,
            max_charts=max_charts,
            template_path=template_path,
            theme=theme,
            rules=rules,
        )
    except Exception as exc:
        return f"Error building dashboard from MSSQL: {exc}"
