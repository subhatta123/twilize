"""Extension pipeline — orchestrate TWBEditor directly for .twbx generation.

Receives data (as rows + field descriptors) from the Tableau extension,
writes a temporary Hyper extract, and builds a .twbx using TWBEditor.
"""

from __future__ import annotations

import csv
import logging
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from lxml import etree

from twilize.chart_suggester import DashboardSuggestion
from twilize.csv_to_hyper import (
    ColumnSpec,
    CsvSchema,
    classify_columns,
    csv_to_hyper,
)
from twilize.dashboard_enhancements import (
    auto_add_actions,
    select_auto_filters,
    validate_suggestion,
)
from twilize.pipeline import (
    _safe_worksheet_name,
    _build_chart_kwargs,
    _build_layout,
)
from twilize.twb_editor import TWBEditor

from .chart_suggestion import dict_to_suggestion
from .schema_inference import TableauField, _TABLEAU_TYPE_MAP, _estimate_null_counts

logger = logging.getLogger(__name__)


def generate_workbook(
    fields: list[TableauField],
    data_rows: list[list[Any]],
    plan: dict,
    output_dir: str = "",
) -> str:
    """Generate a .twbx workbook from extension data and plan.

    Instead of re-inferring types from CSV text (lossy), we use the
    accurate type information that Tableau already provided via the
    Extensions API.

    Args:
        fields: Field descriptors from the extension (with accurate types).
        data_rows: Raw data rows (list of lists).
        plan: Dashboard plan dict (from suggest_dashboard).
        output_dir: Directory for output .twbx. Defaults to temp dir.

    Returns:
        Path to the generated .twbx file.
    """
    if not output_dir:
        output_dir = tempfile.mkdtemp(prefix="twilize_ext_")

    work_dir = Path(output_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    run_id = uuid.uuid4().hex[:8]

    # Step 1: Write data to CSV
    csv_path = work_dir / f"data_{run_id}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([field.name for field in fields])
        writer.writerows(data_rows)

    logger.info("Wrote %d rows to %s", len(data_rows), csv_path)

    # Step 2: Build schema from Tableau's type metadata (NOT re-inferred from CSV)
    # This preserves the accurate types Tableau already knows about
    row_count = len(data_rows)
    # Estimate null counts from actual data for geo quality validation
    null_counts = _estimate_null_counts(fields, data_rows[:100], row_count)
    tab_columns = []
    for i, f in enumerate(fields):
        twilize_type = _TABLEAU_TYPE_MAP.get(f.datatype, "string")
        tab_columns.append(ColumnSpec(
            name=f.name,
            inferred_type=twilize_type,
            sample_values=f.sample_values[:5] if f.sample_values else [],
            null_count=f.null_count if f.null_count > 0 else null_counts.get(i, 0),
            cardinality=f.cardinality,
            total_rows=row_count,
        ))

    schema = CsvSchema(
        columns=tab_columns,
        row_count=row_count,
        file_path=str(csv_path),
    )
    classified = classify_columns(schema)

    # Step 3: Create Hyper extract using the Tableau-provided schema
    hyper_path = work_dir / f"data_{run_id}.hyper"
    logger.info("Creating Hyper extract at %s", hyper_path)
    csv_to_hyper(csv_path, hyper_path, schema=schema, table_name="Extract")

    # Step 4: Create workbook from template
    logger.info("Creating workbook from template")
    editor = TWBEditor("")

    # Step 5: Connect to Hyper extract
    logger.info("Connecting to Hyper extract")
    editor.set_hyper_connection(str(hyper_path), table_name="Extract")
    # Rewrite dbname to relative archive path for .twbx packaging
    hyper_archive_path = f"Data/Extracts/{hyper_path.name}"
    for conn_el in editor._datasource.findall(".//connection[@class='hyper']"):
        conn_el.set("dbname", hyper_archive_path)

    # Step 5b: Create calculated fields if the plan includes them
    calc_fields = plan.get("calculated_fields", [])
    for cf in calc_fields:
        cf_name = cf.get("name", "")
        cf_formula = cf.get("formula", "")
        if cf_name and cf_formula:
            try:
                editor.add_calculated_field(
                    cf_name, cf_formula,
                    default_format=cf.get("format", ""),
                )
                logger.info("Created calculated field: %s = %s", cf_name, cf_formula)
            except Exception as exc:
                logger.warning("Failed to create calculated field '%s': %s", cf_name, exc)

    # Step 6: Create worksheets and configure charts per plan
    suggestion = dict_to_suggestion(plan)

    # Validate suggestion (remove invalid maps, dedup, enforce max)
    suggestion = validate_suggestion(suggestion, classified, max_charts=8)

    # Select auto-filters for interactivity
    auto_filters = select_auto_filters(classified, max_filters=5)

    # Build a field name mapping: bare name -> YEAR(name) for temporal fields
    # so that LLM/rule-based references like "Order Date" resolve to "YEAR(Order Date)"
    known_fields = {f.name for f in fields}
    temporal_map: dict[str, str] = {}
    for fn in known_fields:
        if fn.startswith("YEAR(") and fn.endswith(")"):
            bare = fn[5:-1]  # "YEAR(Order Date)" -> "Order Date"
            if bare not in known_fields:
                temporal_map[bare] = fn

    def _resolve_field(expr: str) -> str:
        """Resolve a field expression to its actual name in the registry.

        Handles both bare names ('Order Date' -> 'YEAR(Order Date)')
        and aggregation-wrapped names ('SUM(Order Date)' -> 'SUM(YEAR(Order Date))').
        """
        if expr in known_fields:
            return expr
        if expr in temporal_map:
            return temporal_map[expr]
        # Check inside aggregation wrapper: SUM(Order Date) -> SUM(YEAR(Order Date))
        import re
        m = re.match(r'^(\w+)\((.+)\)$', expr)
        if m:
            agg, inner = m.group(1), m.group(2)
            if inner in temporal_map:
                return f"{agg}({temporal_map[inner]})"
        return expr

    # Also resolve field names in suggestion shelves BEFORE building kwargs
    if temporal_map:
        print(f"[PIPELINE] Temporal field map: {temporal_map}")
    for chart in suggestion.charts:
        for shelf in chart.shelves:
            if shelf.field_name in temporal_map:
                old = shelf.field_name
                shelf.field_name = temporal_map[shelf.field_name]
                print(f"[PIPELINE] Resolved temporal: '{old}' -> '{shelf.field_name}'")
            # Also check if field_name contains a bare temporal name
            # e.g. LLM might use "YEAR(Order Date)" which won't match
            elif shelf.field_name not in known_fields:
                print(f"[PIPELINE] WARNING: Field '{shelf.field_name}' not in known_fields")

    worksheet_names = []
    configured_ok: set[str] = set()
    failed_indices: list[int] = []

    for i, chart in enumerate(suggestion.charts):
        ws_name = _safe_worksheet_name(chart.title, i)
        worksheet_names.append(ws_name)

        logger.info("Creating worksheet: %s (%s)", ws_name, chart.chart_type)
        print(f"[PIPELINE] Creating worksheet {i+1}/{len(suggestion.charts)}: '{ws_name}' ({chart.chart_type})")
        editor.add_worksheet(ws_name)

        chart_kwargs = _build_chart_kwargs(chart)

        # Map chart type: ensure geo field is on "detail" and measure on "color"
        if chart.chart_type == "Map":
            # Find geo dimension and measure among shelves
            geo_field = None
            measure_field = None
            measure_agg = None
            for shelf in chart.shelves:
                if shelf.shelf == "detail" or (not shelf.aggregation and shelf.shelf in ("rows", "columns")):
                    geo_field = shelf.field_name
                if shelf.aggregation and shelf.shelf in ("color", "rows", "columns", "size"):
                    measure_field = shelf.field_name
                    measure_agg = shelf.aggregation
            if geo_field and measure_field:
                chart_kwargs = {
                    "mark_type": "Map",
                    "detail": geo_field,
                    "color": f"{measure_agg}({measure_field})" if measure_agg else measure_field,
                }

        # Resolve field names in shelves (e.g. "Order Date" -> "YEAR(Order Date)")
        for shelf_key in ("rows", "columns", "color", "size", "label", "detail"):
            if shelf_key in chart_kwargs:
                val = chart_kwargs[shelf_key]
                if isinstance(val, list):
                    chart_kwargs[shelf_key] = [_resolve_field(v) if isinstance(v, str) else v for v in val]
                elif isinstance(val, str):
                    chart_kwargs[shelf_key] = _resolve_field(val)

        if auto_filters:
            chart_kwargs["filters"] = auto_filters
        print(f"[PIPELINE]   kwargs: {chart_kwargs}")
        try:
            editor.configure_chart(ws_name, **chart_kwargs)
            configured_ok.add(ws_name)
            print(f"[PIPELINE]   OK: Configured '{ws_name}' successfully")
        except Exception as exc:
            logger.warning(
                "Failed to configure chart '%s': %s. Will try fallback.", ws_name, exc
            )
            print(f"[PIPELINE]   FAIL: configure_chart failed for '{ws_name}': {exc}")
            failed_indices.append(i)

    # Step 6b: Replace failed charts with fallback alternatives
    if failed_indices and classified:
        from twilize.chart_suggester import suggest_charts as suggest_fallback
        fallback_suggestion = suggest_fallback(classified, max_charts=8)
        # Filter to non-KPI, non-duplicate charts; deprioritize Map
        existing_types = {(c.chart_type, frozenset(s.field_name for s in c.shelves))
                         for c in suggestion.charts if c.chart_type != "Text"}
        fallback_charts = [
            c for c in fallback_suggestion.charts
            if c.chart_type != "Text"
            and (c.chart_type, frozenset(s.field_name for s in c.shelves)) not in existing_types
        ]
        # Avoid duplicate chart types — prefer types not already used
        existing_chart_types = {c.chart_type for c in suggestion.charts if c.chart_type != "Text"}
        # Sort: unused types first, then Bar > Pie > Line > Scatter; Map last
        priority_order = {"Bar": 0, "Pie": 1, "Line": 2, "Scatterplot": 3,
                          "Heatmap": 4, "Tree Map": 5, "Map": 99}
        fallback_charts.sort(key=lambda c: (
            0 if c.chart_type not in existing_chart_types else 50,  # Prefer new types
            priority_order.get(c.chart_type, 10),
        ))

        for fi in failed_indices:
            if not fallback_charts:
                break
            alt = fallback_charts.pop(0)
            ws_name = worksheet_names[fi]
            # Resolve temporal fields in fallback too
            for shelf in alt.shelves:
                if shelf.field_name in temporal_map:
                    shelf.field_name = temporal_map[shelf.field_name]

            alt_kwargs = _build_chart_kwargs(alt)
            for shelf_key in ("rows", "columns", "color", "size", "label", "detail"):
                if shelf_key in alt_kwargs:
                    val = alt_kwargs[shelf_key]
                    if isinstance(val, list):
                        alt_kwargs[shelf_key] = [_resolve_field(v) if isinstance(v, str) else v for v in val]
                    elif isinstance(val, str):
                        alt_kwargs[shelf_key] = _resolve_field(val)
            if auto_filters:
                alt_kwargs["filters"] = auto_filters
            try:
                editor.configure_chart(ws_name, **alt_kwargs)
                configured_ok.add(ws_name)
                print(f"[PIPELINE]   FALLBACK OK: Replaced '{ws_name}' with {alt.chart_type} chart")
            except Exception as exc2:
                logger.warning("Fallback also failed for '%s': %s", ws_name, exc2)
                print(f"[PIPELINE]   FALLBACK FAIL: '{ws_name}': {exc2}")

    if not worksheet_names:
        raise RuntimeError("All chart configurations failed. Cannot build dashboard.")

    # Step 7: Create dashboard
    title = plan.get("title", "Extension Dashboard")
    print(f"[PIPELINE] Building dashboard '{title}' with {len(worksheet_names)} worksheets: {worksheet_names}")
    logger.info("Creating dashboard: %s with %d worksheets", title, len(worksheet_names))
    layout = _build_layout(suggestion, worksheet_names, title=title, filters=auto_filters)

    # Ensure the filter worksheet was successfully configured; fall back to
    # the first worksheet that actually has filters/shelves set up.
    if isinstance(layout, dict) and layout.get("_c3_template"):
        cur_fw = layout.get("_filter_worksheet", "")
        if cur_fw and cur_fw not in configured_ok:
            # Pick the first successfully configured chart worksheet instead
            chart_names_in_layout = layout.get("_chart_names", [])
            fallback = next(
                (n for n in chart_names_in_layout if n in configured_ok),
                next((n for n in layout.get("_kpi_names", []) if n in configured_ok), cur_fw),
            )
            logger.info(
                "Filter worksheet '%s' was not configured; falling back to '%s'",
                cur_fw, fallback,
            )
            layout["_filter_worksheet"] = fallback

    # Log which worksheets are in the layout
    if isinstance(layout, dict) and layout.get("_c3_template"):
        layout_ws = layout.get("_kpi_names", []) + layout.get("_chart_names", [])
        print(f"[PIPELINE] C3 template: {len(layout_ws)} worksheets: {layout_ws}")
    else:
        from twilize.dashboard_layouts import extract_layout_worksheets
        layout_ws = extract_layout_worksheets(layout) if isinstance(layout, dict) else []
        print(f"[PIPELINE] Layout contains {len(layout_ws)} worksheets: {layout_ws}")

    editor.add_dashboard(
        dashboard_name=title,
        worksheet_names=worksheet_names,
        layout=layout,
    )

    # Set "Entire View" fit for all worksheets in the dashboard window
    for win in editor.root.findall(".//windows/window"):
        if win.get("name") == title or win.get("class") == "dashboard":
            vps = win.find("viewpoints")
            if vps is not None:
                for vp in vps.findall("viewpoint"):
                    if vp.find("zoom") is None:
                        zoom = etree.SubElement(vp, "zoom")
                        zoom.set("type", "entire-view")
            break

    # Step 7b: Add cross-sheet filter/highlight actions
    try:
        action_results = auto_add_actions(editor, title, worksheet_names, classified)
        for msg in action_results:
            logger.info("Action: %s", msg)
    except Exception as exc:
        logger.warning("Auto-actions failed: %s", exc)

    # Step 7c: Apply theme — skip for C3 template (it has its own styling)
    is_c3 = isinstance(layout, dict) and layout.get("_c3_template")
    if not is_c3:
        theme_name = plan.get("theme", "modern-light")
        theme_colors = plan.get("theme_colors")
        try:
            from twilize.style_presets import apply_theme_to_editor

            if theme_colors:
                result = apply_theme_to_editor(
                    editor, "custom", title, custom_colors=theme_colors
                )
            elif theme_name:
                result = apply_theme_to_editor(editor, theme_name, title)
            else:
                result = apply_theme_to_editor(editor, "modern-light", title)
            logger.info("Theme applied: %s", result)
        except Exception as exc:
            logger.warning("Theme application failed: %s", exc)
    else:
        logger.info("Skipping theme — C3 template has built-in styling")

    # Step 8: Save as .twbx
    safe_title = re.sub(r'[<>:"/\\|?*]', '', title).replace(' ', '_').strip('_') or "Dashboard"
    output_path = work_dir / f"{safe_title}_{run_id}.twbx"
    logger.info("Saving workbook to %s", output_path)
    editor.save(str(output_path), extra_files=[str(hyper_path)])

    logger.info("Generated workbook: %s", output_path)
    return str(output_path)
