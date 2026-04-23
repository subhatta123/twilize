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


# ── Manifest helpers ────────────────────────────────────────────────


def _chart_to_manifest_entry(
    chart: ChartSuggestion,
    worksheet_name: str = "",
) -> dict:
    """Convert a ChartSuggestion into a JSON-safe manifest entry."""
    shelves: dict[str, list[str]] = {}
    for sh in chart.shelves:
        expr = f"{sh.aggregation}({sh.field_name})" if sh.aggregation else sh.field_name
        shelves.setdefault(sh.shelf, []).append(expr)
    entry: dict = {
        "worksheet_name": worksheet_name or chart.title,
        "title": chart.title,
        "chart_type": chart.chart_type,
        "shelves": shelves,
        "priority": chart.priority,
        "required": getattr(chart, "required", False),
        "reason": chart.reason,
    }
    if chart.top_n:
        entry["top_n"] = chart.top_n
    if chart.sort_descending:
        entry["sort_descending"] = chart.sort_descending
    if chart.text_format:
        entry["text_format"] = chart.text_format
    return entry


def _drop_reason(
    chart: ChartSuggestion,
    max_charts: int,
    kept_count: int,
    kind: str,
) -> dict:
    """Build a dropped-suggestion manifest entry."""
    reason_txt = {
        "trim": (
            f"Trimmed by max_charts={max_charts} "
            f"(only {kept_count} slots available; this chart was lower priority)"
        ),
        "map_invalid": (
            "Map chart removed — geographic data is missing or below quality threshold"
        ),
        "dedup": "Dropped as duplicate chart type/signature",
    }.get(kind, kind)
    return {
        "title": chart.title,
        "chart_type": chart.chart_type,
        "priority": chart.priority,
        "reason": reason_txt,
    }


def _theme_manifest(editor: TWBEditor, theme: str, rules: dict) -> dict:
    """Extract the effective theme as a JSON-safe dict."""
    from twilize.dashboard_rules import dashboard_background

    palette_colors: list[str] = []
    palette_name = ""
    for pal in editor.root.findall(".//color-palette"):
        palette_name = pal.get("name", "") or palette_name
        for c in pal.findall("color"):
            if c.text:
                palette_colors.append(c.text)
        if palette_colors:
            break
    bg = dashboard_background(rules)
    card_bg = rules.get("layout", {}).get("card_background", "#ffffff")
    return {
        "name": theme or palette_name or "modern-light",
        "palette_name": palette_name,
        "palette": palette_colors,
        "background_color": bg,
        "card_background_color": card_bg,
    }


def _reference_applied_summary(style: dict) -> dict:
    """Summarise which parts of a reference image were actually applied.

    Mirrors what ``_apply_style_reference_to_workbook`` mutated and flags
    Tableau limitations the agent shouldn't claim were applied (rounded
    corners, drop shadows).  Returned shape:

        {
            "dashboard_background": True/False,
            "card_background":      True/False,
            "card_borders":         True/False,
            "card_margin":          True/False,
            "chart_palette_registered": True/False,
            "chart_mark_rotation":  {worksheet: hex, ...},
            "font_family":          str | None,
            "not_applied":          ["corner_radius_cards", "drop_shadows", ...]
        }
    """
    colors = style.get("colors", {}) or {}
    card_style = style.get("card_style", {}) or {}
    layout_style = style.get("layout_style", {}) or {}
    chart_palette = style.get("chart_palette") or []
    typography = style.get("typography", {}) or {}
    return {
        "dashboard_background": bool(colors.get("background")),
        "card_background": bool(colors.get("card_background")),
        "card_borders": bool(
            card_style.get("border_width") or (style.get("borders") or {}).get("width")
        ),
        "card_margin": layout_style.get("zone_margin") is not None,
        "chart_palette_registered": bool(chart_palette),
        "chart_mark_rotation": style.get("chart_mark_color_assignments", {}),
        "font_family": typography.get("font_family"),
        "not_applied": list(style.get("not_applied") or []),
    }


def _link_global_filters(editor: TWBEditor) -> dict[str, int]:
    """Stamp a shared ``filter-group`` integer on every worksheet filter
    that targets the same column.

    Tableau decides the dashboard filter's *Apply to Worksheets* scope
    by looking at the ``filter-group`` attribute:

        * missing                 → "Only This Worksheet" (isolated)
        * shared int across wsht's → "All Using This Data Source"

    Our builders already emit a ``<filter class=... column=...>`` on every
    worksheet that receives an auto-filter, but without a ``filter-group``
    attribute each one is treated as independent.  Tableau Desktop therefore
    shows "Only This Worksheet" in the dashboard filter menu even though
    the filters are physically present everywhere.

    This pass walks every ``<worksheet>/<filter>`` (and its alias under
    ``<view>/<datasource-dependencies>/<filter>`` for older schemas),
    groups filters by ``column``, and assigns one integer per column —
    shared across every worksheet.  Returns ``{column: filter_group_int}``
    for manifest surfacing.

    Assignment starts at 2 so we don't collide with Tableau's reserved
    group=1 for context filters.
    """
    column_to_group: dict[str, int] = {}
    next_group = 2

    worksheets_el = editor.root.find("worksheets")
    if worksheets_el is None:
        return {}

    # First pass — discover all distinct filter columns across worksheets
    # so we allocate the same integer to every instance.
    for ws in worksheets_el.findall("worksheet"):
        for f in ws.iter("filter"):
            col = f.get("column")
            if not col:
                continue
            if col not in column_to_group:
                column_to_group[col] = next_group
                next_group += 1

    # Only bother stamping filters that are shared by >=2 worksheets —
    # a single-worksheet filter doesn't need a group and stamping it
    # would incorrectly flag an isolated view as "global".
    usage: dict[str, int] = {}
    for ws in worksheets_el.findall("worksheet"):
        seen_in_ws: set[str] = set()
        for f in ws.iter("filter"):
            col = f.get("column")
            if col and col not in seen_in_ws:
                usage[col] = usage.get(col, 0) + 1
                seen_in_ws.add(col)

    shared = {c: g for c, g in column_to_group.items() if usage.get(c, 0) >= 2}

    # Second pass — stamp each qualifying filter with its integer.
    for ws in worksheets_el.findall("worksheet"):
        for f in ws.iter("filter"):
            col = f.get("column")
            if col and col in shared:
                f.set("filter-group", str(shared[col]))

    return shared


def _summarize_filters(
    dashboards_manifest: list[dict],
    global_filter_groups: dict[str, int] | None = None,
) -> dict:
    """Roll up filter info from every dashboard for the MCP manifest.

    Returns a dict the agent can quote verbatim, e.g.::

        {
          "count": 3,
          "scope": "all",              # "all" = global on dashboard; mixed values surface here
          "fields": ["Category", "Region", "Segment"],
          "clickable": True,           # every filter is >=45 px tall
          "min_height_px": 55,
          "per_dashboard": [
            {"dashboard": "...", "count": 3, "scope": "all",
             "fields": [...], "min_height_px": 55},
             ...
          ]
        }

    ``clickable`` is False if ANY filter zone falls below Tableau's
    readability threshold (45 px).  The agent should warn the user in
    that case rather than claiming filters are usable.
    """
    per_dash: list[dict] = []
    all_fields: list[str] = []
    all_scopes: set[str] = set()
    min_heights: list[int] = []

    for d in dashboards_manifest:
        fs = d.get("filters") or []
        if not fs:
            per_dash.append({
                "dashboard": d.get("name", ""), "count": 0,
                "scope": None, "fields": [], "min_height_px": 0,
            })
            continue
        fields = []
        scopes = set()
        heights = []
        for f in fs:
            raw = f.get("field", "") or ""
            # Strip datasource prefix: "[federated.xxx].[none:Ship Mode:nk]"
            short = raw
            if "].[" in raw:
                short = raw.rsplit("].[", 1)[-1].rstrip("]").lstrip("[")
            elif raw.startswith("[") and raw.endswith("]"):
                short = raw[1:-1]
            # Tableau internal caption format: "agg:Column Name:flags"
            # (e.g. "none:Ship Mode:nk" or "sum:Sales:qk") — peel the
            # middle segment so the agent can surface a clean field name.
            if short.count(":") >= 2:
                parts = short.split(":")
                short = parts[1] if len(parts) >= 3 else short
            fields.append(short)
            scopes.add(f.get("scope") or "selected")
            heights.append(int(f.get("height_px_est") or 0))
        min_h = min(heights) if heights else 0
        per_dash.append({
            "dashboard": d.get("name", ""),
            "count": len(fs),
            "scope": ("all" if scopes == {"all"} else (
                "mixed" if len(scopes) > 1 else next(iter(scopes)))),
            "fields": fields,
            "min_height_px": min_h,
        })
        all_fields.extend(fields)
        all_scopes.update(scopes)
        min_heights.append(min_h)

    overall_min = min(min_heights) if min_heights else 0
    total_count = sum(p["count"] for p in per_dash)

    # Decode filter-group keys the same way dashboard scopes are decoded
    # so the agent sees clean field names in the manifest.
    groups_clean: dict[str, int] = {}
    for raw_col, gid in (global_filter_groups or {}).items():
        short = raw_col
        if "].[" in raw_col:
            short = raw_col.rsplit("].[", 1)[-1].rstrip("]").lstrip("[")
        if short.count(":") >= 2:
            parts = short.split(":")
            short = parts[1] if len(parts) >= 3 else short
        groups_clean[short] = gid

    return {
        "count": total_count,
        "scope": ("all" if all_scopes == {"all"} else (
            "mixed" if len(all_scopes) > 1 else (
                next(iter(all_scopes)) if all_scopes else None))),
        "fields": sorted(set(all_fields)),
        "clickable": overall_min >= 45 if total_count else True,
        "min_height_px": overall_min,
        "global_scope_applied": bool(groups_clean),
        "filter_groups": groups_clean,
        "per_dashboard": per_dash,
    }


def _read_worksheet_summaries(editor: TWBEditor) -> list[dict]:
    """Extract a truthful worksheet summary straight from the editor XML."""
    worksheets_el = editor.root.find("worksheets")
    if worksheets_el is None:
        return []
    summaries: list[dict] = []
    for ws in worksheets_el.findall("worksheet"):
        name = ws.get("name") or ""
        mark_el = ws.find(".//mark")
        mark_class = mark_el.get("class", "") if mark_el is not None else ""
        rows_el = ws.find(".//table/rows")
        cols_el = ws.find(".//table/cols")
        rows_txt = (rows_el.text or "").strip() if rows_el is not None else ""
        cols_txt = (cols_el.text or "").strip() if cols_el is not None else ""
        encodings: dict[str, str] = {}
        enc_el = ws.find(".//encodings")
        if enc_el is not None:
            for enc in enc_el:
                field_attr = enc.get("column") or enc.get("field") or ""
                encodings[enc.tag] = field_attr
        summaries.append({
            "name": name,
            "mark_type": mark_class,
            "rows": rows_txt,
            "columns": cols_txt,
            "encodings": encodings,
        })
    return summaries


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
    required_charts: list[dict] | None = None,
    reference_image: str = "",
    return_manifest: bool = True,
) -> dict | str:
    """Build a complete Tableau dashboard from a CSV file.

    Pipeline steps:
        1. Infer CSV schema (types, cardinality)
        2. Classify columns (dimension/measure/temporal/geographic)
        3. Suggest charts (+ inject any user-required charts)
        4. Create Hyper extract from CSV
        5. Create workbook from template
        6. Connect to Hyper extract
        7. Create worksheets and configure charts
        8. Build dashboard with layout
        9. Optionally re-skin from a reference image
       10. Save as .twbx
       11. Self-verify and return a structured manifest

    Args:
        csv_path: Path to source CSV file.
        output_path: Output .twbx path. Defaults to ``<csv_stem>_dashboard.twbx``.
        dashboard_title: Dashboard title. Derived from filename if empty.
        max_charts: Maximum charts to include.
        template_path: TWB template path (empty for default).
        sample_rows: Rows to sample for type inference.
        suggestion: Pre-built dashboard suggestion (skips auto-suggestion).
        required_charts: User-specified chart specs guaranteed to appear.
            Each entry is a dict with keys ``kind`` / ``rows`` / ``columns``
            / ``color`` / ``top_n`` / ``top_by`` / ``sort_descending`` /
            ``title``. See ``chart_suggester.build_required_chart_suggestion``
            for the complete schema.
        reference_image: Optional path to a PNG/JPG image whose palette will
            be applied to the dashboard(s) via ``apply_style_reference``
            after the initial theme is applied.
        return_manifest: When True (default), return a structured dict
            manifest of what was actually built, including dropped
            suggestions and warnings. Set False for legacy string output.

    Returns:
        Structured manifest dict (default) or a human-readable string when
        ``return_manifest=False``.
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

    # User-required charts are ADDITIVE to the auto-suggestion budget.
    # Otherwise a single required chart (e.g. "Top 10 Customers by Profit")
    # would crowd out all auto-generated analytical charts, leaving template
    # slots empty and producing visible dead space on the dashboard canvas.
    if required_charts:
        max_charts = max_charts + len(required_charts)

    # Default output path
    if not output_path:
        output_path = csv_path.parent / f"{csv_path.stem}_dashboard.twbx"
    output_path = Path(output_path)

    # Step 1-2: Schema inference and classification
    logger.info("Inferring CSV schema from %s", csv_path)
    raw_schema = infer_csv_schema(csv_path, sample_rows=sample_rows)
    classified = classify_columns(raw_schema)

    # Step 2b: Apply Knowledge Base best practices to rules.
    # KB recommendations (font sizes, layout, formatting) are merged into
    # rules so they influence all downstream chart building decisions.
    from twilize.knowledge_base import apply_blueprint_to_rules
    rules = apply_blueprint_to_rules(rules)

    # Step 2c: Auto-infer formatting rules from data characteristics.
    # This analyzes actual values (currency symbols, value ranges, decimals)
    # and merges inferred formats on top of YAML defaults — making the system
    # work with ANY dataset, not just sales/retail.
    from twilize.rules_inference import infer_rules_from_schema
    rules = infer_rules_from_schema(classified, rules)

    # Warning + drop tracking for the manifest.
    warnings: list[str] = []
    dropped_suggestions: list[dict] = []

    # Step 3: Chart suggestion (passes rules for KPI formatting)
    if suggestion is None:
        logger.info("Generating chart suggestions")
        # First, build an unconstrained suggestion so we can see what got
        # trimmed vs generated (used for the manifest's dropped list).
        full_suggestion = suggest_charts(
            classified,
            max_charts=999,  # effectively unlimited for diagnostic pre-pass
            rules=rules,
            required_charts=required_charts,
        )
        suggestion = suggest_charts(
            classified,
            max_charts=max_charts,
            rules=rules,
            required_charts=required_charts,
        )
        # Anything in the full list that's not in the trimmed list was
        # dropped by the max_charts budget.
        kept_sigs = {id(c) for c in suggestion.charts}
        # id() won't match across calls; match by (title, chart_type, shelves).
        def _sig(c: ChartSuggestion) -> tuple:
            return (
                c.title,
                c.chart_type,
                tuple(sorted((sh.shelf, sh.field_name, sh.aggregation) for sh in c.shelves)),
            )
        kept_sigs = {_sig(c) for c in suggestion.charts}
        for c in full_suggestion.charts:
            if _sig(c) not in kept_sigs:
                dropped_suggestions.append(_drop_reason(
                    c, max_charts=max_charts,
                    kept_count=len(suggestion.charts), kind="trim",
                ))

    # Validate suggestion (remove invalid maps, dedup, enforce max).
    # Capture the pre-validation set to record map/dedup removals.
    pre_val_sigs = {
        (c.title, c.chart_type) for c in suggestion.charts
    }
    suggestion = validate_suggestion(suggestion, classified, max_charts, rules=rules)
    post_val_sigs = {(c.title, c.chart_type) for c in suggestion.charts}
    for removed in pre_val_sigs - post_val_sigs:
        dropped_suggestions.append({
            "title": removed[0],
            "chart_type": removed[1],
            "priority": 0,
            "reason": "Removed by validation (invalid map, dedup, or trim)",
        })

    # Surface a warning when user-required charts were not all fulfilled.
    if required_charts:
        want = len(required_charts)
        got = sum(1 for c in suggestion.charts if getattr(c, "required", False))
        if got < want:
            warnings.append(
                f"Only {got}/{want} required_charts could be constructed. "
                "Check that field names in rows/columns/color exist in the data."
            )

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

    # Step 6c: Create enhanced KPI calculated fields.
    # KB best practice: KPI cards show pre-formatted values as string calc
    # fields, bypassing Tableau's unreliable text-format for programmatic TWBs.
    # When temporal data is available, also create CY/PY/change fields for
    # year-over-year comparison indicators.
    _prepare_enhanced_kpis(editor, suggestion, classified, rules)

    # Step 7: Create worksheets and configure charts
    worksheet_names = []
    built_charts: list[dict] = []
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
        # Pass through label_runs for enhanced KPI display
        if chart.label_runs:
            chart_kwargs["label_runs"] = chart.label_runs
        entry = _chart_to_manifest_entry(chart, worksheet_name=ws_name)
        try:
            editor.configure_chart(ws_name, **chart_kwargs)
            entry["build_status"] = "ok"
        except Exception as exc:
            # Keep the worksheet in the layout — an empty chart zone is better
            # than a missing one. The worksheet already exists via add_worksheet().
            logger.warning(
                "Failed to configure chart '%s': %s. Keeping worksheet anyway.", ws_name, exc
            )
            entry["build_status"] = "failed"
            entry["build_error"] = str(exc)
            warnings.append(
                f"Chart '{chart.title}' ({chart.chart_type}) could not be "
                f"fully configured: {exc}"
            )
        built_charts.append(entry)

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
    theme_applied = False
    if theme:
        from twilize.style_presets import apply_theme_to_editor

        for db_name in all_dashboard_names:
            try:
                theme_result = apply_theme_to_editor(editor, theme, db_name)
                logger.info("Theme applied to '%s': %s", db_name, theme_result)
                theme_applied = True
            except Exception as exc:
                logger.warning("Theme application failed for '%s': %s", db_name, exc)
                warnings.append(
                    f"Theme '{theme}' could not be applied to '{db_name}': {exc}"
                )

    # Step 8d: Optional reference-image re-skin (runs AFTER the theme so the
    # image colours win). This is the canonical way for an agent to match a
    # user-supplied dashboard screenshot.
    style_reference_applied: dict | None = None
    if reference_image:
        ref_path = Path(reference_image)
        if not ref_path.exists():
            warnings.append(
                f"reference_image '{reference_image}' not found on disk; "
                "styling fell back to theme defaults."
            )
        else:
            try:
                style_reference_applied = editor.apply_style_reference(
                    image_path=str(ref_path),
                )
                logger.info(
                    "Applied style reference from %s: palette=%d colors",
                    ref_path,
                    len((style_reference_applied or {}).get("palette", [])),
                )
            except Exception as exc:
                logger.warning("apply_style_reference failed: %s", exc)
                warnings.append(
                    f"Failed to apply reference_image '{reference_image}': {exc}"
                )

    # Step 9: Link shared filters across worksheets so Tableau shows
    # "Apply to Worksheets > All Using This Data Source" instead of
    # the default "Only This Worksheet" (see _link_global_filters).
    global_filter_groups = _link_global_filters(editor)

    # Step 10: Save as .twbx (bundle the Hyper extract)
    logger.info("Saving workbook to %s", output_path)
    save_msg = editor.save(str(output_path), extra_files=[str(hyper_path)])

    # Step 10: Self-verify — read back straight from the saved editor state.
    worksheet_manifest = _read_worksheet_summaries(editor)
    dashboards_manifest = editor.list_dashboards()
    theme_info = _theme_manifest(editor, theme, rules)
    if style_reference_applied:
        theme_info["reference_image"] = {
            "path": str(Path(reference_image).resolve()),
            "extracted": style_reference_applied,
            "applied": _reference_applied_summary(style_reference_applied),
        }

    required_fulfilled = [
        {
            "title": c["title"],
            "chart_type": c["chart_type"],
            "worksheet_name": c["worksheet_name"],
        }
        for c in built_charts
        if c.get("required") and c.get("build_status") == "ok"
    ]

    filters_manifest = _summarize_filters(dashboards_manifest, global_filter_groups)
    if filters_manifest["count"] and not filters_manifest["clickable"]:
        warnings.append(
            f"Filter zone under 45 px "
            f"(min {filters_manifest['min_height_px']} px) — "
            "filters may render too small to click in Tableau."
        )

    manifest = {
        "status": "ok",
        "output_path": str(output_path),
        "source": {
            "path": str(csv_path),
            "row_count": classified.row_count,
            "dimensions": len(classified.dimensions),
            "measures": len(classified.measures),
        },
        "dashboards": dashboards_manifest,
        "worksheets": worksheet_manifest,
        "charts_built": built_charts,
        "required_charts_fulfilled": required_fulfilled,
        "dropped_suggestions": dropped_suggestions,
        "filters": filters_manifest,
        "theme": theme_info,
        "theme_applied": theme_applied,
        "warnings": warnings,
        "summary": (
            f"Built {len(built_charts)} worksheet(s) across "
            f"{len(dashboards_manifest)} dashboard(s) from "
            f"{classified.row_count} rows. Saved to {output_path}."
        ),
        "save_message": save_msg,
    }

    if not return_manifest:
        db_count = len(all_dashboard_names)
        db_label = f"{db_count} dashboard{'s' if db_count > 1 else ''}"
        return (
            f"Dashboard created: {output_path}\n"
            f"  Source: {csv_path} ({classified.row_count} rows)\n"
            f"  Charts: {len(worksheet_names)}, {db_label}\n"
            f"  Dimensions: {len(classified.dimensions)}, "
            f"Measures: {len(classified.measures)}\n"
            f"  {save_msg}"
        )

    return manifest


def _prepare_enhanced_kpis(
    editor: TWBEditor,
    suggestion: DashboardSuggestion,
    classified: ClassifiedSchema,
    rules: dict,
) -> None:
    """Create calculated fields for enhanced KPI cards.

    KB best practice: KPI values are pre-formatted as string calculated fields
    so they display correctly regardless of Tableau's text-format behavior.
    The formatted string calc is placed directly on the label shelf, replacing
    the raw numeric measure.

    When temporal data is available, also creates CY/PY/change fields for
    year-over-year comparison indicators (like "▲ 20.4% vs PY").
    When no temporal data is available, creates only the value display field.
    """
    from twilize.knowledge_base import (
        kpi_value_formula,
        kpi_cy_formula,
        kpi_py_formula,
        kpi_change_formula,
    )
    from twilize.rules_inference import infer_kpi_number_format, infer_aggregation

    has_temporal = bool(classified.temporal)
    date_field = classified.temporal[0].spec.name if has_temporal else ""

    for chart in suggestion.charts:
        if chart.chart_type != "Text":
            continue

        # Extract the measure name and aggregation from the shelf assignment
        label_shelf = next(
            (sh for sh in chart.shelves if sh.shelf == "label"), None
        )
        if not label_shelf:
            continue

        measure_name = label_shelf.field_name
        agg = label_shelf.aggregation or infer_aggregation(measure_name, rules)
        fmt_str = infer_kpi_number_format(measure_name, agg, rules)

        # --- Build the full KPI display formula as ONE string calc field ---
        # This produces a multi-line string like:
        #   SALES
        #   ▲ 20.4% vs PY
        #   $457.8K
        # Or without temporal data:
        #   SALES
        #   $457.8K
        val_expr = kpi_value_formula(measure_name, fmt_str, agg)
        display_name = measure_name.upper()

        if has_temporal:
            # Create CY and PY helper calc fields first
            cy_name = f"_kpi_{measure_name}_cy"
            py_name = f"_kpi_{measure_name}_py"
            try:
                editor.add_calculated_field(
                    field_name=cy_name,
                    formula=kpi_cy_formula(measure_name, date_field),
                    datatype="real",
                )
                editor.add_calculated_field(
                    field_name=py_name,
                    formula=kpi_py_formula(measure_name, date_field),
                    datatype="real",
                )
            except Exception as exc:
                logger.warning(
                    "Failed to create CY/PY calcs for '%s': %s — "
                    "falling back to simple KPI",
                    measure_name, exc,
                )
                has_temporal = False  # Fall back for this measure

        if has_temporal:
            # Build change expression inline (references CY/PY calc fields)
            chg_expr = kpi_change_formula(measure_name, agg)
            # Full KPI formula: TITLE + optional(CHANGE " vs PY") + VALUE
            # The change line is only shown when PY data exists (chg_expr is non-empty)
            full_formula = (
                f"'{display_name}' + "
                f"IF LEN({chg_expr}) > 0 THEN CHAR(10) + ({chg_expr}) + ' vs PY' ELSE '' END + "
                f"CHAR(10) + ({val_expr})"
            )
        else:
            # Simple KPI: TITLE + newline + VALUE
            full_formula = (
                f"'{display_name}' + CHAR(10) + "
                f"({val_expr})"
            )

        kpi_field_name = f"_kpi_{measure_name}"
        try:
            # Force dimension/nominal: the formula already embeds SUM(...)
            # internally and returns a string for Text placement. Without
            # these overrides, _infer_calculated_field_semantics sees the
            # SUM tokens and marks it role="measure", which makes Tableau
            # apply an outer SUM wrapper to a string — invalid ("!").
            editor.add_calculated_field(
                field_name=kpi_field_name,
                formula=full_formula,
                datatype="string",
                role="dimension",
                field_type="nominal",
            )
            logger.info("Created KPI display calc: %s", kpi_field_name)
        except Exception as exc:
            logger.warning("Failed to create KPI calc '%s': %s", kpi_field_name, exc)
            continue

        # Replace the label shelf with the KPI display calc field.
        # The string calc already contains the formatted value, so no
        # aggregation wrapper is needed (it's already aggregate internally).
        label_shelf.field_name = kpi_field_name
        label_shelf.aggregation = ""  # String calc, not wrapped in SUM/AVG

        # Clear text_format since the value is now pre-formatted
        chart.text_format = None


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
    required_charts: list[dict] | None = None,
    reference_image: str = "",
    return_manifest: bool = True,
) -> dict | str:
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

    # Required charts are additive to the auto-chart budget; see the matching
    # comment in build_dashboard_from_csv.
    if required_charts:
        max_charts = max_charts + len(required_charts)

    # Apply Knowledge Base best practices to rules
    from twilize.knowledge_base import apply_blueprint_to_rules
    rules = apply_blueprint_to_rules(rules)

    # Auto-infer formatting rules from data characteristics
    from twilize.rules_inference import infer_rules_from_schema, infer_kpi_number_format, infer_aggregation
    rules = infer_rules_from_schema(classified, rules)

    # Warning + drop tracking for the manifest.
    warnings: list[str] = []
    dropped_suggestions: list[dict] = []

    # Chart suggestion — run twice so we can diagnose what got trimmed.
    full_suggestion = suggest_charts(
        classified, max_charts=999, rules=rules, required_charts=required_charts,
    )
    suggestion = suggest_charts(
        classified, max_charts=max_charts, rules=rules, required_charts=required_charts,
    )
    def _sig(c: ChartSuggestion) -> tuple:
        return (
            c.title, c.chart_type,
            tuple(sorted((sh.shelf, sh.field_name, sh.aggregation) for sh in c.shelves)),
        )
    kept_sigs = {_sig(c) for c in suggestion.charts}
    for c in full_suggestion.charts:
        if _sig(c) not in kept_sigs:
            dropped_suggestions.append(_drop_reason(
                c, max_charts=max_charts,
                kept_count=len(suggestion.charts), kind="trim",
            ))

    pre_val_sigs = {(c.title, c.chart_type) for c in suggestion.charts}
    suggestion = validate_suggestion(suggestion, classified, max_charts, rules=rules)
    post_val_sigs = {(c.title, c.chart_type) for c in suggestion.charts}
    for removed in pre_val_sigs - post_val_sigs:
        dropped_suggestions.append({
            "title": removed[0],
            "chart_type": removed[1],
            "priority": 0,
            "reason": "Removed by validation (invalid map, dedup, or trim)",
        })

    if required_charts:
        want = len(required_charts)
        got = sum(1 for c in suggestion.charts if getattr(c, "required", False))
        if got < want:
            warnings.append(
                f"Only {got}/{want} required_charts could be constructed. "
                "Check that field names exist in the data."
            )

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

    # Create enhanced KPI calculated fields
    _prepare_enhanced_kpis(editor, suggestion, classified, rules)

    # Create worksheets
    worksheet_names = []
    built_charts: list[dict] = []
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
        if chart.label_runs:
            chart_kwargs["label_runs"] = chart.label_runs
        entry = _chart_to_manifest_entry(chart, worksheet_name=ws_name)
        try:
            editor.configure_chart(ws_name, **chart_kwargs)
            entry["build_status"] = "ok"
        except Exception as exc:
            logger.warning("Failed to configure chart '%s': %s", ws_name, exc)
            entry["build_status"] = "failed"
            entry["build_error"] = str(exc)
            warnings.append(
                f"Chart '{chart.title}' ({chart.chart_type}) could not be "
                f"fully configured: {exc}"
            )
        built_charts.append(entry)

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
    theme_applied = False
    if theme:
        from twilize.style_presets import apply_theme_to_editor
        for db_name in all_dashboard_names:
            try:
                apply_theme_to_editor(editor, theme, db_name)
                theme_applied = True
            except Exception as exc:
                logger.warning("Theme failed for '%s': %s", db_name, exc)
                warnings.append(
                    f"Theme '{theme}' could not be applied to '{db_name}': {exc}"
                )

    # Optional reference-image re-skin.
    style_reference_applied: dict | None = None
    if reference_image:
        ref_path = Path(reference_image)
        if not ref_path.exists():
            warnings.append(
                f"reference_image '{reference_image}' not found on disk; "
                "styling fell back to theme defaults."
            )
        else:
            try:
                style_reference_applied = editor.apply_style_reference(
                    image_path=str(ref_path),
                )
            except Exception as exc:
                logger.warning("apply_style_reference failed: %s", exc)
                warnings.append(
                    f"Failed to apply reference_image '{reference_image}': {exc}"
                )

    # Link shared filters across worksheets so Tableau's "Apply to
    # Worksheets" menu resolves to "All Using This Data Source" — see
    # _link_global_filters.
    global_filter_groups = _link_global_filters(editor)

    # Save
    save_msg = editor.save(str(output_path), extra_files=extra_files or [])

    # Self-verify: inspect the in-memory state that was just written.
    worksheet_manifest = _read_worksheet_summaries(editor)
    dashboards_manifest = editor.list_dashboards()
    theme_info = _theme_manifest(editor, theme, rules)
    if style_reference_applied:
        theme_info["reference_image"] = {
            "path": str(Path(reference_image).resolve()),
            "extracted": style_reference_applied,
            "applied": _reference_applied_summary(style_reference_applied),
        }

    required_fulfilled = [
        {
            "title": c["title"],
            "chart_type": c["chart_type"],
            "worksheet_name": c["worksheet_name"],
        }
        for c in built_charts
        if c.get("required") and c.get("build_status") == "ok"
    ]

    filters_manifest = _summarize_filters(dashboards_manifest, global_filter_groups)
    if filters_manifest["count"] and not filters_manifest["clickable"]:
        warnings.append(
            f"Filter zone under 45 px "
            f"(min {filters_manifest['min_height_px']} px) — "
            "filters may render too small to click in Tableau."
        )

    manifest = {
        "status": "ok",
        "output_path": str(output_path),
        "source": {
            "path": source_label,
            "row_count": classified.row_count,
            "dimensions": len(classified.dimensions),
            "measures": len(classified.measures),
        },
        "dashboards": dashboards_manifest,
        "worksheets": worksheet_manifest,
        "charts_built": built_charts,
        "required_charts_fulfilled": required_fulfilled,
        "dropped_suggestions": dropped_suggestions,
        "filters": filters_manifest,
        "theme": theme_info,
        "theme_applied": theme_applied,
        "warnings": warnings,
        "summary": (
            f"Built {len(built_charts)} worksheet(s) across "
            f"{len(dashboards_manifest)} dashboard(s) from "
            f"{classified.row_count} rows. Saved to {output_path}."
        ),
        "save_message": save_msg,
    }

    if not return_manifest:
        db_count = len(all_dashboard_names)
        db_label = f"{db_count} dashboard{'s' if db_count > 1 else ''}"
        return (
            f"Dashboard created: {output_path}\n"
            f"  Source: {source_label} ({classified.row_count} rows)\n"
            f"  Charts: {len(worksheet_names)}, {db_label}\n"
            f"  Dimensions: {len(classified.dimensions)}, "
            f"Measures: {len(classified.measures)}\n"
            f"  {save_msg}"
        )

    return manifest


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
    required_charts: list[dict] | None = None,
    reference_image: str = "",
    return_manifest: bool = True,
) -> dict | str:
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
        required_charts=required_charts,
        reference_image=reference_image,
        return_manifest=return_manifest,
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
    required_charts: list[dict] | None = None,
    reference_image: str = "",
    return_manifest: bool = True,
) -> dict | str:
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
        required_charts=required_charts,
        reference_image=reference_image,
        return_manifest=return_manifest,
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
    required_charts: list[dict] | None = None,
    reference_image: str = "",
    return_manifest: bool = True,
) -> dict | str:
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
        required_charts=required_charts,
        reference_image=reference_image,
        return_manifest=return_manifest,
    )
