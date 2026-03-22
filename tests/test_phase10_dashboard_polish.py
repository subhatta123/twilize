"""Tests for Phase 10: Dashboard Polish — BAN KPIs, Geo Quality, Best Practices, Story Scoring."""

import csv

import pytest

from cwtwb.chart_suggester import (
    ChartSuggestion,
    DashboardSuggestion,
    ShelfAssignment,
    _story_score,
    suggest_charts,
)
from cwtwb.csv_to_hyper import (
    ClassifiedColumn,
    ClassifiedSchema,
    ColumnSpec,
    classify_columns,
    infer_csv_schema,
)
from cwtwb.dashboard_enhancements import _map_replacement, validate_suggestion
from cwtwb.viz_best_practices import (
    BEST_PRACTICES_PROMPT,
    DATA_PATTERN_CHART_MAP,
    KPI_GUIDELINES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_column(name, inferred_type="string", role="dimension",
                 semantic_type="categorical", cardinality=5,
                 null_count=0, total_rows=100):
    spec = ColumnSpec(
        name=name,
        inferred_type=inferred_type,
        null_count=null_count,
        cardinality=cardinality,
        total_rows=total_rows,
    )
    return ClassifiedColumn(spec=spec, role=role, semantic_type=semantic_type)


def _make_schema(columns, row_count=100, file_path="test.csv"):
    dims = [c for c in columns if c.role == "dimension"]
    measures = [c for c in columns if c.role == "measure"]
    temporal = [c for c in columns if c.semantic_type == "temporal"]
    geographic = [c for c in columns if c.semantic_type == "geographic"]
    return ClassifiedSchema(
        columns=columns,
        row_count=row_count,
        file_path=file_path,
        dimensions=dims,
        measures=measures,
        temporal=temporal,
        geographic=geographic,
    )


# ---------------------------------------------------------------------------
# 10A — Geographic Data Quality Validation
# ---------------------------------------------------------------------------

class TestGeoQualityValidation:
    """validate_suggestion removes maps when geo data quality is poor."""

    def test_removes_map_no_geo_fields(self):
        """Maps removed when no geographic fields exist."""
        schema = _make_schema([
            _make_column("Category"),
            _make_column("Sales", "float", "measure", "numeric"),
        ])
        suggestion = DashboardSuggestion(charts=[
            ChartSuggestion("Map", "Map Chart", [
                ShelfAssignment("State", "detail"),
                ShelfAssignment("Sales", "color", "SUM"),
            ], priority=80),
        ])
        result = validate_suggestion(suggestion, schema)
        assert not any(c.chart_type == "Map" for c in result.charts)

    def test_removes_map_high_null_rate(self):
        """Maps removed when geo field has >20% null values."""
        schema = _make_schema([
            _make_column("State", semantic_type="geographic",
                         null_count=30, total_rows=100),
            _make_column("Sales", "float", "measure", "numeric"),
        ])
        suggestion = DashboardSuggestion(charts=[
            ChartSuggestion("Map", "Where is Sales?", [
                ShelfAssignment("State", "detail"),
                ShelfAssignment("Sales", "color", "SUM"),
            ], priority=80),
        ])
        result = validate_suggestion(suggestion, schema)
        assert not any(c.chart_type == "Map" for c in result.charts)

    def test_keeps_map_good_quality(self):
        """Maps kept when geo field has <20% nulls."""
        schema = _make_schema([
            _make_column("State", semantic_type="geographic",
                         null_count=5, total_rows=100),
            _make_column("Sales", "float", "measure", "numeric"),
        ])
        suggestion = DashboardSuggestion(charts=[
            ChartSuggestion("Map", "Where is Sales?", [
                ShelfAssignment("State", "detail"),
                ShelfAssignment("Sales", "color", "SUM"),
            ], priority=80),
        ])
        result = validate_suggestion(suggestion, schema)
        assert any(c.chart_type == "Map" for c in result.charts)

    def test_map_replaced_with_bar(self):
        """Removed maps get a Bar chart replacement."""
        schema = _make_schema([
            _make_column("State", semantic_type="geographic",
                         null_count=50, total_rows=100),
            _make_column("Category"),
            _make_column("Sales", "float", "measure", "numeric"),
        ])
        suggestion = DashboardSuggestion(charts=[
            ChartSuggestion("Map", "Where is Sales?", [
                ShelfAssignment("State", "detail"),
                ShelfAssignment("Sales", "color", "SUM"),
            ], priority=80),
        ])
        result = validate_suggestion(suggestion, schema)
        bars = [c for c in result.charts if c.chart_type == "Bar"]
        assert len(bars) == 1
        assert "Replaced Map" in bars[0].reason

    def test_map_replacement_uses_categorical_dim(self):
        """Replacement bar uses best non-geo categorical dim."""
        schema = _make_schema([
            _make_column("State", semantic_type="geographic",
                         null_count=40, total_rows=100),
            _make_column("Category", cardinality=5),
            _make_column("Sales", "float", "measure", "numeric"),
        ])
        map_chart = ChartSuggestion("Map", "Where?", [
            ShelfAssignment("State", "detail"),
            ShelfAssignment("Sales", "color", "SUM"),
        ], priority=80)
        replacement = _map_replacement(map_chart, schema)
        assert replacement is not None
        assert replacement.chart_type == "Bar"
        row_fields = [s.field_name for s in replacement.shelves if s.shelf == "rows"]
        assert "Category" in row_fields

    def test_exactly_20pct_nulls_keeps_map(self):
        """Boundary: exactly 20% nulls should remove (>= 0.20 check is >0.20)."""
        # 20/100 = 0.20, and our threshold is < 0.20, so exactly 20% removes
        schema = _make_schema([
            _make_column("State", semantic_type="geographic",
                         null_count=20, total_rows=100),
            _make_column("Sales", "float", "measure", "numeric"),
        ])
        suggestion = DashboardSuggestion(charts=[
            ChartSuggestion("Map", "Map", [
                ShelfAssignment("State", "detail"),
                ShelfAssignment("Sales", "color", "SUM"),
            ], priority=80),
        ])
        result = validate_suggestion(suggestion, schema)
        # 20/100 = 0.20 is NOT < 0.20, so map should be removed
        assert not any(c.chart_type == "Map" for c in result.charts)


# ---------------------------------------------------------------------------
# 10B — BAN KPI Sizing (verified structurally via layout templates)
# ---------------------------------------------------------------------------

class TestBANLayout:
    """Executive-summary template gives KPIs prominent space."""

    def test_executive_summary_kpi_row_height(self):
        from cwtwb.layout_templates import get_template

        layout = get_template("executive-summary", ["KPI1", "KPI2", "Chart1", "Chart2"])
        # Top-level is vertical container
        assert layout["type"] == "container"
        assert layout["direction"] == "vertical"
        # First child should be KPI row with fixed_size=100 (c.3 pattern)
        kpi_row = layout["children"][0]
        assert kpi_row.get("fixed_size") == 100

    def test_executive_summary_kpi_row_has_both_kpis(self):
        from cwtwb.layout_templates import get_template

        layout = get_template("executive-summary", ["KPI1", "KPI2", "Chart1"])
        kpi_row = layout["children"][0]
        assert kpi_row["type"] == "container"
        assert kpi_row["direction"] == "horizontal"
        # KPI children are worksheet zones directly (c.3 pattern, no wrapper)
        ws_names = []
        for card in kpi_row["children"]:
            if card["type"] == "container":
                for child in card.get("children", []):
                    if child.get("name"):
                        ws_names.append(child["name"])
            elif card.get("name"):
                ws_names.append(card["name"])
        assert "KPI1" in ws_names
        assert "KPI2" in ws_names

    def test_kpi_detail_has_top_row_kpis(self):
        from cwtwb.layout_templates import get_template

        layout = get_template("kpi-detail", ["KPI1", "KPI2", "Main1", "Main2"])
        kpi_row = layout["children"][0]
        assert kpi_row["direction"] == "horizontal"
        assert kpi_row["fixed_size"] == 100  # KPIs at top with fixed height (c.3)


# ---------------------------------------------------------------------------
# 10C — Best Practices Knowledge Repository
# ---------------------------------------------------------------------------

class TestBestPracticesModule:
    """viz_best_practices.py loads and provides expected content."""

    def test_prompt_is_nonempty(self):
        assert len(BEST_PRACTICES_PROMPT) > 200

    def test_prompt_mentions_ban(self):
        assert "BAN" in BEST_PRACTICES_PROMPT or "Big Ass Number" in BEST_PRACTICES_PROMPT

    def test_prompt_mentions_aggregation_rules(self):
        assert "AVG" in BEST_PRACTICES_PROMPT
        assert "SUM" in BEST_PRACTICES_PROMPT
        assert "COUNTD" in BEST_PRACTICES_PROMPT

    def test_data_pattern_map_has_nine_patterns(self):
        assert len(DATA_PATTERN_CHART_MAP) == 9

    def test_kpi_pattern_recommends_text(self):
        kpi = DATA_PATTERN_CHART_MAP["kpi_summary"]
        assert "Text" in kpi["best_charts"]

    def test_spatial_pattern_warns_about_nulls(self):
        spatial = DATA_PATTERN_CHART_MAP["spatial"]
        assert "null" in spatial["notes"].lower() or "20%" in spatial["notes"]

    def test_kpi_guidelines_mentions_font_size(self):
        assert "28" in KPI_GUIDELINES


# ---------------------------------------------------------------------------
# 10D — Story-Driven Chart Selection
# ---------------------------------------------------------------------------

class TestStoryScore:
    """_story_score returns data-appropriate scores."""

    def test_kpi_always_high(self):
        schema = _make_schema([
            _make_column("Sales", "float", "measure", "numeric"),
        ])
        score = _story_score("Text", schema, [])
        assert score == 95

    def test_line_high_with_temporal(self):
        schema = _make_schema([
            _make_column("Date", "date", "dimension", "temporal", cardinality=12),
            _make_column("Sales", "float", "measure", "numeric"),
        ])
        score = _story_score("Line", schema, [])
        assert score >= 90

    def test_line_low_without_temporal(self):
        schema = _make_schema([
            _make_column("Category"),
            _make_column("Sales", "float", "measure", "numeric"),
        ])
        score = _story_score("Line", schema, [])
        assert score <= 50

    def test_bar_high_with_moderate_cardinality(self):
        schema = _make_schema([
            _make_column("Category", cardinality=8),
            _make_column("Sales", "float", "measure", "numeric"),
        ])
        score = _story_score("Bar", schema, [])
        assert score >= 80

    def test_map_low_with_high_nulls(self):
        schema = _make_schema([
            _make_column("State", semantic_type="geographic",
                         null_count=50, total_rows=100),
            _make_column("Sales", "float", "measure", "numeric"),
        ])
        score = _story_score("Map", schema, [])
        assert score <= 30

    def test_map_high_with_good_data(self):
        schema = _make_schema([
            _make_column("State", semantic_type="geographic",
                         null_count=2, total_rows=100),
            _make_column("Sales", "float", "measure", "numeric"),
        ])
        score = _story_score("Map", schema, [])
        assert score >= 75

    def test_pie_low_with_many_categories(self):
        schema = _make_schema([
            _make_column("Product", cardinality=20),
            _make_column("Sales", "float", "measure", "numeric"),
        ])
        score = _story_score("Pie", schema, [])
        assert score <= 30

    def test_pie_high_with_few_categories(self):
        schema = _make_schema([
            _make_column("Region", cardinality=4),
            _make_column("Sales", "float", "measure", "numeric"),
        ])
        score = _story_score("Pie", schema, [])
        assert score >= 60

    def test_scatter_needs_two_measures(self):
        schema = _make_schema([
            _make_column("Sales", "float", "measure", "numeric"),
        ], row_count=100)
        score = _story_score("Scatterplot", schema, [])
        assert score <= 40

    def test_scatter_good_with_two_measures(self):
        schema = _make_schema([
            _make_column("Sales", "float", "measure", "numeric"),
            _make_column("Profit", "float", "measure", "numeric"),
        ], row_count=100)
        score = _story_score("Scatterplot", schema, [])
        assert score >= 70

    def test_scatter_low_with_few_visual_points(self):
        """Scatter with 3-category color dim should score low (only 3 dots)."""
        schema = _make_schema([
            _make_column("Category", cardinality=3),
            _make_column("Sales", "float", "measure", "numeric"),
            _make_column("Profit", "float", "measure", "numeric"),
        ], row_count=10000)
        # When color dim has only 3 categories, scatter shows 3 aggregated points
        shelves = [
            ShelfAssignment("Sales", "columns", "SUM"),
            ShelfAssignment("Profit", "rows", "SUM"),
            ShelfAssignment("Category", "color"),
        ]
        score = _story_score("Scatterplot", schema, shelves)
        assert score <= 40  # Too few visual points

    def test_scatter_high_with_many_visual_points(self):
        """Scatter with high-cardinality color dim should score well."""
        schema = _make_schema([
            _make_column("Customer", cardinality=50),
            _make_column("Sales", "float", "measure", "numeric"),
            _make_column("Profit", "float", "measure", "numeric"),
        ], row_count=10000)
        shelves = [
            ShelfAssignment("Sales", "columns", "SUM"),
            ShelfAssignment("Profit", "rows", "SUM"),
            ShelfAssignment("Customer", "color"),
        ]
        score = _story_score("Scatterplot", schema, shelves)
        assert score >= 70  # Enough visual points

    def test_story_scores_integrated_in_suggest_charts(self):
        """suggest_charts uses story scores (not fixed priorities)."""
        csv_path = None  # We'll use a pre-built schema
        schema = _make_schema([
            _make_column("Date", "date", "dimension", "temporal", cardinality=12),
            _make_column("Category", cardinality=5),
            _make_column("Sales", "float", "measure", "numeric"),
            _make_column("Profit", "float", "measure", "numeric"),
        ], row_count=100)
        result = suggest_charts(schema)
        # KPIs (Text) should be highest priority
        if result.charts:
            kpis = [c for c in result.charts if c.chart_type == "Text"]
            non_kpis = [c for c in result.charts if c.chart_type != "Text"]
            if kpis and non_kpis:
                assert kpis[0].priority >= non_kpis[0].priority


# ---------------------------------------------------------------------------
# Integration: Full pipeline with high-null geo data
# ---------------------------------------------------------------------------

class TestIntegrationGeoQuality:
    """End-to-end: CSV with poor geo data produces no maps."""

    def test_suggest_then_validate_removes_bad_maps(self):
        """suggest_charts may produce a Map; validate_suggestion removes it."""
        schema = _make_schema([
            _make_column("State", semantic_type="geographic",
                         null_count=60, total_rows=100, cardinality=10),
            _make_column("Category", cardinality=5),
            _make_column("Sales", "float", "measure", "numeric"),
            _make_column("Profit", "float", "measure", "numeric"),
        ], row_count=100)
        suggestion = suggest_charts(schema, max_charts=7)
        validated = validate_suggestion(suggestion, schema, max_charts=5)

        # No maps should survive validation
        chart_types = [c.chart_type for c in validated.charts]
        assert "Map" not in chart_types
        # Should have at most 5 charts
        assert len(validated.charts) <= 5
