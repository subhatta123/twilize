"""Tests for reference lines, trend lines, and themes."""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from twilize.server import (
    add_worksheet,
    configure_chart,
    create_workbook,
    add_dashboard,
)
from twilize.mcp.state import get_editor
from twilize.mcp.tools_workbook import (
    add_reference_line,
    add_reference_band,
    add_trend_line,
    apply_color_palette,
    apply_dashboard_theme,
)

TEMPLATE = Path("templates/twb/superstore.twb")


@pytest.fixture(autouse=True)
def fresh_workbook():
    create_workbook(str(TEMPLATE), "Reference Line Test")


def _setup_chart():
    """Create a basic bar chart for reference line testing."""
    add_worksheet("Sales Chart")
    configure_chart(
        worksheet_name="Sales Chart",
        mark_type="Bar",
        columns=["Category"],
        rows=["SUM(Sales)"],
    )


class TestReferenceLines:
    def test_add_constant_reference_line(self):
        _setup_chart()
        result = add_reference_line(
            worksheet_name="Sales Chart",
            axis_field="SUM(Sales)",
            value="50000",
            formula="constant",
        )
        assert "reference line" in result.lower()

        editor = get_editor()
        ws = editor._find_worksheet("Sales Chart")
        refs = ws.findall(".//reference-line")
        assert len(refs) >= 1
        assert refs[0].get("formula") == "constant"
        assert refs[0].get("value") == "50000.0"

    def test_add_average_reference_line(self):
        _setup_chart()
        result = add_reference_line(
            worksheet_name="Sales Chart",
            axis_field="SUM(Sales)",
            formula="average",
        )
        assert "average" in result.lower()

    def test_add_reference_line_with_color(self):
        _setup_chart()
        add_reference_line(
            worksheet_name="Sales Chart",
            axis_field="SUM(Sales)",
            value="30000",
            line_color="#FF0000",
        )
        editor = get_editor()
        ws = editor._find_worksheet("Sales Chart")
        ref = ws.find(".//reference-line")
        color_fmt = ref.find(".//format[@attr='line-color']")
        assert color_fmt is not None
        assert color_fmt.get("value") == "#FF0000"


class TestReferenceBands:
    def test_add_reference_band(self):
        _setup_chart()
        result = add_reference_band(
            worksheet_name="Sales Chart",
            axis_field="SUM(Sales)",
            from_value="10000",
            to_value="50000",
        )
        assert "band" in result.lower()

        editor = get_editor()
        ws = editor._find_worksheet("Sales Chart")
        refs = ws.findall(".//reference-line")
        assert len(refs) >= 2  # from + to


class TestTrendLines:
    def test_add_linear_trend_line(self):
        add_worksheet("Trend Chart")
        configure_chart(
            worksheet_name="Trend Chart",
            mark_type="Line",
            columns=["YEAR(Order Date)"],
            rows=["SUM(Sales)"],
        )
        result = add_trend_line(
            worksheet_name="Trend Chart",
            fit="linear",
        )
        assert "linear" in result.lower()

        editor = get_editor()
        ws = editor._find_worksheet("Trend Chart")
        tl = ws.find(".//trendline")
        assert tl is not None
        assert tl.get("fit") == "linear"
        assert tl.get("enabled") == "true"

    def test_add_polynomial_trend_line(self):
        add_worksheet("Poly Chart")
        configure_chart(
            worksheet_name="Poly Chart",
            mark_type="Line",
            columns=["YEAR(Order Date)"],
            rows=["SUM(Sales)"],
        )
        result = add_trend_line(
            worksheet_name="Poly Chart",
            fit="polynomial",
            degree=3,
        )
        assert "polynomial" in result

        editor = get_editor()
        ws = editor._find_worksheet("Poly Chart")
        tl = ws.find(".//trendline")
        assert tl.get("degree") == "3"

    def test_invalid_fit_type_rejected(self):
        add_worksheet("Bad Trend")
        configure_chart(
            worksheet_name="Bad Trend",
            mark_type="Line",
            columns=["YEAR(Order Date)"],
            rows=["SUM(Sales)"],
        )
        with pytest.raises(ValueError, match="Unknown fit type"):
            add_trend_line(worksheet_name="Bad Trend", fit="cubic")


class TestColorPalettes:
    def test_apply_named_palette(self):
        result = apply_color_palette(palette_name="tableau10")
        assert "tableau10" in result

        editor = get_editor()
        prefs = editor.root.find("preferences")
        assert prefs is not None
        cp = prefs.find("color-palette[@name='tableau10']")
        assert cp is not None
        colors = cp.findall("color")
        assert len(colors) == 10

    def test_apply_custom_colors(self):
        result = apply_color_palette(
            colors=["#FF0000", "#00FF00", "#0000FF"],
            custom_name="my-palette",
        )
        assert "my-palette" in result

    def test_invalid_palette_name_rejected(self):
        with pytest.raises(ValueError, match="Unknown palette"):
            apply_color_palette(palette_name="nonexistent")


class TestDashboardTheme:
    def test_apply_background_theme(self):
        add_worksheet("Sheet1")
        configure_chart(
            worksheet_name="Sheet1",
            mark_type="Bar",
            columns=["Category"],
            rows=["SUM(Sales)"],
        )
        add_dashboard(
            dashboard_name="My Dashboard",
            worksheet_names=["Sheet1"],
        )
        result = apply_dashboard_theme(
            dashboard_name="My Dashboard",
            background_color="#F5F5F5",
        )
        assert "My Dashboard" in result
        assert "background" in result
