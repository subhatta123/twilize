"""Tests for extension chart suggestion (rule-based fallback)."""

import pytest

from extension.backend.chart_suggestion import (
    dict_to_suggestion,
    suggest_dashboard,
    _suggestion_to_dict,
)
from extension.backend.schema_inference import TableauField


class TestSuggestDashboard:
    def test_rule_based_fallback(self):
        fields = [
            TableauField(name="Date", datatype="date", cardinality=365),
            TableauField(name="Category", datatype="string", cardinality=5),
            TableauField(name="Region", datatype="string", cardinality=4),
            TableauField(name="Sales", datatype="float", cardinality=500),
            TableauField(name="Profit", datatype="float", cardinality=400),
        ]
        result = suggest_dashboard(fields, row_count=1000, max_charts=4)
        assert "charts" in result
        assert "title" in result
        assert len(result["charts"]) <= 4
        assert len(result["charts"]) > 0

    def test_chart_has_shelves(self):
        fields = [
            TableauField(name="Category", datatype="string", cardinality=5),
            TableauField(name="Sales", datatype="float", cardinality=200),
        ]
        result = suggest_dashboard(fields, row_count=100)
        for chart in result["charts"]:
            assert "shelves" in chart
            assert "chart_type" in chart

    def test_empty_fields(self):
        result = suggest_dashboard([], row_count=0)
        assert "charts" in result
        assert len(result["charts"]) == 0


class TestDictConversion:
    def test_roundtrip(self):
        plan = {
            "title": "Test Dashboard",
            "layout": "grid",
            "charts": [
                {
                    "chart_type": "Bar",
                    "title": "Sales by Category",
                    "shelves": [
                        {"field_name": "Category", "shelf": "rows", "aggregation": ""},
                        {"field_name": "Sales", "shelf": "columns", "aggregation": "SUM"},
                    ],
                    "reason": "test",
                    "priority": 80,
                }
            ],
        }
        suggestion = dict_to_suggestion(plan)
        assert suggestion.title == "Test Dashboard"
        assert len(suggestion.charts) == 1
        assert suggestion.charts[0].chart_type == "Bar"

        result = _suggestion_to_dict(suggestion)
        assert result["title"] == "Test Dashboard"
        assert len(result["charts"]) == 1
