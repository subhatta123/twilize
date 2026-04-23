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
    text_format: dict | None = field(default=None)  # {"AVG(Margin)": "0%"}
    label_runs: list[dict] | None = field(default=None)  # Rich-text label runs for KPI cards
    required: bool = False  # True → user-requested, never trimmed/dedup-dropped


@dataclass
class DashboardSuggestion:
    """A complete dashboard suggestion with multiple charts."""

    charts: list[ChartSuggestion] = field(default_factory=list)
    layout: str = "grid"  # "grid", "vertical", "horizontal"
    title: str = ""
    template: str = ""  # Named layout template (e.g., "executive-summary", "overview")
    layout_dict: dict | None = field(default=None)  # Custom FlexNode layout from image


def suggest_charts(
    schema: ClassifiedSchema,
    max_charts: int = 14,
    rules: dict | None = None,
    required_charts: list[dict] | None = None,
) -> DashboardSuggestion:
    """Suggest charts based on the classified schema.

    Uses rule-based heuristics to pick chart types that best represent
    the data shape.

    Args:
        schema: Classified CSV schema with dimension/measure/temporal info.
        max_charts: Maximum number of charts to suggest.
        rules: Dashboard rules dict (from ``dashboard_rules.load_rules``).
            When *None*, the built-in defaults are loaded automatically.
        required_charts: Optional list of user-specified chart specs that MUST
            appear in the final suggestion. Each entry is a dict like::

                {"title": "Top 10 Customers by Profit",
                 "kind": "bar",
                 "rows": "Customer Name",
                 "columns": "SUM(Profit)",
                 "top_n": 10, "top_by": "SUM(Profit)",
                 "sort_descending": "SUM(Profit)"}

            Required charts bypass trim/dedup and are always included.

    Returns:
        DashboardSuggestion with prioritized chart list.
    """
    if rules is None:
        from twilize.dashboard_rules import get_default_rules
        rules = get_default_rules()

    # Build required-chart suggestions up front (not subject to story-score
    # override or dedup removal — they carry required=True as a sentinel).
    required_suggestions: list[ChartSuggestion] = []
    if required_charts:
        for i, spec in enumerate(required_charts):
            cs = build_required_chart_suggestion(spec, index=i)
            if cs:
                required_suggestions.append(cs)

    suggestions: list[ChartSuggestion] = []

    dims = schema.dimensions
    measures = schema.measures
    temporal = schema.temporal
    geographic = schema.geographic

    # --- Filter out identifier fields from dimensions ---
    # ID fields (StudentID, OrderID, etc.) provide no analytical insight as
    # chart axes. They inflate cardinality and produce unreadable charts.
    dims = [d for d in dims if not _is_id_field(d.spec.name)]

    # --- Always add KPI summaries for top measures ---
    # Generate KPIs up to the limit set in rules (fills KPI row)
    from twilize.dashboard_rules import kpi_max
    from twilize.rules_inference import infer_kpi_number_format, infer_aggregation
    kpi_limit = min(kpi_max(rules), len(measures))
    for m in measures[:kpi_limit]:
        agg = infer_aggregation(m.spec.name, rules) or smart_aggregation(m.spec.name)
        kpi_title = _kpi_title(agg, m.spec.name)
        fmt_str = infer_kpi_number_format(m.spec.name, agg, rules)
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

    # --- Categorical dim + Measure → Bar charts (multiple dimensions) ---
    # Generate bar charts for multiple categorical dimensions to provide
    # richer analysis (e.g., by Category, Sub-Category, Region, Segment).
    # Include moderate-to-high cardinality dims with Top N filter.
    from twilize.dashboard_rules import bar_top_n as _bar_top_n
    _top_n = _bar_top_n(rules)
    bar_dims = [d for d in dims if d.semantic_type == "categorical"
                and d not in temporal and d not in geographic
                and d.spec.cardinality <= 50]
    if bar_dims and measures:
        for dim in bar_dims[:4]:
            m = measures[0]
            agg = smart_aggregation(m.spec.name)
            bar_title = (
                f"Top {_top_n} {dim.spec.name} by {m.spec.name}"
                if dim.spec.cardinality > _top_n
                else f"{m.spec.name} by {dim.spec.name}"
            )
            bar_text_fmt = None
            if _is_rate_field(m.spec.name):
                bar_text_fmt = {f"{agg}({m.spec.name})": "0%"}
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
            if dim.spec.cardinality > _top_n:
                bar_chart.top_n = {"field": dim.spec.name, "n": _top_n, "by": f"{agg}({m.spec.name})"}
            bar_chart.sort_descending = f"{agg}({m.spec.name})"
            suggestions.append(bar_chart)

    # --- Two measures → Scatter plot (with strict best-practice guards) ---
    # Best practice: scatter plots need ≥15 distinct visual data points and
    # should NOT use identifier fields as detail/color dimensions.
    if len(measures) >= 2:
        m1, m2 = measures[0], measures[1]
        agg1 = smart_aggregation(m1.spec.name)
        agg2 = smart_aggregation(m2.spec.name)

        # Skip scatter when both measures use the same aggregation on ID-like
        # fields — this produces meaningless COUNTD vs COUNTD plots.
        if not (agg1 == "COUNTD" and agg2 == "COUNTD"):
            shelves = [
                ShelfAssignment(m1.spec.name, "columns", agg1),
                ShelfAssignment(m2.spec.name, "rows", agg2),
            ]
            # Add detail dimension for scatter granularity + color for grouping
            # Use a moderate-cardinality dimension to avoid overplotting.
            # NEVER use ID fields as detail — they produce unreadable clouds.
            scatter_detail_dim = None
            if cat_dims:
                all_non_geo = [d for d in dims if d.semantic_type == "categorical"
                               and d not in temporal and d not in geographic
                               and not _is_id_field(d.spec.name)]
                if all_non_geo:
                    # For detail, prefer moderate cardinality (15-100) for readable scatter
                    good_detail = [d for d in all_non_geo if 15 <= d.spec.cardinality <= 100]
                    if good_detail:
                        scatter_detail_dim = good_detail[0]
                    elif any(d.spec.cardinality >= 6 for d in all_non_geo):
                        scatter_detail_dim = max(
                            [d for d in all_non_geo if d.spec.cardinality >= 6],
                            key=lambda d: d.spec.cardinality,
                        )
                    if scatter_detail_dim:
                        shelves.append(ShelfAssignment(scatter_detail_dim.spec.name, "detail"))
                # Color: only if ≥4 and ≤12 categories (too few = meaningless clusters)
                non_id_cats = [d for d in cat_dims if not _is_id_field(d.spec.name)]
                if non_id_cats and 4 <= non_id_cats[0].spec.cardinality <= 12:
                    shelves.append(ShelfAssignment(non_id_cats[0].spec.name, "color"))
            # Format rate fields on axes
            scatter_fmt = {}
            if _is_rate_field(m1.spec.name):
                scatter_fmt[f"{agg1}({m1.spec.name})"] = "0%"
            if _is_rate_field(m2.spec.name):
                scatter_fmt[f"{agg2}({m2.spec.name})"] = "0%"
            suggestions.append(ChartSuggestion(
                chart_type="Scatterplot",
                title=f"{m2.spec.name} vs {m1.spec.name}",
                shelves=shelves,
                reason="Two numeric measures → scatter plot (≥15 points required)",
                priority=70,
                text_format=scatter_fmt or None,
            ))

    # --- Rate measure × Amount measure → relationship scatter ---
    # E.g., Profit by Discount — reveals how discounting affects profitability
    rate_measures = [m for m in measures if _is_rate_field(m.spec.name)]
    amount_measures = [m for m in measures if _is_currency_field(m.spec.name)]
    if rate_measures and amount_measures:
        rate_m = rate_measures[0]
        # Prefer a secondary amount (e.g. Profit) over the primary (Sales)
        amt_m = amount_measures[-1] if len(amount_measures) > 1 else amount_measures[0]
        rate_agg = smart_aggregation(rate_m.spec.name)
        amt_agg = smart_aggregation(amt_m.spec.name)
        if not (rate_agg == "COUNTD" and amt_agg == "COUNTD"):
            rel_shelves = [
                ShelfAssignment(rate_m.spec.name, "columns", rate_agg),
                ShelfAssignment(amt_m.spec.name, "rows", amt_agg),
            ]
            # Add detail dim for scatter granularity
            detail_candidates = [d for d in (bar_dims if bar_dims else cat_dims)
                                 if not _is_id_field(d.spec.name)
                                 and 6 <= d.spec.cardinality <= 50]
            if detail_candidates:
                rel_shelves.append(ShelfAssignment(detail_candidates[0].spec.name, "detail"))
            suggestions.append(ChartSuggestion(
                chart_type="Scatterplot",
                title=f"{amt_m.spec.name} by {rate_m.spec.name}",
                shelves=rel_shelves,
                reason="Rate measure vs amount measure → relationship scatter",
                priority=68,
                text_format={f"{rate_agg}({rate_m.spec.name})": "0%"},
            ))

    # --- Count/Quantity measure → distribution bar ---
    count_measures = [m for m in measures
                      if any(kw in m.spec.name.lower()
                             for kw in ("quantity", "qty", "count", "num"))]
    if count_measures and cat_dims:
        q_m = count_measures[0]
        # Use a different dim than the primary bar's best dim
        q_dim = cat_dims[-1] if len(cat_dims) > 1 else cat_dims[0]
        q_agg = smart_aggregation(q_m.spec.name)
        suggestions.append(ChartSuggestion(
            chart_type="Bar",
            title=f"{q_m.spec.name} by {q_dim.spec.name}",
            shelves=[
                ShelfAssignment(q_dim.spec.name, "rows"),
                ShelfAssignment(q_m.spec.name, "columns", q_agg),
            ],
            reason="Count/quantity measure distribution across categories",
            priority=65,
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
    from twilize.dashboard_rules import pie_max_slices as _pie_max_slices
    small_cat = [d for d in cat_dims if d.spec.cardinality <= _pie_max_slices(rules)]
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

    # --- Categorical dim + multiple measures → grouped bar ---
    # Prefer currency/amount pairs (e.g. Sales & Profit) for richer comparison
    if cat_dims and len(measures) >= 2:
        dim = _best_categorical_dim(cat_dims)
        currency_ms = [m for m in measures if _is_currency_field(m.spec.name)]
        if len(currency_ms) >= 2:
            m1, m2 = currency_ms[0], currency_ms[1]
        else:
            m1, m2 = measures[0], measures[1]
        suggestions.append(ChartSuggestion(
            chart_type="Bar",
            title=f"{m1.spec.name} & {m2.spec.name} by {dim.spec.name}",
            shelves=[
                ShelfAssignment(dim.spec.name, "rows"),
                ShelfAssignment(m1.spec.name, "columns", smart_aggregation(m1.spec.name)),
                ShelfAssignment(m2.spec.name, "columns", smart_aggregation(m2.spec.name)),
            ],
            reason="Category with multiple measures → grouped bar chart",
            priority=78,
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
        s.priority = _story_score(s.chart_type, schema, s.shelves, rules)

    # Sort by story score (required-chart priorities are preserved separately
    # and injected after dedup / trim).
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

    # Save the full ranked list before dedup for space-filling later
    all_ranked = list(suggestions)

    # --- Best practice: deduplicate chart types ---
    # Only one chart per non-KPI type to maximise dashboard variety.
    # Required charts are always preserved (never dropped by dedup/trim).
    suggestions = deduplicate_charts(suggestions, max_per_type=1)
    # Reserve slots for required charts when trimming auto-suggestions.
    auto_budget = max(0, max_charts - len(required_suggestions))
    suggestions = suggestions[:auto_budget]

    # --- Fill available space: if template has empty slots, add more charts ---
    # When fill_available_space is enabled, check if the number of non-KPI
    # charts is less than the template capacity.  If so, pull in additional
    # unique charts from the full ranked list (before dedup) to fill the gap
    # with the next-strongest signal.
    fill_space = (rules or {}).get("charts", {}).get("fill_available_space", False)
    if fill_space:
        n_kpis = sum(1 for s in suggestions if s.chart_type == "Text")
        n_charts = len(suggestions) - n_kpis
        # Template capacity: 4 slots for C2/C3, 3 for C4/C5
        slot_capacity = 4 if n_charts > 3 else 3
        # After initial dedup we may have bumped down to 3; re-check with 4
        if n_charts <= 3:
            slot_capacity = 4  # optimistically target the larger template
        gap = slot_capacity - n_charts
        if gap > 0:
            # Build set of existing chart signatures to avoid true duplicates
            existing_sigs = set()
            for s in suggestions:
                sig = frozenset(
                    (sh.field_name, sh.shelf, sh.aggregation) for sh in s.shelves
                )
                existing_sigs.add(sig)
            # Walk the full ranked list and pick novel non-KPI charts
            for candidate in all_ranked:
                if gap <= 0:
                    break
                if candidate.chart_type == "Text":
                    continue
                sig = frozenset(
                    (sh.field_name, sh.shelf, sh.aggregation)
                    for sh in candidate.shelves
                )
                if sig not in existing_sigs:
                    suggestions.append(candidate)
                    existing_sigs.add(sig)
                    gap -= 1
                    logger.info(
                        "Fill space: added '%s' (%s, score=%d)",
                        candidate.title, candidate.chart_type, candidate.priority,
                    )
            suggestions = suggestions[:auto_budget]

    # Prepend required charts so they always appear, even when max_charts
    # would otherwise trim them out.
    if required_suggestions:
        suggestions = required_suggestions + suggestions

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
        # User-required charts always pass through dedup untouched.
        if getattr(s, "required", False):
            deduped.append(s)
            continue
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
        # Allow more KPI/Text charts since they're compact and high-value.
        # Bar charts get a higher limit to show different dimensions
        # (e.g., by Category, Region, Segment). Scatter gets 2 for
        # general correlation + rate-vs-amount relationship charts.
        if ct == "Text":
            effective_max = max(4, max_per_type)
        elif ct == "Bar":
            effective_max = max(4, max_per_type)
        elif ct == "Scatterplot":
            effective_max = max(2, max_per_type)
        else:
            effective_max = max_per_type
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

_ID_FIELD_PATTERNS = {
    "id", "key", "code", "identifier", "uuid", "guid",
    "student_id", "studentid", "order_id", "orderid",
    "customer_id", "customerid", "product_id", "productid",
    "employee_id", "employeeid", "user_id", "userid",
    "transaction_id", "transactionid", "record_id", "recordid",
}


def _is_id_field(field_name: str) -> bool:
    """Return True if the field is an identifier (ID/key) that should not be
    used as a chart dimension.

    ID fields produce unreadable charts (e.g., StudentID on a bar chart axis
    with hundreds of unique values) and offer no analytical insight.
    """
    lower = field_name.lower().replace(" ", "_")
    # Exact match on known ID patterns
    if lower in _ID_FIELD_PATTERNS:
        return True
    # Suffix match: ends with "id", "_id", "key", "_key", "code", "_code"
    for suffix in ("id", "_id", "key", "_key", "code", "_code", "uuid", "_uuid"):
        if lower.endswith(suffix) and len(lower) > len(suffix):
            return True
    return False


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


# ── Required-chart spec conversion ──────────────────────────────────

_KIND_TO_MARK_TYPE = {
    "bar": "Bar",
    "line": "Line",
    "area": "Area",
    "scatter": "Scatterplot",
    "scatterplot": "Scatterplot",
    "pie": "Pie",
    "map": "Map",
    "heatmap": "Heatmap",
    "tree_map": "Tree Map",
    "treemap": "Tree Map",
    "text": "Text",
    "kpi": "Text",
    "gantt": "Gantt Bar",
    "circle": "Circle",
    "square": "Square",
    "automatic": "Automatic",
}


def _parse_field_expr(expr: str) -> tuple[str, str]:
    """Split an expression like ``SUM(Sales)`` into ``(field, aggregation)``.

    Returns ``(field_name, "")`` for bare dimension expressions.
    Supported aggregations: ``SUM``, ``AVG``, ``MIN``, ``MAX``, ``COUNT``,
    ``COUNTD``, ``MEDIAN``, ``STDEV``, ``VAR``, ``ATTR``.
    """
    import re as _re

    if not isinstance(expr, str):
        return ("", "")
    s = expr.strip()
    if not s:
        return ("", "")
    m = _re.match(
        r"^(SUM|AVG|MIN|MAX|COUNT|COUNTD|MEDIAN|STDEV|VAR|ATTR)\s*\(\s*(.+?)\s*\)\s*$",
        s, flags=_re.IGNORECASE,
    )
    if m:
        return (m.group(2).strip(), m.group(1).upper())
    return (s, "")


def build_required_chart_suggestion(
    spec: dict,
    index: int = 0,
) -> ChartSuggestion | None:
    """Convert a user-supplied chart spec dict into a ``ChartSuggestion``.

    Accepted keys (all optional unless noted):
        kind (str):   "bar", "line", "scatter", "pie", "map", "heatmap",
                      "tree_map", "text"/"kpi". Defaults to "bar" when at
                      least one measure/dim is given, else "Automatic".
        title (str):  Worksheet title; auto-generated from shelves if omitted.
        columns (str | list[str]): Columns-shelf field expressions
                                   (e.g. "SUM(Profit)").
        rows (str | list[str]):    Rows-shelf field expressions.
        color (str):  Color-shelf field expression.
        size (str):   Size-shelf field expression.
        label (str):  Label-shelf field expression.
        detail (str): Detail-shelf field expression.
        top_n (int):  Top-N filter count.
        top_by (str): Measure expression used to rank Top-N
                      (defaults to the first measure in columns/rows).
        top_field (str): Dimension to apply Top-N on
                         (defaults to first dimension in rows/columns).
        sort_descending (str): Measure expression to sort descending by.
        text_format (dict): Tableau number-format overrides
                            (e.g. {"SUM(Profit)": "$#,##0"}).
        reason (str): Human explanation shown in the manifest.

    Returns ``None`` when the spec is structurally unusable
    (e.g. no shelves and no kind).
    """
    if not isinstance(spec, dict):
        return None

    kind = str(spec.get("kind") or spec.get("chart_type") or "").strip().lower()
    mark_type = _KIND_TO_MARK_TYPE.get(kind, "Bar" if kind == "" else kind.title())

    shelves: list[ShelfAssignment] = []

    def _add(shelf_name: str, value):
        if value is None:
            return
        items = value if isinstance(value, list) else [value]
        for item in items:
            field_name, agg = _parse_field_expr(str(item))
            if field_name:
                shelves.append(ShelfAssignment(field_name, shelf_name, agg))

    _add("columns", spec.get("columns") or spec.get("cols"))
    _add("rows", spec.get("rows"))
    _add("color", spec.get("color"))
    _add("size", spec.get("size"))
    _add("label", spec.get("label"))
    _add("detail", spec.get("detail"))

    if not shelves:
        logger.warning("Required chart spec #%d has no shelves; skipping", index)
        return None

    # Title fallback
    title = str(spec.get("title") or "").strip()
    if not title:
        primary_measure = next(
            (sh for sh in shelves if sh.aggregation), None
        )
        primary_dim = next(
            (sh for sh in shelves
             if sh.shelf in ("rows", "columns") and not sh.aggregation),
            None,
        )
        if primary_measure and primary_dim:
            title = f"{primary_measure.aggregation}({primary_measure.field_name}) by {primary_dim.field_name}"
        elif primary_measure:
            title = f"{primary_measure.aggregation}({primary_measure.field_name})"
        else:
            title = f"Required Chart {index + 1}"

    # Top-N filter
    top_n_dict = None
    top_n = spec.get("top_n")
    if isinstance(top_n, int) and top_n > 0:
        top_field = spec.get("top_field") or ""
        if not top_field:
            # First dimension on rows, then columns
            for sh in shelves:
                if sh.shelf in ("rows", "columns") and not sh.aggregation:
                    top_field = sh.field_name
                    break
        top_by = spec.get("top_by") or ""
        if not top_by:
            first_measure = next(
                (sh for sh in shelves if sh.aggregation), None
            )
            if first_measure:
                top_by = f"{first_measure.aggregation}({first_measure.field_name})"
        if top_field and top_by:
            top_n_dict = {"field": top_field, "n": int(top_n), "by": top_by}

    sort_desc = str(spec.get("sort_descending") or "").strip()
    if not sort_desc and top_n_dict:
        # When Top N is active, default to sorting by the same measure.
        sort_desc = top_n_dict["by"]

    text_format = spec.get("text_format")
    if text_format is not None and not isinstance(text_format, dict):
        text_format = None

    return ChartSuggestion(
        chart_type=mark_type,
        title=title,
        shelves=shelves,
        reason=str(spec.get("reason") or "User-required chart"),
        priority=1000,  # high, but the required=True flag is what protects it
        top_n=top_n_dict,
        sort_descending=sort_desc,
        text_format=text_format,
        required=True,
    )


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
    """Choose a Tableau number format for KPI values.

    Always uses 2 decimal places (no K/M abbreviation):
    - Rate/percentage fields → ``0.00%``
    - Currency fields → ``$#,##0.00``
    - Count/quantity fields → ``#,##0``
    - Other fields → ``#,##0.00``
    """
    if _is_rate_field(field_name):
        return "0.00%"

    lower = field_name.lower()

    # Count/quantity fields are usually small enough for full display
    _COUNT_KW = {"quantity", "count", "number", "num", "qty", "units",
                 "items", "orders", "transactions"}
    if any(kw in lower for kw in _COUNT_KW) or aggregation in ("COUNT", "COUNTD"):
        return "#,##0"

    # Currency fields — whole dollars
    _CURRENCY_KW = {"sales", "revenue", "price", "cost", "profit", "amount",
                    "income", "expense", "margin", "fee", "payment", "budget",
                    "spend", "earning"}
    if any(kw in lower for kw in _CURRENCY_KW):
        return "$#,##0"

    # Default: integer with commas
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
    rules: dict | None = None,
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
        # Grouped bars (multiple measures on columns) are more insightful
        col_measures = [sh for sh in shelves if sh.shelf == "columns" and sh.aggregation]
        if len(col_measures) >= 2:
            return 88  # Multi-measure comparison (e.g. Sales & Profit by Category)
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
                color_name = color_dims[0].field_name
                # Reject scatter if the detail/color field is an ID field
                if _is_id_field(color_name):
                    return 10  # ID-based scatter is never meaningful
                color_col = next(
                    (d for d in cat_dims if d.spec.name == color_name), None
                )
                visual_points = color_col.spec.cardinality if color_col else schema.row_count
            else:
                visual_points = schema.row_count

            from twilize.dashboard_rules import scatter_min_points as _scatter_min
            _min_pts = _scatter_min(rules) if rules else 15
            if visual_points >= _min_pts:
                return 72  # Enough points for correlation (lower than bar/line)
            elif visual_points >= 6:
                return 50  # Marginal — small scatter
            return 20  # Too few visual points — use bar chart instead
        return 15

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
