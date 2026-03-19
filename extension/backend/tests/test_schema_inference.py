"""Tests for extension schema inference."""

import pytest

from extension.backend.schema_inference import TableauField, classify_tableau_fields


class TestClassifyTableauFields:
    def test_basic_classification(self):
        fields = [
            TableauField(name="Category", datatype="string", cardinality=5),
            TableauField(name="Sales", datatype="float", cardinality=100),
            TableauField(name="Date", datatype="date", cardinality=365),
        ]
        result = classify_tableau_fields(fields, row_count=1000)
        roles = {c.spec.name: c.role for c in result.columns}
        assert roles["Category"] == "dimension"
        assert roles["Sales"] == "measure"
        assert roles["Date"] == "dimension"  # temporal → dimension

    def test_temporal_classification(self):
        fields = [
            TableauField(name="Order Date", datatype="datetime", cardinality=365),
        ]
        result = classify_tableau_fields(fields, row_count=500)
        assert result.temporal[0].spec.name == "Order Date"

    def test_empty_fields(self):
        result = classify_tableau_fields([], row_count=0)
        assert len(result.columns) == 0
