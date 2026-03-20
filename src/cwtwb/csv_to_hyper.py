"""CSV schema inference, column classification, and Hyper extract creation.

This module provides the data ingestion layer for the CSV-to-dashboard
pipeline. It reads CSV files, infers column types, classifies columns
as dimensions or measures, and (optionally) creates Tableau Hyper
extract files for embedding in .twbx packages.
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Common date formats to try during type inference
_DATE_FORMATS = [
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%Y/%m/%d",
    "%m-%d-%Y",
    "%d-%m-%Y",
    "%Y-%m-%d %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%d %B %Y",
]

# Keywords suggesting a field is a measure
_MEASURE_KEYWORDS = {
    "sales", "profit", "revenue", "cost", "price", "amount", "total",
    "quantity", "discount", "margin", "rate", "ratio", "count", "sum",
    "average", "avg", "budget", "spend", "income", "expense", "fee",
    "tax", "weight", "height", "width", "length", "area", "volume",
    "score", "rating", "percentage", "pct",
}

# Keywords suggesting a field is a dimension (ID-like)
_DIMENSION_KEYWORDS = {
    "id", "key", "code", "name", "type", "category", "class",
    "group", "segment", "region", "country", "state", "city",
    "status", "flag", "label", "tag", "mode", "level",
}

# Geographic keywords
_GEO_KEYWORDS = {
    "country", "state", "city", "region", "province", "zip",
    "postal", "latitude", "longitude", "lat", "lng", "lon",
    "address", "county", "district", "territory",
}


@dataclass
class ColumnSpec:
    """Schema specification for a single CSV column."""

    name: str
    inferred_type: str  # "integer", "float", "date", "boolean", "string"
    sample_values: list[str] = field(default_factory=list)
    null_count: int = 0
    cardinality: int = 0
    total_rows: int = 0


@dataclass
class ClassifiedColumn:
    """A column with its inferred role."""

    spec: ColumnSpec
    role: str  # "dimension" or "measure"
    semantic_type: str  # "categorical", "temporal", "geographic", "numeric", "text"


@dataclass
class CsvSchema:
    """Complete schema for a CSV file."""

    columns: list[ColumnSpec]
    row_count: int
    file_path: str


@dataclass
class ClassifiedSchema:
    """Schema with classified columns."""

    columns: list[ClassifiedColumn]
    row_count: int
    file_path: str
    dimensions: list[ClassifiedColumn] = field(default_factory=list)
    measures: list[ClassifiedColumn] = field(default_factory=list)
    temporal: list[ClassifiedColumn] = field(default_factory=list)
    geographic: list[ClassifiedColumn] = field(default_factory=list)


def _try_parse_date(value: str) -> bool:
    """Check if a string can be parsed as a date."""
    for fmt in _DATE_FORMATS:
        try:
            datetime.strptime(value.strip(), fmt)
            return True
        except ValueError:
            continue
    return False


def _infer_column_type(values: list[str]) -> str:
    """Infer the data type from a sample of non-null values.

    Uses a threshold approach (80% match) rather than break-on-first-failure
    so a single oddly-formatted value doesn't poison the whole column.
    """
    if not values:
        return "string"

    n = len(values)
    threshold = 0.8  # 80% of values must match

    # Check boolean
    bool_vals = {"true", "false", "yes", "no", "1", "0", "t", "f", "y", "n"}
    if all(v.strip().lower() in bool_vals for v in values):
        return "boolean"

    # Check integer
    int_count = 0
    for v in values:
        try:
            int(v.strip().replace(",", ""))
            int_count += 1
        except ValueError:
            pass
    if int_count >= n * threshold:
        return "integer"

    # Check float (also handles currency/percentage formatting)
    float_count = 0
    for v in values:
        cleaned = v.strip().replace(",", "").replace("$", "").replace("%", "")
        # Handle parenthesized negatives like (100.00)
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = cleaned[1:-1]
        if cleaned.startswith("-"):
            cleaned = cleaned[1:]
        try:
            float(cleaned)
            float_count += 1
        except ValueError:
            pass
    if float_count >= n * threshold:
        return "float"

    # Check date
    date_count = sum(1 for v in values[:20] if _try_parse_date(v))
    if date_count >= len(values[:20]) * threshold:
        return "date"

    return "string"


def infer_csv_schema(
    csv_path: str | Path,
    sample_rows: int = 1000,
    encoding: str = "utf-8",
) -> CsvSchema:
    """Read a CSV file and infer the schema for each column.

    Args:
        csv_path: Path to the CSV file.
        sample_rows: Number of rows to sample for type inference.
        encoding: File encoding.

    Returns:
        CsvSchema with column specifications.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    with open(csv_path, "r", encoding=encoding, errors="replace") as f:
        reader = csv.reader(f)
        headers = next(reader)

        # Collect sample values per column
        column_values: list[list[str]] = [[] for _ in headers]
        null_counts = [0] * len(headers)
        unique_values: list[set] = [set() for _ in headers]
        row_count = 0

        for row in reader:
            row_count += 1
            for i, val in enumerate(row):
                if i >= len(headers):
                    break
                if val.strip() == "" or val.strip().lower() in ("null", "na", "n/a", "nan", "none"):
                    null_counts[i] += 1
                else:
                    if len(column_values[i]) < sample_rows:
                        column_values[i].append(val)
                    unique_values[i].add(val)

    columns = []
    for i, header in enumerate(headers):
        inferred_type = _infer_column_type(column_values[i])
        columns.append(ColumnSpec(
            name=header.strip(),
            inferred_type=inferred_type,
            sample_values=column_values[i][:5],
            null_count=null_counts[i],
            cardinality=len(unique_values[i]),
            total_rows=row_count,
        ))

    return CsvSchema(
        columns=columns,
        row_count=row_count,
        file_path=str(csv_path),
    )


def classify_columns(schema: CsvSchema) -> ClassifiedSchema:
    """Classify each column as dimension or measure with semantic types.

    Uses heuristics based on data type, cardinality, and column name patterns.
    """
    classified = []
    dimensions = []
    measures = []
    temporal = []
    geographic = []

    for col in schema.columns:
        name_lower = col.name.lower().replace("_", " ").replace("-", " ")
        name_words = set(name_lower.split())

        # Determine semantic type
        if col.inferred_type == "date":
            semantic_type = "temporal"
            role = "dimension"
        elif name_words & _GEO_KEYWORDS:
            semantic_type = "geographic"
            role = "dimension"
        elif col.inferred_type in ("integer", "float"):
            # Numeric columns: measure if high cardinality or name suggests it
            if name_words & _DIMENSION_KEYWORDS:
                semantic_type = "categorical"
                role = "dimension"
            elif col.cardinality <= 20 and col.total_rows > 50:
                # Low cardinality numeric → likely categorical ID
                semantic_type = "categorical"
                role = "dimension"
            else:
                semantic_type = "numeric"
                role = "measure"
        elif col.inferred_type == "boolean":
            semantic_type = "categorical"
            role = "dimension"
        else:  # string
            if name_words & _MEASURE_KEYWORDS:
                semantic_type = "numeric"
                role = "measure"
            else:
                semantic_type = "categorical"
                role = "dimension"

        # Override based on name patterns
        if name_words & _MEASURE_KEYWORDS and col.inferred_type in ("integer", "float"):
            role = "measure"
            semantic_type = "numeric"

        cc = ClassifiedColumn(spec=col, role=role, semantic_type=semantic_type)
        classified.append(cc)

        if role == "dimension":
            dimensions.append(cc)
        else:
            measures.append(cc)
        if semantic_type == "temporal":
            temporal.append(cc)
        if semantic_type == "geographic":
            geographic.append(cc)

    return ClassifiedSchema(
        columns=classified,
        row_count=schema.row_count,
        file_path=schema.file_path,
        dimensions=dimensions,
        measures=measures,
        temporal=temporal,
        geographic=geographic,
    )


def csv_to_hyper(
    csv_path: str | Path,
    hyper_path: str | Path,
    schema: CsvSchema | None = None,
    table_name: str = "Extract",
) -> str:
    """Convert a CSV file to a Tableau Hyper extract.

    Requires tableauhyperapi to be installed.

    Args:
        csv_path: Path to the source CSV file.
        hyper_path: Output path for the .hyper file.
        schema: Pre-computed schema (avoids re-reading the file).
        table_name: Name for the table inside the Hyper file.

    Returns:
        Confirmation message with row count.
    """
    try:
        from tableauhyperapi import (
            Connection,
            CreateMode,
            HyperProcess,
            Inserter,
            SqlType,
            TableDefinition,
            TableName,
            Telemetry,
        )
    except ImportError:
        raise ImportError(
            "tableauhyperapi is required for CSV-to-Hyper conversion. "
            "Install with: pip install tableauhyperapi"
        )

    csv_path = Path(csv_path)
    hyper_path = Path(hyper_path)

    if schema is None:
        schema = infer_csv_schema(csv_path)

    # Map inferred types to Hyper SQL types
    type_map = {
        "integer": SqlType.big_int(),
        "float": SqlType.double(),
        "date": SqlType.date(),
        "boolean": SqlType.bool(),
        "string": SqlType.text(),
    }

    # Build table definition
    columns = []
    for col in schema.columns:
        sql_type = type_map.get(col.inferred_type, SqlType.text())
        columns.append(TableDefinition.Column(col.name, sql_type))

    table_def = TableDefinition(
        table_name=TableName("Extract", table_name),
        columns=columns,
    )

    hyper_path.parent.mkdir(parents=True, exist_ok=True)

    with HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hyper:
        with Connection(
            endpoint=hyper.endpoint,
            database=str(hyper_path),
            create_mode=CreateMode.CREATE_AND_REPLACE,
        ) as conn:
            conn.catalog.create_schema_if_not_exists("Extract")
            conn.catalog.create_table(table_def)

            row_count = 0
            with Inserter(conn, table_def) as inserter:
                with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
                    reader = csv.reader(f)
                    next(reader)  # skip header

                    for row in reader:
                        typed_row = []
                        for i, col in enumerate(schema.columns):
                            if i >= len(row):
                                typed_row.append(None)
                                continue
                            val = row[i].strip()
                            if val == "" or val.lower() in ("null", "na", "n/a", "nan", "none", "%null%"):
                                typed_row.append(None)
                            elif col.inferred_type == "integer":
                                try:
                                    typed_row.append(int(val.replace(",", "")))
                                except (ValueError, OverflowError):
                                    typed_row.append(None)
                            elif col.inferred_type == "float":
                                try:
                                    cleaned = val.replace(",", "").replace("$", "").replace("%", "")
                                    if cleaned.startswith("(") and cleaned.endswith(")"):
                                        cleaned = "-" + cleaned[1:-1]
                                    typed_row.append(float(cleaned))
                                except (ValueError, OverflowError):
                                    typed_row.append(None)
                            elif col.inferred_type == "boolean":
                                typed_row.append(val.lower() in ("true", "yes", "1", "t", "y"))
                            elif col.inferred_type == "date":
                                parsed = None
                                for fmt in _DATE_FORMATS:
                                    try:
                                        parsed = datetime.strptime(val, fmt).date()
                                        break
                                    except ValueError:
                                        continue
                                typed_row.append(parsed)
                            else:
                                typed_row.append(val)

                        inserter.add_row(typed_row)
                        row_count += 1

                inserter.execute()

    return f"Created Hyper extract at {hyper_path} ({row_count} rows, {len(schema.columns)} columns)"


def format_schema_summary(schema: CsvSchema | ClassifiedSchema) -> str:
    """Format a schema as a human-readable summary string."""
    lines = [f"=== CSV Schema: {schema.file_path} ({schema.row_count} rows) ==="]

    if isinstance(schema, ClassifiedSchema):
        if schema.dimensions:
            lines.append("\nDimensions:")
            for cc in schema.dimensions:
                lines.append(
                    f"  {cc.spec.name} ({cc.spec.inferred_type}, "
                    f"{cc.semantic_type}, cardinality={cc.spec.cardinality})"
                )
        if schema.measures:
            lines.append("\nMeasures:")
            for cc in schema.measures:
                lines.append(
                    f"  {cc.spec.name} ({cc.spec.inferred_type}, "
                    f"cardinality={cc.spec.cardinality})"
                )
        if schema.temporal:
            lines.append(f"\nTemporal fields: {', '.join(c.spec.name for c in schema.temporal)}")
        if schema.geographic:
            lines.append(f"Geographic fields: {', '.join(c.spec.name for c in schema.geographic)}")
    else:
        for col in schema.columns:
            null_pct = f", {col.null_count}/{col.total_rows} nulls" if col.null_count else ""
            lines.append(
                f"  {col.name}: {col.inferred_type} "
                f"(cardinality={col.cardinality}{null_pct})"
            )

    return "\n".join(lines)
