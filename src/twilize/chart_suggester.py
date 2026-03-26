"""Rule-based chart suggestion engine.

Given a classified CSV schema, suggests appropriate chart types and
shelf assignments based on data shape (field types, cardinality,
temporal presence, etc.).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from twilize.csv_to_hyper import ClassifiedColumn, ClassifiedSchema

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
    top_n: dict | None = field(default=None)  # {"field": ..., "n": 10, "by": "SUM(Sales)"}
    sort_descending: str = ""  # e.g. "SUM(Sales)" — adds descending shelf sort
    text_format: dict | None = field(default=None)  # {"AVG(Margin)": "0.00%"}


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

    # --- Always add KPI summaries for top measures ---
    # Generate up to 4 KPIs when enough measures exist (fills KPI row)
    kpi_limit = min(4, len(measures))
    for m in measures[:kpi_limit]:
        agg = smart_aggregation(m.spec.name)
        kpi_title = _kpi_title(agg, m.spec.name)
        # Auto-format numbers for readability:
        #   - Rates/percentages → "12.01%" (not 0.1201)
        #   - Currency → "$173.0B" (not 173,000,000,000 or ############)
        #   - Population → "8.5B" (not 85,05,91,27,313)
        fmt_str = _smart_number_format(m.spec.name, agg)
        kpi_text_fmt = {f"{agg}({m.spec.name})": fmt_str}
        suggestions.append(ChartSuggestion(
            chart_type="Text",
            title=kpi_title,
            shelves=[
                ShelfAssignment(m.spec.name, "label", agg),
            ],
            reason="Key metric summary KPI",
            priority=95,
            text_format=kpi_text_fmt,
        ))

    # --- Temporal + Measure → Line chart ---
    if temporal and measures:
        time_col = temporal[0]
        # Always use MONTH granularity for line trends — bare date fields
        # default to YEAR in Tableau which typically produces too few data
        # points (e.g. 4 dots for 4 years).  MONTH gives ~48 points.
        time_expr = f"MONTH({time_col.spec.name})"
        for m in measures[:2]:
            suggestions.append(ChartSuggestion(
                chart_type="Line",
                title=f"{m.spec.name} Trend Over Time",
                shelves=[
                    ShelfAssignment(time_expr, "columns"),
                    ShelfAssignment(m.spec.name, "rows", smart_aggregation(m.spec.name)),
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
        time_expr_color = f"MONTH({time_col.spec.name})"
        color_dim = cat_dims[0]
        m = measures[0]
        suggestions.append(ChartSuggestion(
            chart_type="Line",
            title=f"{m.spec.name} by {color_dim.spec.name} Over Time",
            shelves=[
                ShelfAssignment(time_expr_color, "columns"),
                ShelfAssignment(m.spec.name, "rows", smart_aggregation(m.spec.name)),
                ShelfAssignment(color_dim.spec.name, "color"),
            ],
            reason="Time series with categorical breakdown → colored line chart",
            priority=85,
        ))

    # --- Categorical dim + Measure → Bar chart ---
    # When the dimension has >10 distinct values, auto-apply a Top 10
    # filter so the chart stays readable (fixes visual clutter).
    if cat_dims and measures:
        dim = _best_categorical_dim(cat_dims)
        m = measures[0]
        agg = smart_aggregation(m.spec.name)
        bar_title = f"Top 10 {dim.spec.name} by {m.spec.name}" if dim.spec.cardinality > 10 else f"{m.spec.name} by {dim.spec.name}"
        bar_text_fmt = None
        if _is_rate_field(m.spec.name):
            bar_text_fmt = {f"{agg}({m.spec.name})": "0.00%"}
        bar_chart = ChartSuggestion(
            chart_type="Bar",
            title=bar_title,
            shelves=[
                ShelfAssignment(dim.spec.name, "rows"),
                ShelfAssignment(m.spec.name, "columns", agg),
            ],
            reason="Categorical dimension with numeric measure → horizontal bar chart",
            priority=80,
            text_format=bar_text_fmt,
        )
        # Attach top-N and sort metadata for pipeline to pick up
        if dim.spec.cardinality > 10:
            bar_chart.top_n = {"field": dim.spec.name, "n": 10, "by": f"{agg}({m.spec.name})"}
        bar_chart.sort_descending = f"{agg}({m.spec.name})"
        suggestions.append(bar_chart)

    # --- Two measures → Scatter plot ---
    if len(measures) >= 2:
        m1, m2 = measures[0], measures[1]
        agg1 = smart_aggregation(m1.spec.name)
        agg2 = smart_aggregation(m2.spec.name)
        shelves = [
            ShelfAssignment(m1.spec.name, "columns", agg1),
            ShelfAssignment(m2.spec.name, "rows", agg2),
        ]
        # Add detail dimension for scatter granularity + color for grouping
        # Use a moderate-cardinality dimension to avoid overplotting
        scatter_detail_dim = None
        if cat_dims:
            # Pick best dimension for detail (labelling individual points)
            all_non_geo = [d for d in dims if d.semantic_type == "categorical"
                           and d not in temporal and d not in geographic]
            # For color, prefer low cardinality; for detail prefer higher
            if all_non_geo:
                scatter_detail_dim = max(all_non_geo, key=lambda d: d.spec.cardinality)
                shelves.append(ShelfAssignment(scatter_detail_dim.spec.name, "detail"))
            if cat_dims[0].spec.cardinality <= 12:
                shelves.append(ShelfAssignment(cat_dims[0].spec.name, "color"))
        # Format rate fields on axes
        scatter_fmt = {}
        if _is_rate_field(m1.spec.name):
            scatter_fmt[f"{agg1}({m1.spec.name})"] = "0.00%"
        if _is_rate_field(m2.spec.name):
            scatter_fmt[f"{agg2}({m2.spec.name})"] = "0.00%"
        suggestions.append(ChartSuggestion(
            chart_type="Scatterplot",
            title=f"{m2.spec.name} vs {m1.spec.name}",
            shelves=shelves,
            reason="Two numeric measures → scatter plot",
            priority=70,
            text_format=scatter_fmt or None,
        ))

    # --- Geographic dim + Measure → Map ---
    if geographic and measures:
        geo = geographic[0]
        m = measures[0]
        suggestions.append(ChartSuggestion(
            chart_type="Map",
            title=f"Where is {m.spec.name} concentrated?",
            shelves=[
                ShelfAssignment(geo.spec.name, "detail"),
                ShelfAssignment(m.spec.name, "color", smart_aggregation(m.spec.name)),
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
            title=f"How is {m.spec.name} split across {dim.spec.name}?",
            shelves=[
                ShelfAssignment(m.spec.name, "size", smart_aggregation(m.spec.name)),
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
            title=f"How does {m.spec.name} vary across {d1.spec.name} and {d2.spec.name}?",
            shelves=[
                ShelfAssignment(d1.spec.name, "columns"),
                ShelfAssignment(d2.spec.name, "rows"),
                ShelfAssignment(m.spec.name, "color", smart_aggregation(m.spec.name)),
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
            title=f"How do {m1.spec.name} and {m2.spec.name} compare by {dim.spec.name}?",
            shelves=[
                ShelfAssignment(dim.spec.name, "rows"),
                ShelfAssignment(m1.spec.name, "columns", smart_aggregation(m1.spec.name)),
                ShelfAssignment(m2.spec.name, "columns", smart_aggregation(m2.spec.name)),
            ],
            reason="Category with multiple measures → grouped bar chart",
            priority=50,
        ))

    # KPIs are now always added at the top (priority=95)

    # --- Categorical dim (high cardinality) + Measure → Treemap ---
    high_cat = [d for d in cat_dims if d.spec.cardinality > 8]
    if high_cat and measures:
        dim = high_cat[0]
        m = measures[0]
        suggestions.append(ChartSuggestion(
            chart_type="Tree Map",
            title=f"How is {m.spec.name} distributed across {dim.spec.name}?",
            shelves=[
                ShelfAssignment(dim.spec.name, "detail"),
                ShelfAssignment(m.spec.name, "size", smart_aggregation(m.spec.name)),
                ShelfAssignment(dim.spec.name, "color"),
            ],
            reason="High-cardinality categorical with measure → treemap",
            priority=45,
        ))

    # Replace fixed priorities with data-driven story scores
    for s in suggestions:
        s.priority = _story_score(s.chart_type, schema, s.shelves)

    # Sort by story score and trim
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
    suggestions = deduplicate_charts(suggestions, max_per_type=2)
    suggestions = suggestions[:max_charts]

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


def deduplicate_charts(
    charts: list[ChartSuggestion],
    max_per_type: int = 2,
) -> list[ChartSuggestion]:
    """Remove duplicate charts that show the same data in the same way.

    Allows up to ``max_per_type`` charts of each type, and only if their
    primary axis fields AND full shelf signatures differ.  This prevents
    two Bar charts with the same dimension + same measure from both
    appearing even when generated by different suggestion rules.
    """
    deduped: list[ChartSuggestion] = []
    type_count: dict[str, int] = {}
    type_sigs: dict[str, list[tuple[frozenset, frozenset]]] = {}

    for s in charts:
        ct = s.chart_type
        primary_fields = frozenset(
            sh.field_name for sh in s.shelves if sh.shelf in ("columns", "rows")
        )
        # Also consider label shelf for Text/KPI charts (they have no columns/rows)
        if not primary_fields:
            primary_fields = frozenset(
                sh.field_name for sh in s.shelves if sh.shelf == "label"
            )
        full_sig = frozenset(
            (sh.field_name, sh.shelf, sh.aggregation) for sh in s.shelves
        )

        if ct not in type_count:
            type_count[ct] = 0
            type_sigs[ct] = []
        # Allow more KPI/Text charts since they're compact and high-value
        effective_max = max_per_type * 2 if ct == "Text" else max_per_type
        if type_count[ct] >= effective_max:
            continue
        # Reject if primary fields match OR full shelf signature matches
        if any(
            primary_fields == ep or full_sig == es
            for ep, es in type_sigs[ct]
        ):
            continue

        type_count[ct] += 1
        type_sigs[ct].append((primary_fields, full_sig))
        deduped.append(s)
    return deduped


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


_RATE_KEYWORDS = {
    "discount", "margin", "rate", "ratio", "percentage", "pct",
    "share", "yield", "efficiency", "utilization", "conversion",
    "bounce", "churn", "retention", "satisfaction", "score", "rating",
    "avg", "average", "mean",
}

_AMOUNT_KEYWORDS = {
    "sales", "profit", "revenue", "cost", "price", "amount",
    "total", "budget", "spend", "income", "expense", "fee",
    "tax", "value", "balance", "payment",
}

_COUNT_KEYWORDS = {
    "count", "quantity", "number", "num_", "qty", "n_",
    "total_count", "orders", "transactions", "visits", "clicks",
}

_ID_KEYWORDS = {"id", "key", "code", "identifier", "uuid"}


def smart_aggregation(field_name: str) -> str:
    """Choose the correct aggregation for a measure based on field semantics.

    Priority: Rate → ID → Count → Amount → fallback SUM.

    * Rates/percentages (discount, margin, score) → ``AVG``
    * Identifiers (order id, customer key) → ``COUNTD``
    * Count-like fields (quantity, num_orders) → ``SUM``
    * Amounts (sales, profit, revenue) → ``SUM``
    * Unknown → ``SUM``
    """
    lower = field_name.lower()

    # Rate / percentage fields → AVG
    for kw in _RATE_KEYWORDS:
        if kw in lower:
            return "AVG"

    # ID / identifier fields → COUNTD
    for kw in _ID_KEYWORDS:
        if kw in lower:
            return "COUNTD"

    # Count-like fields → SUM (summing counts is valid)
    for kw in _COUNT_KEYWORDS:
        if kw in lower:
            return "SUM"

    # Amount fields → SUM (explicit match)
    for kw in _AMOUNT_KEYWORDS:
        if kw in lower:
            return "SUM"

    # Default
    return "SUM"


# Keep backward-compatible alias
_default_aggregation = smart_aggregation


def _is_rate_field(field_name: str) -> bool:
    """Return True if the field represents a rate/percentage/ratio."""
    lower = field_name.lower()
    # Detect "% GDP", "% of Sales", etc. — the % symbol is a strong signal
    if "%" in field_name:
        return True
    return any(kw in lower for kw in _RATE_KEYWORDS)


def _is_currency_field(field_name: str) -> bool:
    """Return True if the field represents a monetary amount."""
    lower = field_name.lower()
    _CURRENCY_KW = {"sales", "revenue", "cost", "price", "amount", "profit",
                    "income", "expense", "fee", "tax", "payment", "budget", "spend",
                    "gdp", "wage", "salary", "debt", "investment", "capital"}
    return any(kw in lower for kw in _CURRENCY_KW)


def _is_population_field(field_name: str) -> bool:
    """Return True if the field represents a count of people/units (large numbers)."""
    lower = field_name.lower()
    _POP_KW = {"population", "headcount", "employees", "users", "subscribers",
               "visitors", "customers"}
    return any(kw in lower for kw in _POP_KW)


def _smart_number_format(field_name: str, aggregation: str) -> str:
    """Choose a human-friendly Tableau number format for KPI values.

    Uses Tableau's comma-suffix trick to abbreviate large numbers:
    - One comma divides by 1,000      → ``#,##0.0,"K"``
    - Two commas divide by 1,000,000  → ``#,##0.0,,"M"``
    - Three commas divide by 1B       → ``#,##0.0,,,"B"``

    For currency fields the ``$`` prefix is prepended.
    For rate/percentage fields ``0.00%`` is used.
    For population/large-count fields ``#,##0.0,,,"B"`` (or "M") is used.
    """
    if _is_rate_field(field_name):
        return "0.00%"

    # Currency fields: always abbreviate to avoid overflow ("############")
    if _is_currency_field(field_name):
        if aggregation == "AVG":
            return "$#,##0"  # averages are usually small
        return '$#,##0.0,,,"B"'  # SUM of revenue/sales → likely billions/millions

    # Population / large-count fields
    if _is_population_field(field_name):
        return '#,##0.0,,,"B"'

    # Generic large number: use standard formatting with commas
    return "#,##0"


def _kpi_title(aggregation: str, field_name: str) -> str:
    """Generate a KPI title that reflects the chosen aggregation."""
    _AGG_LABELS = {
        "SUM": "Total",
        "AVG": "Average",
        "COUNT": "Count of",
        "COUNTD": "Distinct",
        "MIN": "Minimum",
        "MAX": "Maximum",
    }
    label = _AGG_LABELS.get(aggregation, "Total")
    return f"{label} {field_name}"


def _story_score(
    chart_type: str,
    schema: ClassifiedSchema,
    shelves: list[ShelfAssignment],
) -> int:
    """Score a chart suggestion by its analytical story value.

    Instead of fixed priorities per chart type, this evaluates how well
    the *data* supports the analytical pattern the chart represents.
    Higher scores mean stronger data support.
    """
    dims = schema.dimensions
    measures = schema.measures
    temporal = schema.temporal
    geographic = schema.geographic
    cat_dims = [d for d in dims if d.semantic_type == "categorical"]

    if chart_type == "Text":
        # KPIs always provide immediate context — high value
        return 95

    if chart_type == "Line":
        if temporal:
            time_card = temporal[0].spec.cardinality
            if time_card >= 6:
                return 92  # Strong temporal story
            elif time_card >= 3:
                return 82  # Moderate temporal story
        return 40  # No temporal data — line chart is weak

    if chart_type == "Bar":
        if cat_dims:
            best_card = max(d.spec.cardinality for d in cat_dims)
            if 3 <= best_card <= 15:
                return 85  # Sweet spot for ranking/comparison
            elif best_card <= 2:
                return 55  # Too few categories
            else:
                return 70  # High cardinality — still works
        return 35

    if chart_type == "Scatterplot":
        if len(measures) >= 2:
            # Estimate visual data points: when a categorical dim is used as
            # color/detail, the scatter shows one aggregated point per category
            # — NOT one per raw row.  We need enough *distinct* points for a
            # meaningful correlation view.
            color_dims = [
                sh for sh in shelves
                if sh.shelf in ("color", "detail") and not sh.aggregation
            ]
            if color_dims:
                # Find the cardinality of the color/detail dimension
                color_name = color_dims[0].field_name
                color_col = next(
                    (d for d in cat_dims if d.spec.name == color_name), None
                )
                visual_points = color_col.spec.cardinality if color_col else schema.row_count
            else:
                visual_points = schema.row_count

            if visual_points >= 15:
                return 78  # Enough points for correlation
            elif visual_points >= 6:
                return 60  # Marginal — small scatter
            return 35  # Too few visual points for meaningful scatter
        return 30

    if chart_type == "Map":
        if geographic:
            geo = geographic[0]
            total = geo.spec.total_rows or schema.row_count
            if total > 0:
                null_ratio = geo.spec.null_count / total
                if null_ratio < 0.10:
                    return 80  # Good geographic data
                elif null_ratio < 0.20:
                    return 60  # Marginal quality
                return 20  # Too many nulls — story is weak
            return 70  # Can't determine quality
        return 10  # No geographic fields

    if chart_type == "Pie":
        small_cats = [d for d in cat_dims if d.spec.cardinality <= 5]
        if small_cats:
            return 65
        elif cat_dims and min(d.spec.cardinality for d in cat_dims) <= 6:
            return 55
        return 25

    if chart_type == "Heatmap":
        if len(cat_dims) >= 2:
            return 60
        return 20

    if chart_type == "Tree Map":
        high_cat = [d for d in cat_dims if d.spec.cardinality > 8]
        if high_cat:
            return 55
        return 30

    if chart_type == "Area":
        if temporal:
            return 75
        return 35

    return 50  # default for unknown chart types


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


def _auto_detect_theme(schema: ClassifiedSchema) -> str:
    """Pick a theme based on data domain heuristics.

    Examines field names for domain-specific keywords to select
    the most appropriate visual theme.
    """
    field_names = " ".join(c.spec.name.lower() for c in schema.columns)

    # Financial / business data → corporate-blue
    _CORPORATE_KW = (
        "revenue", "budget", "fiscal", "quarter", "finance",
        "sales", "profit", "margin", "cost", "price", "order",
        "invoice", "payment", "discount", "customer", "account",
    )
    if any(kw in field_names for kw in _CORPORATE_KW):
        return "corporate-blue"

    # Marketing / consumer data → vibrant
    _VIBRANT_KW = (
        "click", "impression", "campaign", "conversion", "ad",
        "visitor", "bounce", "session", "pageview", "engagement",
        "subscriber", "follower", "reach", "brand",
    )
    if any(kw in field_names for kw in _VIBRANT_KW):
        return "vibrant"

    # Ops / monitoring data → dark
    _DARK_KW = (
        "sensor", "uptime", "latency", "error", "cpu", "memory",
        "throughput", "request", "response", "alert", "incident",
        "log", "metric", "node", "cluster",
    )
    if any(kw in field_names for kw in _DARK_KW):
        return "dark"

    # Environmental / sustainability data → minimal
    _MINIMAL_KW = (
        "emission", "carbon", "energy", "renewable", "waste",
        "recycle", "sustain", "environment", "green", "climate",
    )
    if any(kw in field_names for kw in _MINIMAL_KW):
        return "minimal"

    return "modern-light"


def _derive_title(schema: ClassifiedSchema) -> str:
    """Derive a dashboard title from the file path."""
    from pathlib import Path

    name = Path(schema.file_path).stem
    # Convert snake_case/kebab-case to title case
    title = name.replace("_", " ").replace("-", " ").title()
    return f"{title} Dashboard"
