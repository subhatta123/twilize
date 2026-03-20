"""Rule-based chart suggestion engine.

Given a classified CSV schema, suggests appropriate chart types and
shelf assignments based on data shape (field types, cardinality,
temporal presence, etc.).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from cwtwb.csv_to_hyper import ClassifiedColumn, ClassifiedSchema

logger = logging.getLogger(__name__)


@dataclass
class ShelfAssignment:
    """A field placed on a specific shelf."""

    field_name: str
    shelf: str  # "columns", "rows", "color", "size", "label", "detail", "pages"
    aggregation: str = ""  # "SUM", "AVG", "COUNT", etc.


@dataclass
class ChartSuggestion:
    """A suggested chart configuration."""

    chart_type: str  # mark type for configure_chart
    title: str
    shelves: list[ShelfAssignment] = field(default_factory=list)
    reason: str = ""
    priority: int = 0  # higher = better fit


@dataclass
class DashboardSuggestion:
    """A complete dashboard suggestion with multiple charts."""

    charts: list[ChartSuggestion] = field(default_factory=list)
    layout: str = "grid"  # "grid", "vertical", "horizontal"
    title: str = ""
    template: str = ""  # Named layout template (e.g., "executive-summary", "overview")
    layout_dict: dict | None = field(default=None)  # Custom FlexNode layout from image


def suggest_charts(schema: ClassifiedSchema, max_charts: int = 5) -> DashboardSuggestion:
    """Suggest charts based on the classified schema.

    Uses rule-based heuristics to pick chart types that best represent
    the data shape.

    Args:
        schema: Classified CSV schema with dimension/measure/temporal info.
        max_charts: Maximum number of charts to suggest.

    Returns:
        DashboardSuggestion with prioritized chart list.
    """
    suggestions: list[ChartSuggestion] = []

    dims = schema.dimensions
    measures = schema.measures
    temporal = schema.temporal
    geographic = schema.geographic

    # --- Temporal + Measure → Line chart ---
    if temporal and measures:
        time_col = temporal[0]
        for m in measures[:2]:
            suggestions.append(ChartSuggestion(
                chart_type="Line",
                title=f"{m.spec.name} over {time_col.spec.name}",
                shelves=[
                    ShelfAssignment(time_col.spec.name, "columns"),
                    ShelfAssignment(m.spec.name, "rows", "SUM"),
                ],
                reason="Temporal dimension with numeric measure → line chart",
                priority=90,
            ))

    # --- Temporal + Measure + categorical dim → Line with color ---
    cat_dims = [d for d in dims if d.semantic_type == "categorical"
                and d not in temporal and d not in geographic
                and d.spec.cardinality <= 12]
    if temporal and measures and cat_dims:
        time_col = temporal[0]
        color_dim = cat_dims[0]
        m = measures[0]
        suggestions.append(ChartSuggestion(
            chart_type="Line",
            title=f"{m.spec.name} by {color_dim.spec.name} over time",
            shelves=[
                ShelfAssignment(time_col.spec.name, "columns"),
                ShelfAssignment(m.spec.name, "rows", "SUM"),
                ShelfAssignment(color_dim.spec.name, "color"),
            ],
            reason="Time series with categorical breakdown → colored line chart",
            priority=85,
        ))

    # --- Categorical dim + Measure → Bar chart ---
    if cat_dims and measures:
        dim = _best_categorical_dim(cat_dims)
        m = measures[0]
        suggestions.append(ChartSuggestion(
            chart_type="Bar",
            title=f"{m.spec.name} by {dim.spec.name}",
            shelves=[
                ShelfAssignment(dim.spec.name, "rows"),
                ShelfAssignment(m.spec.name, "columns", "SUM"),
            ],
            reason="Categorical dimension with numeric measure → horizontal bar chart",
            priority=80,
        ))

    # --- Two measures → Scatter plot ---
    if len(measures) >= 2:
        m1, m2 = measures[0], measures[1]
        shelves = [
            ShelfAssignment(m1.spec.name, "columns", "SUM"),
            ShelfAssignment(m2.spec.name, "rows", "SUM"),
        ]
        if cat_dims:
            shelves.append(ShelfAssignment(cat_dims[0].spec.name, "color"))
        suggestions.append(ChartSuggestion(
            chart_type="Scatterplot",
            title=f"{m1.spec.name} vs {m2.spec.name}",
            shelves=shelves,
            reason="Two numeric measures → scatter plot",
            priority=70,
        ))

    # --- Geographic dim + Measure → Map ---
    if geographic and measures:
        geo = geographic[0]
        m = measures[0]
        suggestions.append(ChartSuggestion(
            chart_type="Map",
            title=f"{m.spec.name} by {geo.spec.name}",
            shelves=[
                ShelfAssignment(geo.spec.name, "detail"),
                ShelfAssignment(m.spec.name, "color", "SUM"),
            ],
            reason="Geographic dimension with measure → filled map",
            priority=75,
        ))

    # --- Categorical dim (few values) + Measure → Pie chart ---
    small_cat = [d for d in cat_dims if d.spec.cardinality <= 6]
    if small_cat and measures:
        dim = small_cat[0]
        m = measures[0]
        suggestions.append(ChartSuggestion(
            chart_type="Pie",
            title=f"{m.spec.name} distribution by {dim.spec.name}",
            shelves=[
                ShelfAssignment(m.spec.name, "size", "SUM"),
                ShelfAssignment(dim.spec.name, "color"),
            ],
            reason="Low-cardinality categorical with measure → pie chart",
            priority=60,
        ))

    # --- Categorical dim + Categorical dim + Measure → Heatmap ---
    if len(cat_dims) >= 2 and measures:
        d1 = cat_dims[0]
        d2 = cat_dims[1]
        m = measures[0]
        suggestions.append(ChartSuggestion(
            chart_type="Heatmap",
            title=f"{m.spec.name}: {d1.spec.name} × {d2.spec.name}",
            shelves=[
                ShelfAssignment(d1.spec.name, "columns"),
                ShelfAssignment(d2.spec.name, "rows"),
                ShelfAssignment(m.spec.name, "color", "SUM"),
            ],
            reason="Two categorical dimensions with measure → heatmap",
            priority=55,
        ))

    # --- Categorical dim + multiple measures → stacked bar ---
    if cat_dims and len(measures) >= 2:
        dim = _best_categorical_dim(cat_dims)
        m1, m2 = measures[0], measures[1]
        suggestions.append(ChartSuggestion(
            chart_type="Bar",
            title=f"{m1.spec.name} and {m2.spec.name} by {dim.spec.name}",
            shelves=[
                ShelfAssignment(dim.spec.name, "rows"),
                ShelfAssignment(m1.spec.name, "columns", "SUM"),
                ShelfAssignment(m2.spec.name, "columns", "SUM"),
            ],
            reason="Category with multiple measures → grouped bar chart",
            priority=50,
        ))

    # --- Single measure, no dims → KPI / text ---
    if measures and not cat_dims and not temporal:
        m = measures[0]
        suggestions.append(ChartSuggestion(
            chart_type="Text",
            title=f"Total {m.spec.name}",
            shelves=[
                ShelfAssignment(m.spec.name, "label", "SUM"),
            ],
            reason="Single measure with no dimensions → KPI text",
            priority=40,
        ))

    # --- Categorical dim (high cardinality) + Measure → Treemap ---
    high_cat = [d for d in cat_dims if d.spec.cardinality > 8]
    if high_cat and measures:
        dim = high_cat[0]
        m = measures[0]
        suggestions.append(ChartSuggestion(
            chart_type="Tree Map",
            title=f"{m.spec.name} by {dim.spec.name}",
            shelves=[
                ShelfAssignment(dim.spec.name, "detail"),
                ShelfAssignment(m.spec.name, "size", "SUM"),
                ShelfAssignment(dim.spec.name, "color"),
            ],
            reason="High-cardinality categorical with measure → treemap",
            priority=45,
        ))

    # Sort by priority and trim
    suggestions.sort(key=lambda s: s.priority, reverse=True)

    # --- Best practice: temporal guard ---
    # If a Line chart uses a temporal dim, remove Bar charts using the same dim
    temporal_dims_in_lines = set()
    for s in suggestions:
        if s.chart_type == "Line":
            for sh in s.shelves:
                if sh.shelf == "columns" and not sh.aggregation:
                    temporal_dims_in_lines.add(sh.field_name)
    if temporal_dims_in_lines:
        suggestions = [
            s for s in suggestions
            if not (s.chart_type == "Bar" and any(
                sh.field_name in temporal_dims_in_lines
                for sh in s.shelves if sh.shelf in ("columns", "rows") and not sh.aggregation
            ))
        ]

    # --- Best practice: deduplicate chart types ---
    # Allow max 2 of the same type, and only if primary axis fields differ
    deduped: list[ChartSuggestion] = []
    type_count: dict[str, int] = {}
    type_fields: dict[str, list[set[str]]] = {}
    for s in suggestions:
        ct = s.chart_type
        primary_fields = {sh.field_name for sh in s.shelves if sh.shelf in ("columns", "rows")}
        if ct not in type_count:
            type_count[ct] = 0
            type_fields[ct] = []
        if type_count[ct] >= 2:
            continue
        # Skip if same primary fields already exist for this type
        if any(primary_fields == existing for existing in type_fields[ct]):
            continue
        type_count[ct] += 1
        type_fields[ct].append(primary_fields)
        deduped.append(s)
    suggestions = deduped[:max_charts]

    # Determine layout
    n = len(suggestions)
    if n <= 2:
        layout = "horizontal"
    else:
        layout = "grid"

    # Select template based on chart mix
    has_kpi = any(s.chart_type == "Text" for s in suggestions)
    template = ""
    if has_kpi:
        template = "executive-summary"
    elif n <= 3:
        template = "overview"
    else:
        template = "grid"

    return DashboardSuggestion(
        charts=suggestions,
        layout=layout,
        title=_derive_title(schema),
        template=template,
    )


def format_suggestions(suggestion: DashboardSuggestion) -> str:
    """Format dashboard suggestion as a human-readable summary."""
    lines = [f"=== Dashboard: {suggestion.title} (layout: {suggestion.layout}) ===\n"]

    for i, chart in enumerate(suggestion.charts, 1):
        lines.append(f"{i}. {chart.title}")
        lines.append(f"   Type: {chart.chart_type} | Priority: {chart.priority}")
        lines.append(f"   Reason: {chart.reason}")
        for shelf in chart.shelves:
            agg = f" ({shelf.aggregation})" if shelf.aggregation else ""
            lines.append(f"   → {shelf.shelf}: {shelf.field_name}{agg}")
        lines.append("")

    return "\n".join(lines)


_COUNT_KEYWORDS = {"count", "quantity", "number", "num_", "qty", "n_", "total_count"}


def _default_aggregation(field_name: str) -> str:
    """Return the best default aggregation for a measure field.

    Count-like fields get COUNT; everything else gets SUM.
    """
    lower = field_name.lower()
    for kw in _COUNT_KEYWORDS:
        if kw in lower:
            return "COUNT"
    return "SUM"


def _best_categorical_dim(dims: list[ClassifiedColumn]) -> ClassifiedColumn:
    """Pick the best categorical dimension for a primary axis.

    Prefers moderate cardinality (3-15) over very low or very high.
    """
    scored = []
    for d in dims:
        card = d.spec.cardinality
        if 3 <= card <= 15:
            score = 100
        elif card <= 2:
            score = 30
        else:
            score = max(10, 80 - card)
        scored.append((score, d))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _derive_title(schema: ClassifiedSchema) -> str:
    """Derive a dashboard title from the file path."""
    from pathlib import Path

    name = Path(schema.file_path).stem
    # Convert snake_case/kebab-case to title case
    title = name.replace("_", " ").replace("-", " ").title()
    return f"{title} Dashboard"
