"""Extension pipeline — orchestrate TWBEditor directly for .twbx generation.

Receives data (as rows + field descriptors) from the Tableau extension,
writes a temporary Hyper extract, and builds a .twbx using TWBEditor.
"""

from __future__ import annotations

import csv
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Any

from cwtwb.chart_suggester import DashboardSuggestion
from cwtwb.csv_to_hyper import CsvSchema, infer_csv_schema, csv_to_hyper
from cwtwb.pipeline import build_dashboard_from_csv, _safe_worksheet_name, _build_chart_kwargs, _build_layout
from cwtwb.twb_editor import TWBEditor

from .chart_suggestion import dict_to_suggestion
from .schema_inference import TableauField, classify_tableau_fields

logger = logging.getLogger(__name__)


def generate_workbook(
    fields: list[TableauField],
    data_rows: list[list[Any]],
    plan: dict,
    output_dir: str = "",
) -> str:
    """Generate a .twbx workbook from extension data and plan.

    Steps:
        1. Write data to a temporary CSV
        2. Create Hyper extract from CSV
        3. Create workbook from default template
        4. Connect to Hyper extract
        5. Create worksheets and configure charts per plan
        6. Build dashboard with layout
        7. Save as .twbx

    Args:
        fields: Field descriptors from the extension.
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

    # Step 2-9: Use the main pipeline
    suggestion = dict_to_suggestion(plan)
    title = plan.get("title", "Extension Dashboard")
    output_path = work_dir / f"{title.replace(' ', '_')}_{run_id}.twbx"

    result = build_dashboard_from_csv(
        csv_path=str(csv_path),
        output_path=str(output_path),
        dashboard_title=title,
        suggestion=suggestion,
    )

    logger.info("Generated workbook: %s", output_path)
    return str(output_path)
