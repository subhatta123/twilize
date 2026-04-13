"""Tests for the prompt_interpreter module.

Covers:
- Chart type detection from various phrasings
- Color / palette extraction
- Theme detection
- Layout detection
- Field extraction heuristics
- create_from_prompt() round-trip (produces a valid .twb file)
- TWBEditor.from_prompt() class-method
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from twilize.prompt_interpreter import (
    PromptInterpretation,
    _detect_chart_type,
    _detect_colors,
    _detect_layout,
    _detect_palette,
    _detect_theme,
    create_from_prompt,
    interpret_prompt,
)
from twilize.twb_editor import TWBEditor


# ---------------------------------------------------------------------------
# Chart type detection
# ---------------------------------------------------------------------------

class TestDetectChartType:
    def test_bar_chart(self):
        assert _detect_chart_type("create a bar chart") == "Bar"

    def test_line_graph(self):
        assert _detect_chart_type("show me a line graph of revenue") == "Line"

    def test_area_chart(self):
        assert _detect_chart_type("area chart with stacked data") == "Area"

    def test_pie_chart(self):
        assert _detect_chart_type("pie chart by region") == "Pie"

    def test_donut_chart(self):
        assert _detect_chart_type("donut chart showing category share") == "Pie"

    def test_scatter(self):
        assert _detect_chart_type("scatter plot of profit vs sales") == "Circle"

    def test_bubble(self):
        assert _detect_chart_type("bubble chart for market size") == "Circle"

    def test_heatmap(self):
        assert _detect_chart_type("heatmap of sales by month") == "Square"

    def test_heat_map_two_words(self):
        assert _detect_chart_type("heat map showing density") == "Square"

    def test_map(self):
        assert _detect_chart_type("geographic map of customers") == "Map"

    def test_text_table(self):
        assert _detect_chart_type("text table with measures") == "Text"

    def test_kpi(self):
        assert _detect_chart_type("kpi scorecard for Q4") == "Text"

    def test_default_bar(self):
        # No chart keyword → default Bar
        assert _detect_chart_type("show me something") == "Bar"

    def test_column_chart(self):
        assert _detect_chart_type("column chart of sales") == "Bar"


# ---------------------------------------------------------------------------
# Color detection
# ---------------------------------------------------------------------------

class TestDetectColors:
    def test_single_colour(self):
        colors = _detect_colors("blue bar chart")
        assert "#4E79A7" in colors

    def test_multiple_colours(self):
        colors = _detect_colors("red and green theme")
        assert "#E15759" in colors
        assert "#59A14F" in colors

    def test_no_colour(self):
        assert _detect_colors("show sales data") == []

    def test_dark_blue(self):
        colors = _detect_colors("dark blue chart")
        assert "#1f5fa6" in colors

    def test_teal(self):
        colors = _detect_colors("use teal")
        assert "#499894" in colors

    def test_no_duplicates(self):
        colors = _detect_colors("blue blue blue")
        assert colors.count("#4E79A7") == 1


# ---------------------------------------------------------------------------
# Palette detection
# ---------------------------------------------------------------------------

class TestDetectPalette:
    def test_tableau10(self):
        assert _detect_palette("using tableau10 palette") == "tableau10"

    def test_tableau_10_spaced(self):
        assert _detect_palette("tableau 10 colours") == "tableau10"

    def test_blue_red(self):
        assert _detect_palette("blue-red palette") == "blue-red"

    def test_green_gold(self):
        assert _detect_palette("green gold scheme") == "green-gold"

    def test_none(self):
        assert _detect_palette("use default colours") == ""


# ---------------------------------------------------------------------------
# Theme detection
# ---------------------------------------------------------------------------

class TestDetectTheme:
    def test_dark(self):
        assert _detect_theme("dark theme chart") == "dark"

    def test_dark_mode(self):
        assert _detect_theme("dark mode dashboard") == "dark"

    def test_corporate(self):
        assert _detect_theme("corporate style") == "corporate-blue"

    def test_minimal(self):
        assert _detect_theme("minimalist layout") == "minimal"

    def test_vibrant(self):
        assert _detect_theme("colorful bold chart") == "vibrant"

    def test_light(self):
        assert _detect_theme("light theme") == "modern-light"

    def test_none(self):
        assert _detect_theme("create a chart") == ""


# ---------------------------------------------------------------------------
# Layout detection
# ---------------------------------------------------------------------------

class TestDetectLayout:
    def test_vertical(self):
        assert _detect_layout("vertical layout") == "vertical"

    def test_horizontal(self):
        assert _detect_layout("horizontal layout") == "horizontal"

    def test_grid(self):
        assert _detect_layout("grid layout 2x2") == "grid-2x2"

    def test_side_by_side(self):
        assert _detect_layout("side by side charts") == "horizontal"

    def test_default(self):
        assert _detect_layout("make a chart") == "vertical"


# ---------------------------------------------------------------------------
# interpret_prompt — full integration
# ---------------------------------------------------------------------------

class TestInterpretPrompt:
    def test_returns_interpretation(self):
        result = interpret_prompt("bar chart")
        assert isinstance(result, PromptInterpretation)

    def test_chart_type_detected(self):
        result = interpret_prompt("Create a line chart of Revenue over Month")
        assert result.chart_type == "Line"

    def test_colour_detected(self):
        result = interpret_prompt("bar chart with blue colour")
        assert "#4E79A7" in result.colors

    def test_palette_takes_priority_over_colour(self):
        result = interpret_prompt("use tableau20 palette with blue bars")
        assert result.palette_name == "tableau20"

    def test_theme_detected(self):
        result = interpret_prompt("dark-themed pie chart")
        assert result.theme == "dark"

    def test_layout_detected(self):
        result = interpret_prompt("horizontal layout line chart")
        assert result.layout == "horizontal"

    def test_field_extraction_capitalized(self):
        result = interpret_prompt("bar chart showing Sales by Category")
        # At minimum one of the two fields should be found
        all_fields = result.rows + result.columns + ([result.color_field] if result.color_field else [])
        assert any("Sales" in f or "Category" in f for f in all_fields)

    def test_raw_prompt_preserved(self):
        prompt = "some prompt text"
        result = interpret_prompt(prompt)
        assert result.raw_prompt == prompt

    def test_no_fields_warning(self):
        result = interpret_prompt("create a bar chart")
        # Should still parse without error
        assert result.chart_type == "Bar"

    def test_quoted_fields(self):
        result = interpret_prompt('chart showing "Order Date" and "Profit"')
        all_fields = result.rows + result.columns + ([result.color_field] if result.color_field else [])
        assert any("Order Date" in f for f in all_fields)


# ---------------------------------------------------------------------------
# create_from_prompt — produces a valid .twb file
# ---------------------------------------------------------------------------

class TestCreateFromPrompt:
    def test_basic_bar_chart(self, tmp_path):
        out = tmp_path / "out.twb"
        result = create_from_prompt("bar chart", output_path=str(out))
        assert Path(result).exists()
        content = Path(result).read_text(encoding="utf-8")
        assert "<workbook" in content

    def test_twbx_output(self, tmp_path):
        out = tmp_path / "out.twbx"
        result = create_from_prompt("line chart", output_path=str(out))
        assert Path(result).exists()
        assert Path(result).suffix == ".twbx"

    def test_pie_chart(self, tmp_path):
        out = tmp_path / "pie.twb"
        result = create_from_prompt(
            "Pie chart showing Sales by Region with blue colours",
            output_path=str(out),
        )
        assert Path(result).exists()

    def test_dark_theme(self, tmp_path):
        out = tmp_path / "dark.twb"
        result = create_from_prompt(
            "dark-themed bar chart of Revenue by Category",
            output_path=str(out),
        )
        assert Path(result).exists()

    def test_colour_palette(self, tmp_path):
        out = tmp_path / "palette.twb"
        result = create_from_prompt(
            "bar chart using tableau10 palette, vertical layout",
            output_path=str(out),
        )
        content = Path(result).read_text(encoding="utf-8")
        # Palette should be written to preferences
        assert "color-palette" in content

    def test_no_dashboard(self, tmp_path):
        out = tmp_path / "no_db.twb"
        result = create_from_prompt(
            "bar chart",
            output_path=str(out),
            add_dashboard=False,
        )
        assert Path(result).exists()

    def test_no_theme(self, tmp_path):
        out = tmp_path / "no_theme.twb"
        result = create_from_prompt(
            "dark line chart",
            output_path=str(out),
            apply_theme=False,
        )
        assert Path(result).exists()

    def test_heatmap(self, tmp_path):
        out = tmp_path / "heatmap.twb"
        result = create_from_prompt(
            "heatmap of Sales by Month and Category with teal colours",
            output_path=str(out),
        )
        assert Path(result).exists()

    def test_horizontal_layout(self, tmp_path):
        out = tmp_path / "horiz.twb"
        result = create_from_prompt(
            "bar chart with horizontal layout",
            output_path=str(out),
        )
        assert Path(result).exists()


# ---------------------------------------------------------------------------
# TWBEditor.from_prompt — class-method wrapper
# ---------------------------------------------------------------------------

class TestTWBEditorFromPrompt:
    def test_class_method_exists(self):
        assert hasattr(TWBEditor, "from_prompt")
        assert callable(TWBEditor.from_prompt)

    def test_produces_file(self, tmp_path):
        out = tmp_path / "editor_prompt.twb"
        result = TWBEditor.from_prompt(
            "Create a corporate-blue bar chart of Profit by Segment",
            output_path=str(out),
        )
        assert Path(result).exists()
        content = Path(result).read_text(encoding="utf-8")
        assert "<workbook" in content

    def test_line_chart_prompt(self, tmp_path):
        out = tmp_path / "line.twb"
        TWBEditor.from_prompt(
            "Line chart showing Revenue over Time using green colours, dark theme",
            output_path=str(out),
        )
        assert out.exists()

    def test_scatter_prompt(self, tmp_path):
        out = tmp_path / "scatter.twb"
        TWBEditor.from_prompt(
            "Scatter plot of Profit vs Sales with vibrant palette",
            output_path=str(out),
        )
        assert out.exists()
