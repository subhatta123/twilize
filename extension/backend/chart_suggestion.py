"""LLM-based and rule-based chart suggestion for the extension.

Provides dashboard plan generation from field schema + user prompt +
optional image analysis. Falls back to rule-based suggestion when
no LLM API key is configured.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from cwtwb.chart_suggester import (
    ChartSuggestion,
    DashboardSuggestion,
    ShelfAssignment,
    smart_aggregation,
    suggest_charts,
)
from cwtwb.csv_to_hyper import ClassifiedSchema
from cwtwb.dashboard_enhancements import validate_suggestion
from cwtwb.viz_best_practices import BEST_PRACTICES_PROMPT

from .schema_inference import TableauField, classify_tableau_fields

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Geo-field detection for rule-based engine
# ---------------------------------------------------------------------------

_GEO_KEYWORDS = {"state", "city", "country", "region", "province", "zip", "postal", "latitude", "longitude", "lat", "lng", "lon", "county", "district", "territory"}


def _is_geo_field(field_name: str) -> bool:
    """Check if a field name suggests geographical data."""
    return any(kw in field_name.lower() for kw in _GEO_KEYWORDS)


def suggest_dashboard(
    fields: list[TableauField],
    row_count: int = 0,
    prompt: str = "",
    image_analysis: dict | None = None,
    max_charts: int = 5,
    sample_rows: list[list[Any]] | None = None,
) -> dict:
    """Generate a dashboard plan from field schema and user prompt.

    Tries LLM-based suggestion first (if ANTHROPIC_API_KEY or
    OPENAI_API_KEY is set). Falls back to rule-based engine.

    Args:
        fields: List of Tableau field descriptors.
        row_count: Total row count in the data.
        prompt: User's natural-language dashboard description.
        image_analysis: Optional layout extracted from reference image.
        max_charts: Maximum charts to suggest.
        sample_rows: Optional sample data rows for statistical analysis.

    Returns:
        Dashboard plan dict with charts, layout, and title.
    """
    classified = classify_tableau_fields(fields, row_count, sample_rows=sample_rows)

    # Compute field statistics from sample data if available
    field_stats = _compute_field_stats(fields, sample_rows) if sample_rows else {}

    _llm_warning: str | None = None  # Set if LLM fails, surfaced to user

    # LLM-first: try LLM for ALL cases when API key is available
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
    logger.warning("API key check: ANTHROPIC=%s, OPENAI=%s, using_llm=%s",
                   bool(os.environ.get("ANTHROPIC_API_KEY")),
                   bool(os.environ.get("OPENAI_API_KEY")),
                   bool(api_key))
    if api_key:
        effective_prompt = (
            prompt if prompt and prompt.strip()
            else "Analyze this data and create the best possible dashboard with KPIs, charts, and filters"
        )
        try:
            result = _llm_suggest(
                classified, effective_prompt, image_analysis, max_charts, api_key,
                field_stats=field_stats, sample_rows=sample_rows,
            )
            # Truncate to max_charts
            if "charts" in result:
                result["charts"] = result["charts"][:max_charts]
            # Validate through the standard pipeline
            suggestion = dict_to_suggestion(result)
            suggestion = validate_suggestion(suggestion, classified, max_charts)

            # Fill remaining slots if LLM returned fewer than max_charts
            if len(suggestion.charts) < max_charts:
                suggestion = _fill_remaining_slots(suggestion, classified, max_charts)

            validated = _suggestion_to_dict(suggestion)
            # Preserve any extra keys from LLM result (theme_colors, etc.)
            for k in result:
                if k not in validated:
                    validated[k] = result[k]
            return validated
        except Exception as exc:
            logger.error("LLM suggestion failed, falling back to rules: %s", exc, exc_info=True)
            # Detect specific API errors and surface warnings
            exc_msg = str(exc)
            if "credit balance is too low" in exc_msg or "insufficient_quota" in exc_msg:
                _llm_warning = ("Your Anthropic API key has no credits remaining. "
                                "Please add credits at https://console.anthropic.com/settings/billing. "
                                "Falling back to rule-based suggestions (limited chart variety).")
            elif "invalid_api_key" in exc_msg or "401" in exc_msg:
                _llm_warning = ("Your API key is invalid or expired. "
                                "Please update it in API Key Settings. "
                                "Falling back to rule-based suggestions.")
            else:
                _llm_warning = (f"AI suggestion failed: {exc_msg[:100]}. "
                                "Falling back to rule-based suggestions (limited chart variety).")

    # Rule-based fallback (no API key or LLM failed)
    # Also fill remaining slots for rule-based results
    if image_analysis and image_analysis.get("panels"):
        suggestion = _image_guided_suggest(classified, image_analysis, max_charts)
        suggestion = validate_suggestion(suggestion, classified, max_charts)
        result = _suggestion_to_dict(suggestion)
        # Attach image-extracted colors for theme application
        color_scheme = image_analysis.get("color_scheme", [])
        if color_scheme:
            result["theme_colors"] = color_scheme
        if _llm_warning:
            result["_warning"] = _llm_warning
        return result

    from cwtwb.chart_suggester import _auto_detect_theme

    # If the user provided a prompt, parse it for chart type / field hints
    if prompt and prompt.strip():
        suggestion = _prompt_guided_suggest(classified, prompt, max_charts)
    else:
        suggestion = suggest_charts(classified, max_charts=max_charts)

    # Post-process: geo-field detection, top-N, and chart diversity
    suggestion = _apply_rule_enhancements(suggestion, classified, field_stats)

    suggestion = validate_suggestion(suggestion, classified, max_charts)

    # Fill remaining slots to ensure enough charts for the layout
    if len(suggestion.charts) < max_charts:
        suggestion = _fill_remaining_slots(suggestion, classified, max_charts)

    # Ensure chart type diversity in final suggestion
    suggestion = _ensure_chart_diversity(suggestion)

    result = _suggestion_to_dict(suggestion)
    result["theme"] = _auto_detect_theme(classified)
    if _llm_warning:
        result["_warning"] = _llm_warning
    return result


def _fill_remaining_slots(
    suggestion: DashboardSuggestion,
    classified: ClassifiedSchema,
    max_charts: int,
) -> DashboardSuggestion:
    """Fill empty chart slots with auto-suggested charts to fill the dashboard.

    The dashboard must have enough charts to fill the layout (typically 7-8).
    If the LLM or rule engine produced fewer, auto-generate complementary charts.
    """
    if len(suggestion.charts) >= max_charts:
        return suggestion

    auto = suggest_charts(classified, max_charts=max_charts)
    existing_sigs = {
        frozenset(sh.field_name for sh in c.shelves) for c in suggestion.charts
    }
    new_charts = list(suggestion.charts)
    for ac in auto.charts:
        if len(new_charts) >= max_charts:
            break
        ac_sig = frozenset(sh.field_name for sh in ac.shelves)
        if ac_sig not in existing_sigs:
            new_charts.append(ac)
            existing_sigs.add(ac_sig)

    return DashboardSuggestion(
        charts=new_charts,
        layout=suggestion.layout,
        title=suggestion.title,
        template=suggestion.template,
        layout_dict=suggestion.layout_dict,
    )


def _apply_rule_enhancements(
    suggestion: DashboardSuggestion,
    classified: ClassifiedSchema,
    field_stats: dict | None = None,
) -> DashboardSuggestion:
    """Apply geo-field detection and top-N rules to rule-based suggestions.

    A) If a Bar chart uses a geographical field, swap it to Map (one map max).
    B) If a categorical field has >10 unique values, annotate title with 'Top 10'.
    C) If a categorical field has <=6 unique values and chart is Bar, prefer Pie.
    """
    measures = classified.measures
    new_charts: list[ChartSuggestion] = []
    has_map = any(c.chart_type == "Map" for c in suggestion.charts)
    has_pie = any(c.chart_type == "Pie" for c in suggestion.charts)

    for chart in suggestion.charts:
        if chart.chart_type == "Bar":
            # Check if any dimension shelf is a geo field
            dim_shelves = [s for s in chart.shelves if s.shelf in ("rows", "columns") and not s.aggregation]
            geo_dim = next((s for s in dim_shelves if _is_geo_field(s.field_name)), None)

            if geo_dim and not has_map and measures:
                # Swap to Map: geo field on detail, first measure on color
                measure_shelf = next(
                    (s for s in chart.shelves if s.aggregation),
                    None,
                )
                if measure_shelf:
                    new_shelves = [
                        ShelfAssignment(geo_dim.field_name, "detail"),
                        ShelfAssignment(measure_shelf.field_name, "color", measure_shelf.aggregation),
                    ]
                    new_charts.append(ChartSuggestion(
                        chart_type="Map",
                        title=f"{measure_shelf.field_name} by {geo_dim.field_name}",
                        shelves=new_shelves,
                        reason="Geo field detected — using Map chart",
                        priority=chart.priority,
                    ))
                    has_map = True
                    continue

            # Check cardinality for top-N annotation
            if dim_shelves and field_stats:
                dim_name = dim_shelves[0].field_name
                # Look up cardinality from classified schema
                col_spec = next(
                    (c for c in classified.columns if c.spec.name == dim_name),
                    None,
                )
                if col_spec and col_spec.spec.cardinality > 10:
                    # Annotate title with Top 10
                    title = chart.title
                    if "top" not in title.lower():
                        title = f"Top 10 {dim_name} by {chart.title.split('by')[-1].strip() if 'by' in chart.title else title}"
                    new_charts.append(ChartSuggestion(
                        chart_type=chart.chart_type,
                        title=title,
                        shelves=chart.shelves,
                        reason=chart.reason,
                        priority=chart.priority,
                    ))
                    continue

            # Check if low-cardinality dim should be Pie instead of Bar
            if dim_shelves and not has_pie:
                dim_name = dim_shelves[0].field_name
                col_spec = next(
                    (c for c in classified.columns if c.spec.name == dim_name),
                    None,
                )
                if col_spec and col_spec.spec.cardinality <= 6:
                    measure_shelf = next(
                        (s for s in chart.shelves if s.aggregation), None,
                    )
                    if measure_shelf:
                        new_charts.append(ChartSuggestion(
                            chart_type="Pie",
                            title=f"{measure_shelf.field_name} by {dim_name}",
                            shelves=[
                                ShelfAssignment(measure_shelf.field_name, "size", measure_shelf.aggregation),
                                ShelfAssignment(dim_name, "color"),
                            ],
                            reason="Low-cardinality dimension — using Pie chart",
                            priority=chart.priority,
                        ))
                        has_pie = True
                        continue

        new_charts.append(chart)

    return DashboardSuggestion(
        charts=new_charts,
        layout=suggestion.layout,
        title=suggestion.title,
        template=suggestion.template,
        layout_dict=suggestion.layout_dict,
    )


def _ensure_chart_diversity(
    suggestion: DashboardSuggestion,
) -> DashboardSuggestion:
    """Ensure no chart type (except Text/KPI) is used more than twice.

    When duplicates exceed 2, cycle through alternative types to improve
    visual diversity on the dashboard.
    """
    _DIVERSITY_CYCLE = ["Bar", "Pie", "Line", "Scatterplot", "Heatmap", "Tree Map", "Area"]

    type_counts: dict[str, int] = {}
    new_charts: list[ChartSuggestion] = []

    for chart in suggestion.charts:
        ct = chart.chart_type
        if ct == "Text":
            # KPIs are exempt from diversity rules
            new_charts.append(chart)
            continue

        type_counts[ct] = type_counts.get(ct, 0) + 1
        if type_counts[ct] <= 2:
            new_charts.append(chart)
        else:
            # Find an alternative type not yet used twice
            replacement = None
            for alt in _DIVERSITY_CYCLE:
                if alt != ct and type_counts.get(alt, 0) < 2:
                    replacement = alt
                    break
            if replacement:
                type_counts[replacement] = type_counts.get(replacement, 0) + 1
                new_charts.append(ChartSuggestion(
                    chart_type=replacement,
                    title=chart.title,
                    shelves=chart.shelves,
                    reason=f"Diversified from {ct} to {replacement}",
                    priority=chart.priority,
                ))
            else:
                # No alternatives left, keep original
                new_charts.append(chart)

    return DashboardSuggestion(
        charts=new_charts,
        layout=suggestion.layout,
        title=suggestion.title,
        template=suggestion.template,
        layout_dict=suggestion.layout_dict,
    )


def _llm_suggest(
    schema: ClassifiedSchema,
    prompt: str,
    image_analysis: dict | None,
    max_charts: int,
    api_key: str,
    field_stats: dict | None = None,
    sample_rows: list | None = None,
) -> dict:
    """Use an LLM to generate a dashboard plan."""
    # Build rich field descriptions with stats
    fields_desc = []
    for col in schema.columns:
        desc = (
            f"- {col.spec.name}: {col.role} ({col.semantic_type}, "
            f"type={col.spec.inferred_type}, cardinality={col.spec.cardinality})"
        )
        # Append statistical summary if available
        if field_stats and col.spec.name in field_stats:
            stats = field_stats[col.spec.name]
            stat_parts = []
            if "min" in stats:
                stat_parts.append(f"min={stats['min']}")
            if "max" in stats:
                stat_parts.append(f"max={stats['max']}")
            if "mean" in stats:
                stat_parts.append(f"mean={stats['mean']:.2f}")
            if "is_rate" in stats and stats["is_rate"]:
                stat_parts.append("LIKELY A RATE/PERCENTAGE → use AVG")
            if "top_values" in stats:
                stat_parts.append(f"top values: {stats['top_values']}")
            if stat_parts:
                desc += f"  [{', '.join(stat_parts)}]"
        fields_desc.append(desc)

    geo_fields = [c.spec.name for c in schema.columns if c.semantic_type == "geographic"]
    if geo_fields:
        # Check geographic data quality
        geo_col = next((c for c in schema.columns if c.semantic_type == "geographic"), None)
        total = (geo_col.spec.total_rows or schema.row_count) if geo_col else 0
        if geo_col and total > 0:
            null_ratio = geo_col.spec.null_count / total
            if null_ratio > 0.20:
                geo_note = (
                    f"\nGeographic fields: {', '.join(geo_fields)} — WARNING: "
                    f"{null_ratio:.0%} null/unknown values. Do NOT use Map charts. "
                    f"Use Bar or Tree Map instead for geographic breakdowns."
                )
            else:
                geo_note = f"\nGeographic fields: {', '.join(geo_fields)}"
        else:
            geo_note = f"\nGeographic fields: {', '.join(geo_fields)}"
    else:
        geo_note = "\nNo geographic fields — do NOT suggest Map charts."

    # Format sample data table if available
    sample_table = ""
    if sample_rows:
        sample_table = _format_sample_table(schema, sample_rows[:10])

    system_prompt = f"""{BEST_PRACTICES_PROMPT}

You are an expert data analyst and Tableau dashboard designer.
You must create a COMPREHENSIVE DASHBOARD PLAN before designing charts.

Follow this 5-step process:

STEP 1 — ANALYZE THE DATA:
Examine each field semantically and choose the correct aggregation:
- Rates/percentages (discount, margin, score, rating) → AVG
- Amounts (sales, profit, revenue, cost) → SUM
- Counts (quantity, orders, transactions) → SUM
- Identifiers (order id, customer key) → COUNTD (count distinct)

STEP 2 — PLAN THE DASHBOARD STRUCTURE:
Think like a senior analyst building a dashboard for stakeholders:
a) What are the 3-5 KEY BUSINESS QUESTIONS this data can answer?
b) Which KPIs should appear at the top? (EXACTLY 2-3 KPIs — no more! These are summary numbers only)
c) Which ANALYTICAL CHARTS best answer each question? You MUST include at least 3-4 analytical charts (Bar, Line, Pie, Scatter, etc.). The dashboard should be CHART-HEAVY, not KPI-heavy.
d) Which fields should be available as FILTERS for interactivity? (categorical fields with 3-20 unique values)
e) Are CALCULATED FIELDS needed? (e.g., Profit Margin = Profit/Sales, Year-over-Year growth)
f) The dashboard must FILL the available space — no empty areas. Use varied chart types for visual variety.

STEP 3 — DEFINE CALCULATED FIELDS (if needed):
If the data would benefit from derived metrics, define them.
Use Tableau formula syntax: e.g., [Profit]/[Sales] for ratios.

STEP 4 — DESIGN EXACTLY {max_charts} CHARTS (you MUST produce {max_charts} charts, no fewer!):
CRITICAL CHART MIX: Include EXACTLY 3 Text/KPI charts AND {max_charts - 3} analytical charts.
The dashboard MUST fill the entire screen — empty space is unacceptable.
Use DIFFERENT chart types for variety (Bar, Line, Pie, Heatmap, Scatterplot, Tree Map, Area).
Each chart must have a clear analytical purpose with insight-driven titles.
If you have fewer fields, reuse measures with different dimensions or aggregations to create enough charts.

CRITICAL RULES FOR CHART TYPE SELECTION:
- Follow the user's EXACT chart type requests. If they say "bar chart of X", create a Bar chart, NOT a Map.
- Do NOT assume geographic intent from words like "region", "country", "city", or "state" unless the user EXPLICITLY asks for a map, geographic visualization, or spatial analysis.
- "Top customers by region" means a Bar chart grouped by Region, NOT a Map.
- Only use Map charts when the user explicitly requests a map/geographic/spatial visualization, OR when the prompt clearly implies spatial analysis (e.g., "where are our sales concentrated?").
- Use Map chart type when fields are geographical (State, City, Country) AND the user asks for geographic/spatial visualization. Place the geo field on "detail" shelf and a measure on "color" shelf. Limit to ONE Map per dashboard.
- For Bar charts with >10 categories (high cardinality dimensions), show only the Top 10. Reflect this in the chart title (e.g., "Top 10 States by Profit").
- Prefer Pie charts for categorical fields with 6 or fewer unique values — they work well for parts-of-whole analysis.
- NEVER use the same chart type more than twice in a dashboard unless absolutely necessary. Vary chart types for visual diversity (Bar, Line, Pie, Scatterplot, Heatmap, Tree Map, Area).
- Include filter_fields in your plan for ALL categorical dimensions with 2-50 unique values that would make good interactive filters.
- When the user mentions specific fields or metrics, prioritize those in your chart designs over auto-inferred ones.

STEP 5 — SELECT THEME:
Choose the visual theme that best matches the data domain and user's intent.

Available fields:
{chr(10).join(fields_desc)}
{geo_note}

Row count: {schema.row_count}
{sample_table}

Supported chart types: Bar, Line, Scatterplot, Pie, Heatmap, Map, Tree Map, Text, Area
- Text = KPI summary (single aggregated number, 28pt+ font). MAX 2-3 per dashboard.
- Bar = Horizontal bars comparing categories. Great for "top N" analysis.
- Line = Temporal trends with dates on x-axis. ALWAYS include one if date fields exist.
- Pie = Parts of a whole. MAX 5-7 slices. Good for market share / composition.
- Heatmap = Two dimensions crossed with a color-encoded measure. Great for patterns.
- Scatterplot = Two measures compared. Great for correlation analysis.
- Area = Stacked area for composition over time.
- Maps = ONLY when lat/long or geocodable fields exist with good data quality.
- Tree Map = Hierarchical comparison by size and color.

Available layout templates:
- "executive-summary" — KPI row at top + chart grid below (most common, good default)
- "kpi-detail" — KPI row + focused featured chart
- "left-filter" — Left dark sidebar for filters + KPIs + charts (good for many filters)
- "featured-detail" — Large chart on left + stacked detail charts on right
- "comparison" — Side-by-side equal-weight charts
- "overview" — Featured chart on top + detail row below
- "grid" — Balanced grid layout (no KPI row)

Available themes:
- "modern-light" — Clean white/gray, Tableau 10 palette, general business data
- "corporate-blue" — Professional blue-gray, financial/sales data
- "dark" — Dark background, bright accents, monitoring/ops dashboards
- "minimal" — Pure white, muted greens, sustainability/environmental data
- "vibrant" — Bold colors, marketing/consumer dashboards

Respond with valid JSON only:
{{
  "title": "Dashboard title",
  "layout": "executive-summary",
  "theme": "corporate-blue",
  "plan": {{
    "questions": ["Question 1?", "Question 2?"],
    "kpi_count": 3,
    "filter_fields": ["Category", "Region"],
    "insights": ["Insight 1", "Insight 2"]
  }},
  "calculated_fields": [
    {{
      "name": "Profit Margin",
      "formula": "[Profit]/[Sales]",
      "description": "Profit as percentage of sales"
    }}
  ],
  "charts": [
    {{
      "chart_type": "Text",
      "title": "Total Sales",
      "shelves": [
        {{"field_name": "Sales", "shelf": "label", "aggregation": "SUM"}}
      ],
      "reason": "Key metric for immediate context",
      "priority": 95
    }},
    {{
      "chart_type": "Bar",
      "title": "Which Category is most profitable?",
      "shelves": [
        {{"field_name": "Category", "shelf": "rows", "aggregation": ""}},
        {{"field_name": "Profit", "shelf": "columns", "aggregation": "SUM"}}
      ],
      "reason": "Answers: Which category generates most profit?",
      "priority": 90
    }}
  ]
}}"""

    user_msg = prompt
    if image_analysis:
        user_msg += f"\n\nReference image layout: {json.dumps(image_analysis)}"

    # Try Anthropic first, then OpenAI
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        return _call_anthropic(system_prompt, user_msg, anthropic_key)

    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        return _call_openai(system_prompt, user_msg, openai_key)

    raise RuntimeError("No LLM API key available")


def _compute_field_stats(
    fields: list[TableauField],
    sample_rows: list[list[Any]] | None,
) -> dict[str, dict]:
    """Compute per-field statistics from sample data rows.

    For numeric fields: min, max, mean, is_rate (all values in [0,1] or [0,100]).
    For string fields: top 3 most frequent values.
    For date fields: min/max date strings.
    """
    if not sample_rows or not fields:
        return {}

    stats: dict[str, dict] = {}
    for col_idx, field in enumerate(fields):
        if col_idx >= len(sample_rows[0]) if sample_rows else True:
            continue

        values = []
        for row in sample_rows:
            if col_idx < len(row) and row[col_idx] is not None:
                values.append(row[col_idx])

        if not values:
            continue

        fname = field.name
        ftype = field.datatype

        if ftype in ("int", "float", "real"):
            # Numeric stats
            nums = []
            for v in values:
                try:
                    nums.append(float(str(v).replace(",", "")))
                except (ValueError, TypeError):
                    pass
            if nums:
                min_v = min(nums)
                max_v = max(nums)
                mean_v = sum(nums) / len(nums)
                # Detect rate-like fields: all values between 0 and 1
                is_rate = all(0 <= n <= 1 for n in nums)
                # Also check 0-100 range for percentage fields
                if not is_rate and max_v <= 100 and min_v >= 0:
                    lower_name = fname.lower()
                    if any(kw in lower_name for kw in ("pct", "percent", "rate", "ratio", "discount", "margin")):
                        is_rate = True
                stats[fname] = {
                    "min": round(min_v, 2),
                    "max": round(max_v, 2),
                    "mean": mean_v,
                    "is_rate": is_rate,
                }
        elif ftype in ("date", "date-time", "datetime"):
            str_vals = sorted(set(str(v) for v in values))
            if str_vals:
                stats[fname] = {
                    "min_date": str_vals[0],
                    "max_date": str_vals[-1],
                }
        else:
            # String/categorical — top values
            from collections import Counter
            counter = Counter(str(v) for v in values)
            top = counter.most_common(3)
            stats[fname] = {
                "top_values": ", ".join(f"{val} ({cnt})" for val, cnt in top),
            }

    return stats


def _format_sample_table(
    schema: ClassifiedSchema,
    rows: list[list[Any]],
) -> str:
    """Format sample data rows as a markdown table for the LLM prompt."""
    if not rows:
        return ""

    headers = [col.spec.name for col in schema.columns]
    # Limit to first 8 columns to keep prompt size manageable
    headers = headers[:8]

    lines = ["\nSample data (first rows):"]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")

    for row in rows[:10]:
        cells = []
        for i, h in enumerate(headers):
            val = row[i] if i < len(row) else ""
            cell = str(val)[:20] if val is not None else ""
            cells.append(cell)
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response text.

    Handles common LLM quirks:
    - Markdown code fences (```json ... ```)
    - Leading/trailing whitespace
    - Text before/after the JSON object
    """
    import re
    text = text.strip()
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip markdown code fences
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Find first { ... last }
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(text[first_brace:last_brace + 1])
        except json.JSONDecodeError:
            pass
    raise json.JSONDecodeError(f"Could not extract JSON from LLM response: {text[:200]}", text, 0)


def _call_anthropic(system_prompt: str, user_msg: str, api_key: str) -> dict:
    """Call Anthropic Claude API for suggestion."""
    import httpx

    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_msg}],
        },
        timeout=60.0,
    )
    response.raise_for_status()
    body = response.json()
    logger.info("Anthropic API response type: %s", body.get("type"))
    if body.get("type") == "error":
        raise RuntimeError(f"Anthropic API error: {body.get('error', {}).get('message', body)}")
    text = body["content"][0]["text"]
    logger.info("LLM raw response (first 200 chars): %s", text[:200])
    return _extract_json(text)


def _call_openai(system_prompt: str, user_msg: str, api_key: str) -> dict:
    """Call OpenAI API for suggestion."""
    import httpx

    response = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            "response_format": {"type": "json_object"},
        },
        timeout=30.0,
    )
    response.raise_for_status()
    text = response.json()["choices"][0]["message"]["content"]
    return _extract_json(text)


def _image_guided_suggest(
    schema: ClassifiedSchema,
    image_analysis: dict,
    max_charts: int,
) -> DashboardSuggestion:
    """Use image analysis panels as a template for rule-based suggestion.

    Maps each panel's chart_type from the image to the available fields,
    creating shelf assignments that match the image layout.
    """
    panels = image_analysis.get("panels", [])
    if not panels:
        return suggest_charts(schema, max_charts=max_charts)

    dims = schema.dimensions
    measures = schema.measures
    temporal = schema.temporal
    geographic = schema.geographic
    cat_dims = [d for d in dims if d.semantic_type == "categorical"]

    # Map image chart types to cwtwb mark types
    _IMAGE_TYPE_MAP = {
        "bar": "Bar",
        "line": "Line",
        "pie": "Pie",
        "scatter": "Scatterplot",
        "scatterplot": "Scatterplot",
        "map": "Map",
        "heatmap": "Heatmap",
        "treemap": "Tree Map",
        "text": "Text",
        "kpi": "Text",
        "area": "Area",
    }

    charts: list[ChartSuggestion] = []
    used_measures: list[int] = []  # track which measures have been used

    for i, panel in enumerate(panels[:max_charts]):
        raw_type = panel.get("chart_type", "bar").lower().strip()
        mark_type = _IMAGE_TYPE_MAP.get(raw_type, "Bar")
        desc = panel.get("description", f"Chart {i + 1}")

        shelves: list[ShelfAssignment] = []
        measure_idx = i % len(measures) if measures else 0

        if mark_type == "Line" and temporal and measures:
            time_col = temporal[0]
            m = measures[measure_idx % len(measures)]
            shelves = [
                ShelfAssignment(time_col.spec.name, "columns"),
                ShelfAssignment(m.spec.name, "rows", smart_aggregation(m.spec.name)),
            ]
            if cat_dims and "by" in desc.lower():
                shelves.append(ShelfAssignment(cat_dims[0].spec.name, "color"))
        elif mark_type == "Bar" and cat_dims and measures:
            dim = cat_dims[i % len(cat_dims)] if i < len(cat_dims) else cat_dims[0]
            m = measures[measure_idx % len(measures)]
            shelves = [
                ShelfAssignment(dim.spec.name, "rows"),
                ShelfAssignment(m.spec.name, "columns", smart_aggregation(m.spec.name)),
            ]
        elif mark_type == "Scatterplot" and len(measures) >= 2:
            m1 = measures[0]
            m2 = measures[1]
            shelves = [
                ShelfAssignment(m1.spec.name, "columns", smart_aggregation(m1.spec.name)),
                ShelfAssignment(m2.spec.name, "rows", smart_aggregation(m2.spec.name)),
            ]
            if cat_dims:
                shelves.append(ShelfAssignment(cat_dims[0].spec.name, "color"))
        elif mark_type == "Pie" and cat_dims and measures:
            dim = cat_dims[0]
            m = measures[measure_idx % len(measures)]
            shelves = [
                ShelfAssignment(m.spec.name, "size", smart_aggregation(m.spec.name)),
                ShelfAssignment(dim.spec.name, "color"),
            ]
        elif mark_type == "Map" and geographic and measures:
            geo = geographic[0]
            m = measures[measure_idx % len(measures)]
            shelves = [
                ShelfAssignment(geo.spec.name, "detail"),
                ShelfAssignment(m.spec.name, "color", smart_aggregation(m.spec.name)),
            ]
        elif mark_type == "Text" and measures:
            m = measures[measure_idx % len(measures)]
            shelves = [ShelfAssignment(m.spec.name, "label", smart_aggregation(m.spec.name))]
        elif mark_type == "Heatmap" and len(cat_dims) >= 2 and measures:
            m = measures[0]
            shelves = [
                ShelfAssignment(cat_dims[0].spec.name, "columns"),
                ShelfAssignment(cat_dims[1].spec.name, "rows"),
                ShelfAssignment(m.spec.name, "color", smart_aggregation(m.spec.name)),
            ]
        else:
            # Fallback: bar chart with first available dim + measure
            if cat_dims and measures:
                m = measures[measure_idx % len(measures)]
                shelves = [
                    ShelfAssignment(cat_dims[0].spec.name, "rows"),
                    ShelfAssignment(m.spec.name, "columns", smart_aggregation(m.spec.name)),
                ]
            elif measures:
                shelves = [ShelfAssignment(measures[0].spec.name, "label", smart_aggregation(measures[0].spec.name))]
                mark_type = "Text"

        if shelves:
            charts.append(ChartSuggestion(
                chart_type=mark_type,
                title=desc or f"{mark_type} Chart",
                shelves=shelves,
                reason=f"Matching reference image panel ({raw_type})",
                priority=100 - i,
            ))

    layout = image_analysis.get("layout_type", "grid")

    # Build spatial layout from panel positions
    from .image_analysis import build_layout_from_panels

    # Create placeholder worksheet names matching chart order
    ws_names = [_safe_ws_name(c.title, i) for i, c in enumerate(charts)]
    layout_dict = build_layout_from_panels(panels[:len(charts)], ws_names)

    # Extract color scheme for theme application
    color_scheme = image_analysis.get("color_scheme", [])

    suggestion = DashboardSuggestion(
        charts=charts,
        layout=layout,
        title="Dashboard",
        layout_dict=layout_dict,
    )

    return suggestion


def _safe_ws_name(title: str, index: int) -> str:
    """Create a safe worksheet name for layout mapping."""
    name = title[:50].strip()
    return name if name else f"Sheet {index + 1}"


# ---------------------------------------------------------------------------
# Prompt-guided rule-based suggestion (no LLM API key required)
# ---------------------------------------------------------------------------

# Map of keywords found in user prompts to cwtwb mark types
_PROMPT_CHART_MAP = {
    "bar": "Bar", "bar chart": "Bar", "horizontal bar": "Bar",
    "line": "Line", "line chart": "Line", "trend": "Line", "time series": "Line",
    "pie": "Pie", "pie chart": "Pie", "donut": "Pie",
    "scatter": "Scatterplot", "scatterplot": "Scatterplot", "correlation": "Scatterplot",
    "map": "Map", "geographic map": "Map", "geo map": "Map",
    "heatmap": "Heatmap", "heat map": "Heatmap",
    "treemap": "Tree Map", "tree map": "Tree Map",
    "kpi": "Text", "summary": "Text", "metric": "Text",
    "area": "Area", "area chart": "Area",
}


def _prompt_guided_suggest(
    schema: ClassifiedSchema,
    prompt: str,
    max_charts: int,
) -> DashboardSuggestion:
    """Parse a user's natural-language prompt and build charts accordingly.

    Extracts:
    * Requested chart types (bar, line, pie, map, etc.)
    * Field references (fuzzy match against schema field names)
    * Analytical intent (top, comparison, trend, distribution)

    Falls back to ``suggest_charts`` for any remaining slots.
    """
    import re
    from cwtwb.chart_suggester import _kpi_title

    prompt_lower = prompt.lower()

    dims = schema.dimensions
    measures = schema.measures
    temporal = schema.temporal
    geographic = schema.geographic
    cat_dims = [d for d in dims if d.semantic_type == "categorical"]

    # --- Step 1: Find chart types + nearby field context in the prompt ---
    # Split prompt by common separators to isolate per-chart phrases
    import re
    chart_requests: list[tuple[str, str]] = []  # (chart_type, context_phrase)
    # Split on commas, "and", periods, or "a/an" before chart keywords
    phrases = re.split(r'[,.]|\band\b|\ba\s+(?=bar|line|pie|scatter|map|heat|tree|kpi|area)', prompt_lower)

    sorted_keys = sorted(_PROMPT_CHART_MAP.keys(), key=len, reverse=True)
    for phrase in phrases:
        phrase = phrase.strip()
        if not phrase:
            continue
        for keyword in sorted_keys:
            if keyword in phrase:
                chart_type = _PROMPT_CHART_MAP[keyword]
                chart_requests.append((chart_type, phrase))
                break

    # If no chart types found in phrases, try full prompt
    if not chart_requests:
        for keyword in sorted_keys:
            if keyword in prompt_lower:
                chart_type = _PROMPT_CHART_MAP[keyword]
                chart_requests.append((chart_type, prompt_lower))

    # If STILL no chart types found, infer from analytical intent
    if not chart_requests:
        # "top X by Y", "X by Y" → Bar chart
        if re.search(r'(top|by|compare|highest|lowest)', prompt_lower):
            chart_requests.append(("Bar", prompt_lower))
        # "over time", "monthly", "trend" → Line chart
        if re.search(r'(over time|month|year|trend|daily|weekly)', prompt_lower):
            chart_requests.append(("Line", prompt_lower))
        # Fallback: just create a general dashboard
        if not chart_requests:
            chart_requests.append(("Bar", prompt_lower))

    # --- Step 2: Build field lookup from schema ---
    all_fields = {c.spec.name.lower(): c for c in schema.columns}
    # Also map individual words from multi-word field names
    word_to_field: dict[str, str] = {}
    for fname_lower, col in all_fields.items():
        words = fname_lower.replace("_", " ").replace("-", " ").split()
        for word in words:
            if len(word) >= 3:
                word_to_field[word] = col.spec.name

    def _fields_in_text(text: str) -> list[str]:
        """Find field names referenced in a text snippet."""
        found = []
        for word, field_name in word_to_field.items():
            if word in text and field_name not in found:
                found.append(field_name)
        return found

    # Global references for fallback
    all_referenced = _fields_in_text(prompt_lower)
    ref_measures = [f for f in all_referenced if any(m.spec.name == f for m in measures)]
    ref_dims = [f for f in all_referenced if any(d.spec.name == f for d in cat_dims)]

    # --- Step 3: Detect analytical intent keywords ---
    wants_top = any(kw in prompt_lower for kw in ("top", "highest", "best", "most", "largest", "biggest"))
    wants_trend = any(kw in prompt_lower for kw in ("trend", "over time", "month", "year", "weekly", "daily", "quarter"))

    # --- Step 4: Build charts from parsed intent ---
    charts: list[ChartSuggestion] = []
    used_measures: set[str] = set()

    def _pick_measure(preferred: list[str] | None = None) -> Any:
        """Pick the best available measure, preferring referenced fields."""
        if preferred:
            for pname in preferred:
                col = next((m for m in measures if m.spec.name == pname), None)
                if col and col.spec.name not in used_measures:
                    used_measures.add(col.spec.name)
                    return col
        for m in measures:
            if m.spec.name not in used_measures:
                used_measures.add(m.spec.name)
                return m
        # Allow reuse if we run out
        return measures[0] if measures else None

    def _pick_dim(preferred: list[str] | None = None) -> Any:
        """Pick a categorical dimension, preferring referenced fields."""
        if preferred:
            for pname in preferred:
                col = next((d for d in cat_dims if d.spec.name == pname), None)
                if col:
                    return col
        return cat_dims[0] if cat_dims else None

    for chart_type, context in chart_requests:
        if len(charts) >= max_charts:
            break

        # Find fields mentioned near this specific chart request
        ctx_fields = _fields_in_text(context)
        ctx_measures = [f for f in ctx_fields if any(m.spec.name == f for m in measures)]
        ctx_dims = [f for f in ctx_fields if any(d.spec.name == f for d in cat_dims)]
        # Fallback to global references
        local_measures = ctx_measures or ref_measures
        local_dims = ctx_dims or ref_dims

        if chart_type == "Bar":
            dim = _pick_dim(local_dims)
            m = _pick_measure(local_measures)
            if dim and m:
                title = f"Top {dim.spec.name} by {m.spec.name}" if wants_top else f"{m.spec.name} by {dim.spec.name}"
                charts.append(ChartSuggestion(
                    chart_type="Bar",
                    title=title,
                    shelves=[
                        ShelfAssignment(dim.spec.name, "rows"),
                        ShelfAssignment(m.spec.name, "columns", smart_aggregation(m.spec.name)),
                    ],
                    reason=f"User requested bar chart: '{context.strip()}'",
                    priority=90,
                ))

        elif chart_type == "Line":
            time_col = temporal[0] if temporal else None
            m = _pick_measure(local_measures)
            if time_col and m:
                charts.append(ChartSuggestion(
                    chart_type="Line",
                    title=f"{m.spec.name} Trend Over Time",
                    shelves=[
                        ShelfAssignment(time_col.spec.name, "columns"),
                        ShelfAssignment(m.spec.name, "rows", smart_aggregation(m.spec.name)),
                    ],
                    reason=f"User requested line chart: '{context.strip()}'",
                    priority=90,
                ))

        elif chart_type == "Pie":
            dim = _pick_dim(local_dims)
            m = _pick_measure(local_measures)
            if dim and m:
                charts.append(ChartSuggestion(
                    chart_type="Pie",
                    title=f"{m.spec.name} Distribution by {dim.spec.name}",
                    shelves=[
                        ShelfAssignment(m.spec.name, "size", smart_aggregation(m.spec.name)),
                        ShelfAssignment(dim.spec.name, "color"),
                    ],
                    reason="User requested pie chart",
                    priority=85,
                ))

        elif chart_type == "Map":
            geo = geographic[0] if geographic else None
            m = _pick_measure(local_measures)
            if geo and m:
                charts.append(ChartSuggestion(
                    chart_type="Map",
                    title=f"{m.spec.name} by {geo.spec.name}",
                    shelves=[
                        ShelfAssignment(geo.spec.name, "detail"),
                        ShelfAssignment(m.spec.name, "color", smart_aggregation(m.spec.name)),
                    ],
                    reason=f"User requested map: '{context.strip()}'",
                    priority=85,
                ))

        elif chart_type == "Scatterplot":
            if len(measures) >= 2:
                m1 = _pick_measure(local_measures)
                m2 = _pick_measure(local_measures)
                if m1 and m2:
                    shelves = [
                        ShelfAssignment(m1.spec.name, "columns", smart_aggregation(m1.spec.name)),
                        ShelfAssignment(m2.spec.name, "rows", smart_aggregation(m2.spec.name)),
                    ]
                    if cat_dims:
                        shelves.append(ShelfAssignment(cat_dims[0].spec.name, "color"))
                    charts.append(ChartSuggestion(
                        chart_type="Scatterplot",
                        title=f"{m1.spec.name} vs {m2.spec.name}",
                        shelves=shelves,
                        reason="User requested scatter plot",
                        priority=80,
                    ))

        elif chart_type == "Heatmap":
            if len(cat_dims) >= 2 and measures:
                m = _pick_measure(local_measures)
                if m:
                    charts.append(ChartSuggestion(
                        chart_type="Heatmap",
                        title=f"{m.spec.name} by {cat_dims[0].spec.name} and {cat_dims[1].spec.name}",
                        shelves=[
                            ShelfAssignment(cat_dims[0].spec.name, "columns"),
                            ShelfAssignment(cat_dims[1].spec.name, "rows"),
                            ShelfAssignment(m.spec.name, "color", smart_aggregation(m.spec.name)),
                        ],
                        reason="User requested heatmap",
                        priority=80,
                    ))

        elif chart_type == "Text":
            m = _pick_measure(local_measures)
            if m:
                agg = smart_aggregation(m.spec.name)
                charts.append(ChartSuggestion(
                    chart_type="Text",
                    title=_kpi_title(agg, m.spec.name),
                    shelves=[ShelfAssignment(m.spec.name, "label", agg)],
                    reason="User requested KPI",
                    priority=95,
                ))

        elif chart_type == "Area":
            time_col = temporal[0] if temporal else None
            m = _pick_measure(ref_measures)
            if time_col and m:
                charts.append(ChartSuggestion(
                    chart_type="Area",
                    title=f"{m.spec.name} Over Time",
                    shelves=[
                        ShelfAssignment(time_col.spec.name, "columns"),
                        ShelfAssignment(m.spec.name, "rows", smart_aggregation(m.spec.name)),
                    ],
                    reason="User requested area chart",
                    priority=85,
                ))

    # --- Step 5: Add KPIs if not explicitly requested but space remains ---
    kpi_count = sum(1 for c in charts if c.chart_type == "Text")
    if kpi_count == 0 and len(charts) < max_charts:
        # Always include at least 3 KPIs for context
        for m in measures[:min(3, max_charts - len(charts))]:
            if m.spec.name not in used_measures:
                agg = smart_aggregation(m.spec.name)
                charts.insert(0, ChartSuggestion(
                    chart_type="Text",
                    title=_kpi_title(agg, m.spec.name),
                    shelves=[ShelfAssignment(m.spec.name, "label", agg)],
                    reason="Auto-added KPI for context",
                    priority=95,
                ))

    # --- Step 6: Fill remaining slots with auto-suggested charts ---
    if len(charts) < max_charts:
        auto = suggest_charts(schema, max_charts=max_charts)
        existing_sigs = {
            frozenset(sh.field_name for sh in c.shelves) for c in charts
        }
        for ac in auto.charts:
            if len(charts) >= max_charts:
                break
            ac_sig = frozenset(sh.field_name for sh in ac.shelves)
            if ac_sig not in existing_sigs:
                charts.append(ac)
                existing_sigs.add(ac_sig)

    # Determine template
    has_kpi = any(c.chart_type == "Text" for c in charts)
    template = "executive-summary" if has_kpi else ("overview" if len(charts) <= 3 else "grid")

    # Derive title from prompt
    title = _derive_title_from_prompt(prompt)

    return DashboardSuggestion(
        charts=charts,
        layout="grid",
        title=title,
        template=template,
    )


def _derive_title_from_prompt(prompt: str) -> str:
    """Extract a dashboard title from the user's prompt."""
    import re
    # Look for "X dashboard" pattern
    match = re.search(r'(\w[\w\s]{2,20})\s+dashboard', prompt, re.IGNORECASE)
    if match:
        return f"{match.group(1).strip().title()} Dashboard"
    # Look for leading subject
    words = prompt.split()[:4]
    if words:
        subject = " ".join(w.capitalize() for w in words if len(w) > 2)[:30]
        return f"{subject} Dashboard"
    return "Dashboard"


def _suggestion_to_dict(suggestion: DashboardSuggestion) -> dict:
    """Convert a DashboardSuggestion to a JSON-serializable dict."""
    result: dict = {
        "title": suggestion.title,
        "layout": suggestion.layout,
        "charts": [
            {
                "chart_type": c.chart_type,
                "title": c.title,
                "shelves": [
                    {
                        "field_name": s.field_name,
                        "shelf": s.shelf,
                        "aggregation": s.aggregation,
                    }
                    for s in c.shelves
                ],
                "reason": c.reason,
                "priority": c.priority,
            }
            for c in suggestion.charts
        ],
    }
    if suggestion.template:
        result["template"] = suggestion.template
    if suggestion.layout_dict:
        result["layout_dict"] = suggestion.layout_dict
    return result


def dict_to_suggestion(plan: dict) -> DashboardSuggestion:
    """Convert a plan dict back to a DashboardSuggestion."""
    charts = []
    for c in plan.get("charts", []):
        shelves = [
            ShelfAssignment(
                field_name=s["field_name"],
                shelf=s["shelf"],
                aggregation=s.get("aggregation", ""),
            )
            for s in c.get("shelves", [])
        ]
        charts.append(ChartSuggestion(
            chart_type=c["chart_type"],
            title=c["title"],
            shelves=shelves,
            reason=c.get("reason", ""),
            priority=c.get("priority", 0),
        ))

    return DashboardSuggestion(
        charts=charts,
        layout=plan.get("layout", "grid"),
        title=plan.get("title", "Dashboard"),
        template=plan.get("template", ""),
        layout_dict=plan.get("layout_dict"),
    )
