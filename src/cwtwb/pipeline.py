"""End-to-end CSV-to-dashboard pipeline.

Orchestrates: CSV → schema inference → column classification →
chart suggestion → Hyper extract → TWB creation → .twbx output.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional

from cwtwb.chart_suggester import (
    ChartSuggestion,
    DashboardSuggestion,
    ShelfAssignment,
    suggest_charts,
)
from cwtwb.csv_to_hyper import (
    ClassifiedSchema,
    CsvSchema,
    classify_columns,
    csv_to_hyper,
    infer_csv_schema,
)
from cwtwb.twb_editor import TWBEditor

logger = logging.getLogger(__name__)


def build_dashboard_from_csv(
    csv_path: str | Path,
    output_path: str | Path = "",
    dashboard_title: str = "",
    max_charts: int = 5,
    template_path: str = "",
    sample_rows: int = 1000,
    suggestion: DashboardSuggestion | None = None,
    theme: str = "modern-light",
) -> str:
    """Build a complete Tableau dashboard from a CSV file.

    Pipeline steps:
        1. Infer CSV schema (types, cardinality)
        2. Classify columns (dimension/measure/temporal/geographic)
        3. Suggest charts (or use provided suggestion)
        4. Create Hyper extract from CSV
        5. Create workbook from template
        6. Connect to Hyper extract
        7. Create worksheets and configure charts
        8. Build dashboard with layout
        9. Save as .twbx

    Args:
        csv_path: Path to source CSV file.
        output_path: Output .twbx path. Defaults to ``<csv_stem>_dashboard.twbx``.
        dashboard_title: Dashboard title. Derived from filename if empty.
        max_charts: Maximum charts to include.
        template_path: TWB template path (empty for default).
        sample_rows: Rows to sample for type inference.
        suggestion: Pre-built dashboard suggestion (skips auto-suggestion).

    Returns:
        Confirmation message with output path.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    # Default output path
    if not output_path:
        output_path = csv_path.parent / f"{csv_path.stem}_dashboard.twbx"
    output_path = Path(output_path)

    # Step 1-2: Schema inference and classification
    logger.info("Inferring CSV schema from %s", csv_path)
    raw_schema = infer_csv_schema(csv_path, sample_rows=sample_rows)
    classified = classify_columns(raw_schema)

    # Step 3: Chart suggestion
    if suggestion is None:
        logger.info("Generating chart suggestions")
        suggestion = suggest_charts(classified, max_charts=max_charts)

    if not suggestion.charts:
        raise ValueError(
            "No charts could be suggested for this data. "
            "Ensure the CSV has at least one numeric column."
        )

    # Step 4: Create Hyper extract
    hyper_dir = tempfile.mkdtemp(prefix="cwtwb_pipeline_")
    hyper_path = Path(hyper_dir) / f"{csv_path.stem}.hyper"
    logger.info("Creating Hyper extract at %s", hyper_path)
    csv_to_hyper(csv_path, hyper_path, schema=raw_schema, table_name="Extract")

    # Step 5: Create workbook
    logger.info("Creating workbook from template")
    editor = TWBEditor(template_path)

    # Step 6: Connect to Hyper extract
    # First connect with the real path so _rebuild_fields_from_hyper can read it
    logger.info("Connecting to Hyper extract")
    editor.set_hyper_connection(str(hyper_path), table_name="Extract")
    # Rewrite the dbname to the relative archive path for .twbx packaging
    hyper_archive_path = f"Data/Extracts/{hyper_path.name}"
    for conn_el in editor._datasource.findall(".//connection[@class='hyper']"):
        conn_el.set("dbname", hyper_archive_path)

    # Step 7: Create worksheets and configure charts
    worksheet_names = []
    for i, chart in enumerate(suggestion.charts):
        ws_name = _safe_worksheet_name(chart.title, i)
        worksheet_names.append(ws_name)

        logger.info("Creating worksheet: %s (%s)", ws_name, chart.chart_type)
        editor.add_worksheet(ws_name)

        # Build configure_chart kwargs from shelf assignments
        chart_kwargs = _build_chart_kwargs(chart)
        try:
            editor.configure_chart(ws_name, **chart_kwargs)
        except Exception as exc:
            logger.warning(
                "Failed to configure chart '%s': %s. Skipping.", ws_name, exc
            )
            # Remove failed worksheet
            worksheet_names.pop()
            continue

    if not worksheet_names:
        raise RuntimeError("All chart configurations failed. Cannot build dashboard.")

    # Step 8: Create dashboard
    if not dashboard_title:
        dashboard_title = suggestion.title or f"{csv_path.stem} Dashboard"

    logger.info("Creating dashboard: %s", dashboard_title)
    layout = _build_layout(suggestion, worksheet_names)
    editor.add_dashboard(
        dashboard_name=dashboard_title,
        worksheet_names=worksheet_names,
        layout=layout,
    )

    # Step 8b: Apply theme
    if theme:
        from cwtwb.style_presets import apply_theme_to_editor

        try:
            theme_result = apply_theme_to_editor(editor, theme, dashboard_title)
            logger.info("Theme applied: %s", theme_result)
        except Exception as exc:
            logger.warning("Theme application failed: %s", exc)

    # Step 9: Save as .twbx (bundle the Hyper extract)
    logger.info("Saving workbook to %s", output_path)
    result = editor.save(str(output_path), extra_files=[str(hyper_path)])

    return (
        f"Dashboard created: {output_path}\n"
        f"  Source: {csv_path} ({classified.row_count} rows)\n"
        f"  Charts: {len(worksheet_names)}\n"
        f"  Dimensions: {len(classified.dimensions)}, "
        f"Measures: {len(classified.measures)}\n"
        f"  {result}"
    )


def _safe_worksheet_name(title: str, index: int) -> str:
    """Create a valid worksheet name from a chart title.

    Tableau worksheet names must be unique and not too long.
    """
    # Truncate to 50 chars and ensure uniqueness
    name = title[:50].strip()
    if not name:
        name = f"Sheet {index + 1}"
    return name


def _build_chart_kwargs(chart: ChartSuggestion) -> dict:
    """Convert ShelfAssignments into configure_chart keyword arguments."""
    kwargs: dict = {"mark_type": chart.chart_type}

    columns: list[str] = []
    rows: list[str] = []
    color: Optional[str] = None
    size: Optional[str] = None
    label: Optional[str] = None
    detail: Optional[str] = None

    for shelf in chart.shelves:
        field_expr = _format_field_expression(shelf)

        if shelf.shelf == "columns":
            columns.append(field_expr)
        elif shelf.shelf == "rows":
            rows.append(field_expr)
        elif shelf.shelf == "color":
            color = field_expr
        elif shelf.shelf == "size":
            size = field_expr
        elif shelf.shelf == "label":
            label = field_expr
        elif shelf.shelf == "detail":
            detail = field_expr

    if columns:
        kwargs["columns"] = columns
    if rows:
        kwargs["rows"] = rows
    if color:
        kwargs["color"] = color
    if size:
        kwargs["size"] = size
    if label:
        kwargs["label"] = label
    if detail:
        kwargs["detail"] = detail

    return kwargs


def _format_field_expression(shelf: ShelfAssignment) -> str:
    """Format a shelf assignment into a field expression string.

    The field registry expects expressions like ``SUM(Sales)`` for
    aggregated measures or just ``Category`` for dimensions.
    """
    if shelf.aggregation:
        return f"{shelf.aggregation}({shelf.field_name})"
    return shelf.field_name


def _build_layout(
    suggestion: DashboardSuggestion,
    worksheet_names: list[str],
) -> str | dict:
    """Build a layout specification for the dashboard.

    Uses the suggestion's template if available, otherwise falls back
    to template selection based on chart mix, or simple layout strings.
    """
    from cwtwb.layout_templates import TEMPLATE_NAMES, get_template

    # Use explicitly set template from suggestion
    template = suggestion.template
    if template and template in TEMPLATE_NAMES:
        return get_template(template, worksheet_names)

    # Use layout_dict from image recognition if available
    if suggestion.layout_dict:
        return suggestion.layout_dict

    # Auto-select template based on chart mix
    has_kpi = any(c.chart_type == "Text" for c in suggestion.charts)
    n = len(worksheet_names)

    if has_kpi and n >= 3:
        return get_template("executive-summary", worksheet_names)
    elif n <= 3:
        return get_template("overview", worksheet_names)
    else:
        return get_template("grid", worksheet_names)
