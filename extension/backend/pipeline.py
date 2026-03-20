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

from cwtwb.chart_suggester import DashboardSuggestion
from cwtwb.csv_to_hyper import (
    ColumnSpec,
    CsvSchema,
    classify_columns,
    csv_to_hyper,
)
from cwtwb.pipeline import (
    _safe_worksheet_name,
    _build_chart_kwargs,
    _build_layout,
)
from cwtwb.twb_editor import TWBEditor

from .chart_suggestion import dict_to_suggestion
from .schema_inference import TableauField, _TABLEAU_TYPE_MAP

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
        output_dir = tempfile.mkdtemp(prefix="cwtwb_ext_")

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
    tab_columns = []
    for f in fields:
        cwtwb_type = _TABLEAU_TYPE_MAP.get(f.datatype, "string")
        tab_columns.append(ColumnSpec(
            name=f.name,
            inferred_type=cwtwb_type,
            sample_values=f.sample_values[:5] if f.sample_values else [],
            null_count=0,
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

    # Step 6: Create worksheets and configure charts per plan
    suggestion = dict_to_suggestion(plan)
    worksheet_names = []

    for i, chart in enumerate(suggestion.charts):
        ws_name = _safe_worksheet_name(chart.title, i)
        worksheet_names.append(ws_name)

        logger.info("Creating worksheet: %s (%s)", ws_name, chart.chart_type)
        editor.add_worksheet(ws_name)

        chart_kwargs = _build_chart_kwargs(chart)
        try:
            editor.configure_chart(ws_name, **chart_kwargs)
        except Exception as exc:
            logger.warning(
                "Failed to configure chart '%s': %s. Skipping.", ws_name, exc
            )
            worksheet_names.pop()
            continue

    if not worksheet_names:
        raise RuntimeError("All chart configurations failed. Cannot build dashboard.")

    # Step 7: Create dashboard
    title = plan.get("title", "Extension Dashboard")
    logger.info("Creating dashboard: %s", title)
    layout = _build_layout(suggestion, worksheet_names)
    editor.add_dashboard(
        dashboard_name=title,
        worksheet_names=worksheet_names,
        layout=layout,
    )

    # Step 7b: Apply theme
    theme_name = plan.get("theme", "modern-light")
    theme_colors = plan.get("theme_colors")
    try:
        from cwtwb.style_presets import apply_theme_to_editor

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

    # Step 8: Save as .twbx
    safe_title = re.sub(r'[<>:"/\\|?*]', '', title).replace(' ', '_').strip('_') or "Dashboard"
    output_path = work_dir / f"{safe_title}_{run_id}.twbx"
    logger.info("Saving workbook to %s", output_path)
    editor.save(str(output_path), extra_files=[str(hyper_path)])

    logger.info("Generated workbook: %s", output_path)
    return str(output_path)
