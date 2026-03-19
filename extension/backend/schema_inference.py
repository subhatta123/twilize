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


def classify_tableau_fields(
    fields: list[TableauField],
    row_count: int = 0,
) -> ClassifiedSchema:
    """Classify Tableau fields into a ClassifiedSchema.

    Uses the same heuristics as csv_to_hyper.classify_columns but
    adapted for fields received from the Extensions API.
    """
    columns = []
    for f in fields:
        cwtwb_type = _TABLEAU_TYPE_MAP.get(f.datatype, "string")
        columns.append(ColumnSpec(
            name=f.name,
            inferred_type=cwtwb_type,
            sample_values=f.sample_values[:5],
            null_count=0,
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
