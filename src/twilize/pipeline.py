"""End-to-end CSV-to-dashboard pipeline.

Orchestrates: CSV → schema inference → column classification →
chart suggestion → Hyper extract → TWB creation → .twbx output.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional

from twilize.chart_suggester import (
    ChartSuggestion,
    DashboardSuggestion,
    ShelfAssignment,
    suggest_charts,
)
from twilize.csv_to_hyper import (
    ClassifiedSchema,
    CsvSchema,
    classify_columns,
    csv_to_hyper,
    infer_csv_schema,
)
from twilize.dashboard_enhancements import (
    auto_add_actions,
    select_auto_filters,
    validate_suggestion,
)
from twilize.twb_editor import TWBEditor

logger = logging.getLogger(__name__)


def build_dashboard_from_csv(
    csv_path: str | Path,
    output_path: str | Path = "",
    dashboard_title: str = "",
    max_charts: int = 0,
    template_path: str = "",
    sample_rows: int = 1000,
    suggestion: DashboardSuggestion | None = None,
    theme: str = "",
    rules: dict | None = None,
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

    # Load YAML dashboard rules (user overrides + built-in defaults)
    if rules is None:
        from twilize.dashboard_rules import load_rules
        rules = load_rules(csv_path)

    # Resolve sentinel defaults from rules
    from twilize.dashboard_rules import max_charts as _rules_max_charts, theme_name as _rules_theme, max_filters as _rules_max_filters
    if max_charts == 0:
        max_charts = _rules_max_charts(rules)
    if not theme:
        theme = _rules_theme(rules)

    # Default output path
    if not output_path:
        output_path = csv_path.parent / f"{csv_path.stem}_dashboard.twbx"
    output_path = Path(output_path)

    # Step 1-2: Schema inference and classification
    logger.info("Inferring CSV schema from %s", csv_path)
    raw_schema = infer_csv_schema(csv_path, sample_rows=sample_rows)
    classified = classify_columns(raw_schema)

    # Step 2b: Auto-infer formatting rules from data characteristics.
    # This analyzes actual values (currency symbols, value ranges, decimals)
    # and merges inferred formats on top of YAML defaults — making the system
    # work with ANY dataset, not just sales/retail.
    from twilize.rules_inference import infer_rules_from_schema
    rules = infer_rules_from_schema(classified, rules)

    # Step 3: Chart suggestion (passes rules for KPI formatting)
    if suggestion is None:
        logger.info("Generating chart suggestions")
        suggestion = suggest_charts(classified, max_charts=max_charts, rules=rules)

    # Validate suggestion (remove invalid maps, dedup, enforce max)
    suggestion = validate_suggestion(suggestion, classified, max_charts, rules=rules)

    if not suggestion.charts:
        raise ValueError(
            "No charts could be suggested for this data. "
            "Ensure the CSV has at least one numeric column."
        )

    # Select auto-filters for interactivity
    auto_filters = select_auto_filters(classified, max_filters=_rules_max_filters(rules))

    # Step 4: Create Hyper extract
    hyper_dir = tempfile.mkdtemp(prefix="twilize_pipeline_")
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

    # Step 6b: Auto-format measure columns using data-inferred + YAML rules.
    # Applies default-format on datasource columns so KPI text marks and
    # axis labels render with the correct number format in Tableau.
    from twilize.rules_inference import infer_kpi_number_format, infer_aggregation
    for col_el in editor._datasource.findall(".//column"):
        col_name = col_el.get("caption") or col_el.get("name", "")
        bare_name = col_name.strip("[]")
        if col_el.get("role") != "measure":
            continue
        if col_el.get("default-format"):
            continue  # already formatted
        if col_el.get("datatype") not in ("real", "integer"):
            continue
        agg = infer_aggregation(bare_name, rules)
        fmt = infer_kpi_number_format(bare_name, agg, rules)
        col_el.set("default-format", fmt)
        logger.info("Auto-format: %s → %s (agg=%s)", bare_name, fmt, agg)

    # Step 7: Create worksheets and configure charts
    worksheet_names = []
    used_names: set[str] = set()
    for i, chart in enumerate(suggestion.charts):
        ws_name = _safe_worksheet_name(chart.title, i, used_names)
        worksheet_names.append(ws_name)

        logger.info("Creating worksheet: %s (%s)", ws_name, chart.chart_type)
        editor.add_worksheet(ws_name)

        # Build configure_chart kwargs from shelf assignments
        chart_kwargs = _build_chart_kwargs(chart)
        if auto_filters:
            chart_kwargs["filters"] = auto_filters
        # Pass through Top N filter, sort, and text_format from suggestion
        if chart.top_n:
            top = chart.top_n
            top_filter = {
                "type": "categorical",
                "field": top["field"],
                "top": top["n"],
                "by": top["by"],
                "direction": "DESC",
            }
            chart_kwargs.setdefault("filters", [])
            chart_kwargs["filters"] = list(chart_kwargs["filters"]) + [top_filter]
        if chart.sort_descending:
            chart_kwargs["sort_descending"] = chart.sort_descending
        if chart.text_format:
            chart_kwargs["text_format"] = chart.text_format
        try:
            editor.configure_chart(ws_name, **chart_kwargs)
        except Exception as exc:
            # Keep the worksheet in the layout — an empty chart zone is better
            # than a missing one. The worksheet already exists via add_worksheet().
            logger.warning(
                "Failed to configure chart '%s': %s. Keeping worksheet anyway.", ws_name, exc
            )

    if not worksheet_names:
        raise RuntimeError("All chart configurations failed. Cannot build dashboard.")

    # Step 8: Create dashboard(s)
    # When there are more analytical charts than a single template can hold,
    # split into multiple dashboards (KPIs are repeated on each).
    if not dashboard_title:
        dashboard_title = suggestion.title or f"{csv_path.stem} Dashboard"

    dashboard_groups = _split_into_dashboards(suggestion, worksheet_names, dashboard_title, auto_filters)

    all_dashboard_names: list[str] = []
    for group in dashboard_groups:
        db_title = group["title"]
        db_ws_names = group["worksheet_names"]
        db_layout = group["layout"]
        # Inject rules and background color into the C3 layout dict
        if isinstance(db_layout, dict) and db_layout.get("_c3_template"):
            db_layout["_rules"] = rules
        if isinstance(db_layout, dict):
            from twilize.dashboard_rules import dashboard_background
            db_layout["_background_color"] = dashboard_background(rules)
        logger.info("Creating dashboard: %s (%d worksheets)", db_title, len(db_ws_names))
        editor.add_dashboard(
            dashboard_name=db_title,
            worksheet_names=db_ws_names,
            layout=db_layout,
        )
        all_dashboard_names.append(db_title)

        # Step 8b: Add cross-sheet filter/highlight actions per dashboard
        try:
            action_results = auto_add_actions(editor, db_title, db_ws_names, classified)
            for msg in action_results:
                logger.info("Action: %s", msg)
        except Exception as exc:
            logger.warning("Auto-actions failed for '%s': %s", db_title, exc)

    # Step 8c: Apply theme to each dashboard
    if theme:
        from twilize.style_presets import apply_theme_to_editor

        for db_name in all_dashboard_names:
            try:
                theme_result = apply_theme_to_editor(editor, theme, db_name)
                logger.info("Theme applied to '%s': %s", db_name, theme_result)
            except Exception as exc:
                logger.warning("Theme application failed for '%s': %s", db_name, exc)

    # Step 9: Save as .twbx (bundle the Hyper extract)
    logger.info("Saving workbook to %s", output_path)
    result = editor.save(str(output_path), extra_files=[str(hyper_path)])

    db_count = len(all_dashboard_names)
    db_label = f"{db_count} dashboard{'s' if db_count > 1 else ''}"
    return (
        f"Dashboard created: {output_path}\n"
        f"  Source: {csv_path} ({classified.row_count} rows)\n"
        f"  Charts: {len(worksheet_names)}, {db_label}\n"
        f"  Dimensions: {len(classified.dimensions)}, "
        f"Measures: {len(classified.measures)}\n"
        f"  {result}"
    )


def _safe_worksheet_name(title: str, index: int, used: set[str] | None = None) -> str:
    """Create a valid worksheet name from a chart title.

    Tableau worksheet names must be unique and not too long.
    Appends a numeric suffix when a collision is detected.
    """
    base = title[:50].strip()
    if not base:
        base = f"Sheet {index + 1}"
    if used is None:
        return base
    name = base
    counter = 2
    while name in used:
        suffix = f" ({counter})"
        name = base[: 50 - len(suffix)] + suffix
        counter += 1
    used.add(name)
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


def _reorder_kpis_first(
    suggestion: DashboardSuggestion,
    worksheet_names: list[str],
) -> list[str]:
    """Reorder worksheet names so KPI (Text) charts come first.

    This ensures templates place KPIs in the compact KPI row and
    analytical charts in the spacious detail area.
    """
    if not suggestion.charts or len(suggestion.charts) != len(worksheet_names):
        return worksheet_names

    # Build a map from worksheet name to chart type
    kpi_names = []
    other_names = []
    for ws_name, chart in zip(worksheet_names, suggestion.charts):
        if chart.chart_type == "Text":
            kpi_names.append(ws_name)
        else:
            other_names.append(ws_name)

    return kpi_names + other_names


def _split_into_dashboards(
    suggestion: DashboardSuggestion,
    worksheet_names: list[str],
    base_title: str,
    filters: list[dict] | None = None,
) -> list[dict]:
    """Split charts into multiple dashboards when they exceed template capacity.

    Each dashboard gets its own KPIs + a subset of analytical charts.
    KPIs are repeated on every dashboard for context.

    Returns a list of dashboard group dicts with keys:
        title, worksheet_names, layout
    """
    from twilize.c3_layout import TEMPLATE_SHEET_CAPACITY

    # Separate KPIs from analytical charts
    kpi_names = []
    chart_names = []
    if suggestion.charts and len(suggestion.charts) == len(worksheet_names):
        for ws_name, chart in zip(worksheet_names, suggestion.charts):
            if chart.chart_type == "Text":
                kpi_names.append(ws_name)
            else:
                chart_names.append(ws_name)
    else:
        chart_names = list(worksheet_names)

    # Determine template capacity based on chart count
    n_kpis = len(kpi_names)
    # For the first dashboard, check how many sheet slots the auto-selected
    # template will provide
    if len(chart_names) <= 3:
        capacity = 3  # C4/C5 templates have 3 sheet slots
    else:
        capacity = 4  # C2/C3 templates have 4 sheet slots

    # If all charts fit in one dashboard, no splitting needed
    if len(chart_names) <= capacity:
        layout = _build_layout(suggestion, worksheet_names, title=base_title, filters=filters)
        return [{
            "title": base_title,
            "worksheet_names": kpi_names + chart_names,
            "layout": layout,
        }]

    # Split charts into groups of `capacity` size
    groups: list[dict] = []
    for i in range(0, len(chart_names), capacity):
        chunk = chart_names[i:i + capacity]
        group_idx = i // capacity
        title = base_title if group_idx == 0 else f"{base_title} ({group_idx + 1})"

        # Build layout for this group
        filter_ws = chunk[0] if chunk else (kpi_names[0] if kpi_names else "")
        layout = {
            "_c3_template": True,
            "_title": title,
            "_kpi_names": kpi_names,
            "_chart_names": chunk,
            "_filters": filters,
            "_filter_worksheet": filter_ws,
            "_rules": None,  # Will be set by caller
        }
        groups.append({
            "title": title,
            "worksheet_names": kpi_names + chunk,
            "layout": layout,
        })

    return groups


def _build_layout(
    suggestion: DashboardSuggestion,
    worksheet_names: list[str],
    title: str = "",
    filters: list[dict] | None = None,
) -> str | dict:
    """Build a layout specification for the dashboard.

    Uses the C3 direct template (bypasses FlexNode for exact Tableau
    layout matching) when KPIs are present. Falls back to named
    templates or simple layout strings for other cases.

    Args:
        suggestion: Dashboard suggestion with chart list and optional template.
        worksheet_names: List of worksheet names to place in the layout.
        title: Optional dashboard title to prepend as a text zone.
        filters: Optional list of auto-filter dicts for quick-filter zones.
    """
    # Reorder worksheet names so KPI (Text) charts come first.
    ordered_names = _reorder_kpis_first(suggestion, worksheet_names)

    # Use layout_dict from image recognition if available
    if suggestion.layout_dict:
        return suggestion.layout_dict

    # Split into KPI and analytical chart names
    kpi_names = []
    chart_names_list = []
    if suggestion.charts and len(suggestion.charts) == len(worksheet_names):
        for ws_name, chart in zip(worksheet_names, suggestion.charts):
            if chart.chart_type == "Text":
                kpi_names.append(ws_name)
            else:
                chart_names_list.append(ws_name)
    else:
        chart_names_list = list(ordered_names)

    # Use C3 direct template for all dashboards (exact Tableau zone XML)
    # Prefer the first chart worksheet for filter binding (more likely to
    # have been configured successfully than the last one).
    filter_ws = chart_names_list[0] if chart_names_list else (
        kpi_names[0] if kpi_names else ""
    )

    return {
        "_c3_template": True,
        "_title": title,
        "_kpi_names": kpi_names,
        "_chart_names": chart_names_list,
        "_filters": filters,
        "_filter_worksheet": filter_ws,
        "_rules": None,  # Will be set by caller
    }


# ── Multi-source pipeline helpers ────────────────────────────────────


def _build_dashboard_from_classified(
    classified: ClassifiedSchema,
    editor: TWBEditor,
    output_path: Path,
    dashboard_title: str = "",
    max_charts: int = 0,
    theme: str = "",
    rules: dict | None = None,
    extra_files: list[str] | None = None,
    source_label: str = "",
) -> str:
    """Shared pipeline logic for all data sources.

    Assumes the editor already has a connection configured and
    the ClassifiedSchema is ready.  Handles: chart suggestion,
    worksheet creation, dashboard layout, theme, and save.
    """
    from twilize.dashboard_rules import (
        load_rules,
        max_charts as _rules_max_charts,
        theme_name as _rules_theme,
        max_filters as _rules_max_filters,
        dashboard_background,
        kpi_number_format,
    )

    if rules is None:
        rules = load_rules()
    if max_charts == 0:
        max_charts = _rules_max_charts(rules)
    if not theme:
        theme = _rules_theme(rules)

    # Auto-infer formatting rules from data characteristics
    from twilize.rules_inference import infer_rules_from_schema, infer_kpi_number_format, infer_aggregation
    rules = infer_rules_from_schema(classified, rules)

    # Chart suggestion
    suggestion = suggest_charts(classified, max_charts=max_charts, rules=rules)
    suggestion = validate_suggestion(suggestion, classified, max_charts, rules=rules)

    if not suggestion.charts:
        raise ValueError(
            "No charts could be suggested for this data. "
            "Ensure the data has at least one numeric column."
        )

    # Auto-filters
    auto_filters = select_auto_filters(classified, max_filters=_rules_max_filters(rules))

    # Auto-format measure columns using data-inferred + YAML rules
    for col_el in editor._datasource.findall(".//column"):
        col_name = col_el.get("caption") or col_el.get("name", "")
        bare_name = col_name.strip("[]")
        if col_el.get("role") != "measure":
            continue
        if col_el.get("default-format"):
            continue
        if col_el.get("datatype") not in ("real", "integer"):
            continue
        agg = infer_aggregation(bare_name, rules)
        fmt = infer_kpi_number_format(bare_name, agg, rules)
        col_el.set("default-format", fmt)
        logger.info("Auto-format: %s → %s (agg=%s)", bare_name, fmt, agg)

    # Create worksheets
    worksheet_names = []
    used_names: set[str] = set()
    for i, chart in enumerate(suggestion.charts):
        ws_name = _safe_worksheet_name(chart.title, i, used_names)
        worksheet_names.append(ws_name)
        editor.add_worksheet(ws_name)

        chart_kwargs = _build_chart_kwargs(chart)
        if auto_filters:
            chart_kwargs["filters"] = auto_filters
        if chart.top_n:
            top = chart.top_n
            top_filter = {
                "type": "categorical",
                "field": top["field"],
                "top": top["n"],
                "by": top["by"],
                "direction": "DESC",
            }
            chart_kwargs.setdefault("filters", [])
            chart_kwargs["filters"] = list(chart_kwargs["filters"]) + [top_filter]
        if chart.sort_descending:
            chart_kwargs["sort_descending"] = chart.sort_descending
        if chart.text_format:
            chart_kwargs["text_format"] = chart.text_format
        try:
            editor.configure_chart(ws_name, **chart_kwargs)
        except Exception as exc:
            logger.warning("Failed to configure chart '%s': %s", ws_name, exc)

    if not worksheet_names:
        raise RuntimeError("All chart configurations failed.")

    # Dashboard title
    if not dashboard_title:
        dashboard_title = suggestion.title or "Dashboard"

    # Create dashboard(s)
    dashboard_groups = _split_into_dashboards(suggestion, worksheet_names, dashboard_title, auto_filters)

    all_dashboard_names: list[str] = []
    for group in dashboard_groups:
        db_title = group["title"]
        db_ws_names = group["worksheet_names"]
        db_layout = group["layout"]
        if isinstance(db_layout, dict) and db_layout.get("_c3_template"):
            db_layout["_rules"] = rules
        if isinstance(db_layout, dict):
            db_layout["_background_color"] = dashboard_background(rules)
        editor.add_dashboard(
            dashboard_name=db_title,
            worksheet_names=db_ws_names,
            layout=db_layout,
        )
        all_dashboard_names.append(db_title)

        try:
            from twilize.dashboard_enhancements import auto_add_actions
            action_results = auto_add_actions(editor, db_title, db_ws_names, classified)
            for msg in action_results:
                logger.info("Action: %s", msg)
        except Exception as exc:
            logger.warning("Auto-actions failed for '%s': %s", db_title, exc)

    # Apply theme
    if theme:
        from twilize.style_presets import apply_theme_to_editor
        for db_name in all_dashboard_names:
            try:
                apply_theme_to_editor(editor, theme, db_name)
            except Exception as exc:
                logger.warning("Theme failed for '%s': %s", db_name, exc)

    # Save
    result = editor.save(str(output_path), extra_files=extra_files or [])

    db_count = len(all_dashboard_names)
    db_label = f"{db_count} dashboard{'s' if db_count > 1 else ''}"
    return (
        f"Dashboard created: {output_path}\n"
        f"  Source: {source_label} ({classified.row_count} rows)\n"
        f"  Charts: {len(worksheet_names)}, {db_label}\n"
        f"  Dimensions: {len(classified.dimensions)}, "
        f"Measures: {len(classified.measures)}\n"
        f"  {result}"
    )


# ── Hyper pipeline ──────────────────────────────────────────────────


def build_dashboard_from_hyper(
    hyper_path: str | Path,
    output_path: str | Path = "",
    dashboard_title: str = "",
    max_charts: int = 0,
    template_path: str = "",
    table_name: str = "",
    theme: str = "",
    rules: dict | None = None,
) -> str:
    """Build a Tableau dashboard from an existing Hyper extract file.

    Pipeline: Hyper → schema inference → chart suggestion →
    workbook creation → chart configuration → dashboard layout → .twbx.

    Args:
        hyper_path: Path to the .hyper file.
        output_path: Output .twbx path.
        dashboard_title: Dashboard title.
        max_charts: Maximum charts (0 = use rules default).
        template_path: TWB template path.
        table_name: Table name inside the Hyper file (empty = first table).
        theme: Theme preset name.
        rules: Dashboard rules dict.

    Returns:
        Summary of the created dashboard.
    """
    from twilize.schema_inference import infer_hyper_schema

    hyper_path = Path(hyper_path)
    if not hyper_path.exists():
        raise FileNotFoundError(f"Hyper file not found: {hyper_path}")

    if rules is None:
        from twilize.dashboard_rules import load_rules
        rules = load_rules()

    if not output_path:
        output_path = hyper_path.parent / f"{hyper_path.stem}_dashboard.twbx"
    output_path = Path(output_path)

    # Schema inference
    raw_schema = infer_hyper_schema(hyper_path, table_name=table_name)
    classified = classify_columns(raw_schema)

    # Create workbook and connect to Hyper
    editor = TWBEditor(template_path)
    editor.set_hyper_connection(str(hyper_path), table_name=table_name or "Extract")

    # Rewrite dbname for .twbx packaging
    hyper_archive_path = f"Data/Extracts/{hyper_path.name}"
    for conn_el in editor._datasource.findall(".//connection[@class='hyper']"):
        conn_el.set("dbname", hyper_archive_path)

    return _build_dashboard_from_classified(
        classified=classified,
        editor=editor,
        output_path=output_path,
        dashboard_title=dashboard_title or f"{hyper_path.stem} Dashboard",
        max_charts=max_charts,
        theme=theme,
        rules=rules,
        extra_files=[str(hyper_path)],
        source_label=str(hyper_path),
    )


# ── MySQL pipeline ──────────────────────────────────────────────────


def build_dashboard_from_mysql(
    server: str,
    dbname: str,
    table_name: str,
    username: str,
    password: str = "",
    port: int = 3306,
    output_path: str | Path = "",
    dashboard_title: str = "",
    max_charts: int = 0,
    template_path: str = "",
    theme: str = "",
    rules: dict | None = None,
) -> str:
    """Build a Tableau dashboard from a MySQL table (live connection).

    Pipeline: MySQL → schema inference → chart suggestion →
    workbook creation → live MySQL connection → .twb output.

    The output is a .twb file (not .twbx) since the data stays in MySQL.

    Args:
        server: MySQL server hostname.
        dbname: Database name.
        table_name: Table to visualize.
        username: Database username.
        password: Database password (used for schema inference only).
        port: Server port.
        output_path: Output .twb path.
        dashboard_title: Dashboard title.
        max_charts: Maximum charts (0 = use rules default).
        template_path: TWB template path.
        theme: Theme preset name.
        rules: Dashboard rules dict.

    Returns:
        Summary of the created dashboard.
    """
    from twilize.schema_inference import infer_mysql_schema

    if rules is None:
        from twilize.dashboard_rules import load_rules
        rules = load_rules()

    if not output_path:
        output_path = Path(f"{table_name}_dashboard.twb")
    output_path = Path(output_path)

    # Schema inference (needs password for DB access)
    raw_schema = infer_mysql_schema(
        server=server, dbname=dbname, table_name=table_name,
        username=username, password=password, port=port,
    )
    classified = classify_columns(raw_schema)

    # Create workbook with live MySQL connection
    editor = TWBEditor(template_path)
    editor.set_mysql_connection(
        server=server, dbname=dbname,
        username=username, table_name=table_name, port=str(port),
    )

    return _build_dashboard_from_classified(
        classified=classified,
        editor=editor,
        output_path=output_path,
        dashboard_title=dashboard_title or f"{table_name} Dashboard",
        max_charts=max_charts,
        theme=theme,
        rules=rules,
        source_label=f"mysql://{server}/{dbname}/{table_name}",
    )


# ── MSSQL pipeline ──────────────────────────────────────────────────


def build_dashboard_from_mssql(
    server: str,
    dbname: str,
    table_name: str,
    username: str = "",
    password: str = "",
    port: int = 1433,
    trusted_connection: bool = False,
    output_path: str | Path = "",
    dashboard_title: str = "",
    max_charts: int = 0,
    template_path: str = "",
    theme: str = "",
    rules: dict | None = None,
) -> str:
    """Build a Tableau dashboard from a Microsoft SQL Server table (live connection).

    Pipeline: MSSQL → schema inference → chart suggestion →
    workbook creation → live MSSQL connection → .twb output.

    The output is a .twb file (not .twbx) since the data stays in MSSQL.

    Args:
        server: MSSQL server hostname.
        dbname: Database name.
        table_name: Table to visualize.
        username: Database username.
        password: Database password (used for schema inference only).
        port: Server port.
        trusted_connection: Use Windows Authentication for schema inference.
        output_path: Output .twb path.
        dashboard_title: Dashboard title.
        max_charts: Maximum charts (0 = use rules default).
        template_path: TWB template path.
        theme: Theme preset name.
        rules: Dashboard rules dict.

    Returns:
        Summary of the created dashboard.
    """
    from twilize.schema_inference import infer_mssql_schema

    if rules is None:
        from twilize.dashboard_rules import load_rules
        rules = load_rules()

    if not output_path:
        output_path = Path(f"{table_name}_dashboard.twb")
    output_path = Path(output_path)

    # Schema inference
    raw_schema = infer_mssql_schema(
        server=server, dbname=dbname, table_name=table_name,
        username=username, password=password, port=port,
        trusted_connection=trusted_connection,
    )
    classified = classify_columns(raw_schema)

    # Create workbook with live MSSQL connection
    editor = TWBEditor(template_path)
    editor.set_mssql_connection(
        server=server, dbname=dbname,
        username=username, table_name=table_name, port=str(port),
    )

    return _build_dashboard_from_classified(
        classified=classified,
        editor=editor,
        output_path=output_path,
        dashboard_title=dashboard_title or f"{table_name} Dashboard",
        max_charts=max_charts,
        theme=theme,
        rules=rules,
        source_label=f"mssql://{server}/{dbname}/{table_name}",
    )
