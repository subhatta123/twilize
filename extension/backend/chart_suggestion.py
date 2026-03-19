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
    max_charts: int = 6,
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

    # Rule-based fallback
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


def _suggestion_to_dict(suggestion: DashboardSuggestion) -> dict:
    """Convert a DashboardSuggestion to a JSON-serializable dict."""
    return {
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
    )
