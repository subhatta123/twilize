"""Classify fields received from Tableau Extensions API.

Maps Tableau data types to cwtwb types and assigns dimension/measure
roles based on type, cardinality, and name patterns.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cwtwb.csv_to_hyper import (
    _DIMENSION_KEYWORDS,
    _GEO_KEYWORDS,
    _MEASURE_KEYWORDS,
    ClassifiedColumn,
    ClassifiedSchema,
    ColumnSpec,
    CsvSchema,
)


# Tableau Extensions API data types → cwtwb types
_TABLEAU_TYPE_MAP = {
    "int": "integer",
    "float": "float",
    "real": "float",
    "string": "string",
    "bool": "boolean",
    "date": "date",
    "date-time": "date",
    "datetime": "date",
    "spatial": "string",
}


@dataclass
class TableauField:
    """Field info received from the Tableau Extensions API."""

    name: str
    datatype: str  # Tableau type: "int", "float", "string", "date", etc.
    role: str = ""  # "dimension" or "measure" (from Tableau if available)
    cardinality: int = 0
    sample_values: list[str] = field(default_factory=list)
    null_count: int = 0  # Null count computed by frontend from ALL rows


def classify_tableau_fields(
    fields: list[TableauField],
    row_count: int = 0,
    sample_rows: list[list] | None = None,
) -> ClassifiedSchema:
    """Classify Tableau fields into a ClassifiedSchema.

    Uses the same heuristics as csv_to_hyper.classify_columns but
    adapted for fields received from the Extensions API.

    If *sample_rows* are provided, null counts are estimated from the
    sample and extrapolated to the full dataset.
    """
    # Use frontend-provided null_count (computed from ALL rows),
    # with sample-based estimation as fallback
    fallback_nulls = _estimate_null_counts(fields, sample_rows, row_count) if sample_rows else {}

    columns = []
    for i, f in enumerate(fields):
        cwtwb_type = _TABLEAU_TYPE_MAP.get(f.datatype, "string")
        # Prefer frontend null_count (from ALL data); fall back to sample estimate
        nc = f.null_count if f.null_count > 0 else fallback_nulls.get(i, 0)
        columns.append(ColumnSpec(
            name=f.name,
            inferred_type=cwtwb_type,
            sample_values=f.sample_values[:5],
            null_count=nc,
            cardinality=f.cardinality,
            total_rows=row_count,
        ))

    raw_schema = CsvSchema(
        columns=columns,
        row_count=row_count,
        file_path="<tableau-extension>",
    )

    from cwtwb.csv_to_hyper import classify_columns
    return classify_columns(raw_schema)


_NULL_SENTINELS = {"", "null", "none", "na", "n/a", "nan", "%null%", "unknown"}


def _estimate_null_counts(
    fields: list[TableauField],
    sample_rows: list[list] | None,
    row_count: int,
) -> dict[int, int]:
    """Estimate per-field null counts from sample data.

    Checks for Python None, empty strings, and common null sentinels.
    Extrapolates the null ratio from the sample to the full row count.

    Returns:
        dict mapping column index → estimated null count.
    """
    if not sample_rows or not fields:
        return {}

    n_sample = len(sample_rows)
    if n_sample == 0:
        return {}

    result: dict[int, int] = {}
    for col_idx in range(len(fields)):
        null_count = 0
        for row in sample_rows:
            if col_idx >= len(row):
                null_count += 1
                continue
            val = row[col_idx]
            if val is None:
                null_count += 1
            elif isinstance(val, str) and val.strip().lower() in _NULL_SENTINELS:
                null_count += 1

        if null_count > 0:
            # Extrapolate to full dataset
            null_ratio = null_count / n_sample
            estimated = int(null_ratio * row_count) if row_count > 0 else null_count
            result[col_idx] = estimated

    return result
