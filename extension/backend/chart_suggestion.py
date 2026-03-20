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
    suggest_charts,
)
from cwtwb.csv_to_hyper import ClassifiedSchema

from .schema_inference import TableauField, classify_tableau_fields

logger = logging.getLogger(__name__)


def suggest_dashboard(
    fields: list[TableauField],
    row_count: int = 0,
    prompt: str = "",
    image_analysis: dict | None = None,
    max_charts: int = 5,
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

    Returns:
        Dashboard plan dict with charts, layout, and title.
    """
    classified = classify_tableau_fields(fields, row_count)

    # Try LLM-based suggestion
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if api_key and prompt:
        try:
            return _llm_suggest(classified, prompt, image_analysis, max_charts, api_key)
        except Exception as exc:
            logger.warning("LLM suggestion failed, falling back to rules: %s", exc)

    # Rule-based fallback — incorporate image analysis if available
    if image_analysis and image_analysis.get("panels"):
        suggestion = _image_guided_suggest(classified, image_analysis, max_charts)
        result = _suggestion_to_dict(suggestion)
        # Attach image-extracted colors for theme application
        color_scheme = image_analysis.get("color_scheme", [])
        if color_scheme:
            result["theme_colors"] = color_scheme
        return result
    else:
        suggestion = suggest_charts(classified, max_charts=max_charts)
        return _suggestion_to_dict(suggestion)


def _llm_suggest(
    schema: ClassifiedSchema,
    prompt: str,
    image_analysis: dict | None,
    max_charts: int,
    api_key: str,
) -> dict:
    """Use an LLM to generate a dashboard plan."""
    # Build the system prompt
    fields_desc = []
    for col in schema.columns:
        fields_desc.append(
            f"- {col.spec.name}: {col.role} ({col.semantic_type}, "
            f"type={col.spec.inferred_type}, cardinality={col.spec.cardinality})"
        )

    system_prompt = f"""You are a Tableau dashboard designer. Given data fields and a user request,
suggest a dashboard layout with up to {max_charts} charts.

Available fields:
{chr(10).join(fields_desc)}

Row count: {schema.row_count}

Supported chart types: Bar, Line, Scatterplot, Pie, Heatmap, Map, Tree Map, Text, Area

Respond with valid JSON only:
{{
  "title": "Dashboard title",
  "layout": "grid",
  "charts": [
    {{
      "chart_type": "Line",
      "title": "Chart title",
      "shelves": [
        {{"field_name": "FieldName", "shelf": "columns", "aggregation": ""}},
        {{"field_name": "FieldName", "shelf": "rows", "aggregation": "SUM"}}
      ],
      "reason": "Why this chart",
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
            "max_tokens": 2048,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_msg}],
        },
        timeout=30.0,
    )
    response.raise_for_status()
    text = response.json()["content"][0]["text"]
    return json.loads(text)


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
    return json.loads(text)


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
                ShelfAssignment(m.spec.name, "rows", "SUM"),
            ]
            if cat_dims and "by" in desc.lower():
                shelves.append(ShelfAssignment(cat_dims[0].spec.name, "color"))
        elif mark_type == "Bar" and cat_dims and measures:
            dim = cat_dims[i % len(cat_dims)] if i < len(cat_dims) else cat_dims[0]
            m = measures[measure_idx % len(measures)]
            shelves = [
                ShelfAssignment(dim.spec.name, "rows"),
                ShelfAssignment(m.spec.name, "columns", "SUM"),
            ]
        elif mark_type == "Scatterplot" and len(measures) >= 2:
            m1 = measures[0]
            m2 = measures[1]
            shelves = [
                ShelfAssignment(m1.spec.name, "columns", "SUM"),
                ShelfAssignment(m2.spec.name, "rows", "SUM"),
            ]
            if cat_dims:
                shelves.append(ShelfAssignment(cat_dims[0].spec.name, "color"))
        elif mark_type == "Pie" and cat_dims and measures:
            dim = cat_dims[0]
            m = measures[measure_idx % len(measures)]
            shelves = [
                ShelfAssignment(m.spec.name, "size", "SUM"),
                ShelfAssignment(dim.spec.name, "color"),
            ]
        elif mark_type == "Map" and geographic and measures:
            geo = geographic[0]
            m = measures[measure_idx % len(measures)]
            shelves = [
                ShelfAssignment(geo.spec.name, "detail"),
                ShelfAssignment(m.spec.name, "color", "SUM"),
            ]
        elif mark_type == "Text" and measures:
            m = measures[measure_idx % len(measures)]
            shelves = [ShelfAssignment(m.spec.name, "label", "SUM")]
        elif mark_type == "Heatmap" and len(cat_dims) >= 2 and measures:
            shelves = [
                ShelfAssignment(cat_dims[0].spec.name, "columns"),
                ShelfAssignment(cat_dims[1].spec.name, "rows"),
                ShelfAssignment(measures[0].spec.name, "color", "SUM"),
            ]
        else:
            # Fallback: bar chart with first available dim + measure
            if cat_dims and measures:
                shelves = [
                    ShelfAssignment(cat_dims[0].spec.name, "rows"),
                    ShelfAssignment(measures[measure_idx % len(measures)].spec.name, "columns", "SUM"),
                ]
            elif measures:
                shelves = [ShelfAssignment(measures[0].spec.name, "label", "SUM")]
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
