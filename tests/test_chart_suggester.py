"""Tests for the rule-based chart suggestion engine."""

import csv

import pytest

from cwtwb.chart_suggester import (
    ChartSuggestion,
    DashboardSuggestion,
    format_suggestions,
    suggest_charts,
)
from cwtwb.csv_to_hyper import classify_columns, infer_csv_schema


@pytest.fixture
def full_csv(tmp_path):
    """CSV with temporal, categorical, geographic, and numeric columns."""
    csv_path = tmp_path / "orders.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Category", "Region", "Sales", "Profit", "Quantity"])
        for i in range(100):
            writer.writerow([
                f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                ["Furniture", "Technology", "Office Supplies"][i % 3],
                ["East", "West", "Central", "South"][i % 4],
                round(100 + i * 10.5, 2),
                round(20 + i * 3.1, 2),
                (i % 15) + 1,
            ])
    return csv_path


@pytest.fixture
def numeric_only_csv(tmp_path):
    """CSV with only numeric measures, no dimensions."""
    csv_path = tmp_path / "metrics.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Revenue", "Cost", "Margin"])
        for i in range(50):
            writer.writerow([1000 + i * 10, 800 + i * 8, 200 + i * 2])
    return csv_path


@pytest.fixture
def categorical_csv(tmp_path):
    """CSV with categorical dimensions and one measure."""
    csv_path = tmp_path / "products.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Category", "Sub-Category", "Sales"])
        cats = ["Furniture", "Technology", "Supplies"]
        subs = ["Chairs", "Tables", "Phones", "Laptops", "Paper", "Binders"]
        for i in range(60):
            writer.writerow([cats[i % 3], subs[i % 6], round(50 + i * 7.5, 2)])
    return csv_path


def _get_classified(csv_path):
    schema = infer_csv_schema(csv_path)
    return classify_columns(schema)


class TestChartSuggestion:
    def test_suggests_line_for_temporal(self, full_csv):
        classified = _get_classified(full_csv)
        result = suggest_charts(classified)
        assert isinstance(result, DashboardSuggestion)
        types = [c.chart_type for c in result.charts]
        assert "Line" in types

    def test_suggests_bar_for_categorical(self, full_csv):
        classified = _get_classified(full_csv)
        result = suggest_charts(classified)
        types = [c.chart_type for c in result.charts]
        assert "Bar" in types

    def test_suggests_scatter_for_two_measures(self, full_csv):
        classified = _get_classified(full_csv)
        result = suggest_charts(classified)
        types = [c.chart_type for c in result.charts]
        assert "Scatterplot" in types

    def test_max_charts_limit(self, full_csv):
        classified = _get_classified(full_csv)
        result = suggest_charts(classified, max_charts=3)
        assert len(result.charts) <= 3

    def test_priority_ordering(self, full_csv):
        classified = _get_classified(full_csv)
        result = suggest_charts(classified)
        priorities = [c.priority for c in result.charts]
        assert priorities == sorted(priorities, reverse=True)

    def test_shelf_assignments_present(self, full_csv):
        classified = _get_classified(full_csv)
        result = suggest_charts(classified)
        for chart in result.charts:
            assert len(chart.shelves) > 0
            for shelf in chart.shelves:
                assert shelf.field_name
                assert shelf.shelf

    def test_text_kpi_for_measures_only(self, numeric_only_csv):
        classified = _get_classified(numeric_only_csv)
        result = suggest_charts(classified)
        types = [c.chart_type for c in result.charts]
        # Should suggest scatter (2+ measures) and possibly Text KPI
        assert "Scatterplot" in types or "Text" in types

    def test_heatmap_for_two_categories(self, categorical_csv):
        classified = _get_classified(categorical_csv)
        result = suggest_charts(classified)
        types = [c.chart_type for c in result.charts]
        assert "Heatmap" in types or "Bar" in types

    def test_dashboard_title_derived(self, full_csv):
        classified = _get_classified(full_csv)
        result = suggest_charts(classified)
        assert "Dashboard" in result.title
        assert "Orders" in result.title

    def test_layout_type(self, full_csv):
        classified = _get_classified(full_csv)
        result = suggest_charts(classified)
        assert result.layout in ("grid", "horizontal", "vertical")


class TestFormatSuggestions:
    def test_format_output(self, full_csv):
        classified = _get_classified(full_csv)
        result = suggest_charts(classified)
        formatted = format_suggestions(result)
        assert "Dashboard:" in formatted
        assert "Type:" in formatted
        assert "Reason:" in formatted
