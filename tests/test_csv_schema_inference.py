"""Tests for CSV schema inference and column classification."""

import csv
import tempfile
from pathlib import Path

import pytest

from twilize.csv_to_hyper import (
    ClassifiedSchema,
    CsvSchema,
    classify_columns,
    format_schema_summary,
    infer_csv_schema,
)


@pytest.fixture
def sample_csv(tmp_path):
    """Create a sample CSV with mixed column types."""
    csv_path = tmp_path / "sample.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Order ID", "Date", "Category", "City", "Sales", "Profit", "Quantity"])
        for i in range(50):
            writer.writerow([
                f"ORD-{i:04d}",
                f"2024-01-{(i % 28) + 1:02d}",
                ["Furniture", "Technology", "Office Supplies"][i % 3],
                ["New York", "Los Angeles", "Chicago", "Houston", "Phoenix"][i % 5],
                round(100.0 + i * 12.5, 2),
                round(10.0 + i * 2.3, 2),
                (i % 10) + 1,
            ])
    return csv_path


@pytest.fixture
def numeric_only_csv(tmp_path):
    """CSV with only numeric columns."""
    csv_path = tmp_path / "numeric.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Sales", "Profit", "Cost", "Revenue"])
        for i in range(30):
            writer.writerow([100 + i, 50 + i, 80 + i, 200 + i])
    return csv_path


@pytest.fixture
def boolean_csv(tmp_path):
    """CSV with boolean columns."""
    csv_path = tmp_path / "booleans.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Name", "Active", "Premium"])
        for i in range(20):
            writer.writerow([f"User {i}", "true" if i % 2 == 0 else "false", "yes" if i % 3 == 0 else "no"])
    return csv_path


class TestSchemaInference:
    def test_infer_basic_types(self, sample_csv):
        schema = infer_csv_schema(sample_csv)
        assert isinstance(schema, CsvSchema)
        assert schema.row_count == 50
        assert len(schema.columns) == 7

        type_map = {c.name: c.inferred_type for c in schema.columns}
        assert type_map["Sales"] == "float"
        assert type_map["Profit"] == "float"
        assert type_map["Quantity"] == "integer"
        assert type_map["Date"] == "date"
        assert type_map["Category"] == "string"

    def test_cardinality(self, sample_csv):
        schema = infer_csv_schema(sample_csv)
        card_map = {c.name: c.cardinality for c in schema.columns}
        assert card_map["Category"] == 3
        assert card_map["City"] == 5

    def test_sample_values_captured(self, sample_csv):
        schema = infer_csv_schema(sample_csv)
        for col in schema.columns:
            assert len(col.sample_values) <= 5

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            infer_csv_schema("/nonexistent/file.csv")

    def test_boolean_detection(self, boolean_csv):
        schema = infer_csv_schema(boolean_csv)
        type_map = {c.name: c.inferred_type for c in schema.columns}
        assert type_map["Active"] == "boolean"
        assert type_map["Premium"] == "boolean"

    def test_numeric_only(self, numeric_only_csv):
        schema = infer_csv_schema(numeric_only_csv)
        for col in schema.columns:
            assert col.inferred_type in ("integer", "float")


class TestColumnClassification:
    def test_classify_dimensions_measures(self, sample_csv):
        schema = infer_csv_schema(sample_csv)
        classified = classify_columns(schema)
        assert isinstance(classified, ClassifiedSchema)

        dim_names = {c.spec.name for c in classified.dimensions}
        measure_names = {c.spec.name for c in classified.measures}

        assert "Category" in dim_names
        assert "Sales" in measure_names
        assert "Profit" in measure_names

    def test_temporal_detection(self, sample_csv):
        schema = infer_csv_schema(sample_csv)
        classified = classify_columns(schema)
        temporal_names = {c.spec.name for c in classified.temporal}
        assert "Date" in temporal_names

    def test_geographic_detection(self, sample_csv):
        schema = infer_csv_schema(sample_csv)
        classified = classify_columns(schema)
        geo_names = {c.spec.name for c in classified.geographic}
        assert "City" in geo_names

    def test_all_columns_classified(self, sample_csv):
        schema = infer_csv_schema(sample_csv)
        classified = classify_columns(schema)
        assert len(classified.columns) == len(schema.columns)
        for cc in classified.columns:
            assert cc.role in ("dimension", "measure")
            assert cc.semantic_type in ("categorical", "temporal", "geographic", "numeric", "text")


class TestFormatSummary:
    def test_raw_schema_summary(self, sample_csv):
        schema = infer_csv_schema(sample_csv)
        summary = format_schema_summary(schema)
        assert "sample.csv" in summary
        assert "50 rows" in summary
        assert "Sales" in summary

    def test_classified_schema_summary(self, sample_csv):
        schema = infer_csv_schema(sample_csv)
        classified = classify_columns(schema)
        summary = format_schema_summary(classified)
        assert "Dimensions:" in summary
        assert "Measures:" in summary
