"""Vision API integration for reference dashboard image analysis.

Sends a reference dashboard image to a vision-capable LLM and extracts
layout structure, chart types, and color scheme information.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def analyze_reference_image(
    image_path: str | None = None,
    image_bytes: bytes | None = None,
    image_base64: str | None = None,
) -> dict:
    """Analyze a reference dashboard image using a vision LLM.

    Args:
        image_path: Path to the image file.
        image_bytes: Raw image bytes.
        image_base64: Base64-encoded image string.

    Returns:
        Dict with layout_type, panels (chart types per position),
        color_scheme, and notes.
    """
    if image_path:
        with open(image_path, "rb") as f:
            image_bytes = f.read()

    if image_bytes:
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    if not image_base64:
        return {"error": "No image provided"}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            return _analyze_with_anthropic(image_base64, api_key)
        except Exception as exc:
            logger.warning("Anthropic vision failed: %s", exc)

    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        try:
            return _analyze_with_openai(image_base64, api_key)
        except Exception as exc:
            logger.warning("OpenAI vision failed: %s", exc)

    return {
        "error": "No vision API available. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.",
        "layout_type": "grid",
        "panels": [],
        "color_scheme": [],
    }


_VISION_PROMPT = """Analyze this dashboard screenshot carefully. Extract the exact spatial layout and chart structure.

For each chart panel, identify:
1. Its position in the grid (row and column, 0-indexed)
2. Its approximate width and height as fractions of the total dashboard (0.0 to 1.0)
3. The chart type (bar, line, pie, scatter, map, heatmap, text/KPI, area, treemap)
4. A brief description of what data it likely shows

Also extract:
- Overall layout structure (grid, vertical, horizontal, mixed)
- Color scheme (list of hex colors used for data encoding, not background)
- Background color of the dashboard (hex)
- Font style observations (serif/sans-serif, approximate heading size)

Respond with JSON only:
{
  "layout_type": "grid",
  "panels": [
    {"row": 0, "col": 0, "width_pct": 0.5, "height_pct": 0.5, "chart_type": "bar", "description": "Sales by category"},
    {"row": 0, "col": 1, "width_pct": 0.5, "height_pct": 0.5, "chart_type": "line", "description": "Revenue trend"}
  ],
  "color_scheme": ["#4E79A7", "#F28E2B"],
  "background_color": "#FFFFFF",
  "font_style": "sans-serif",
  "notes": "Any additional observations"
}"""


def build_layout_from_panels(
    panels: list[dict],
    worksheet_names: list[str],
) -> dict:
    """Convert image analysis panels into a FlexNode-compatible layout dict.

    Groups panels by row, creates horizontal containers for each row,
    and stacks rows vertically. Uses width_pct/height_pct for weights.

    Args:
        panels: Panel dicts from image analysis with row, col, width_pct, height_pct.
        worksheet_names: Actual worksheet names to map to panel slots.

    Returns:
        Layout dict consumable by resolve_dashboard_layout().
    """
    if not panels or not worksheet_names:
        return {
            "type": "container",
            "direction": "vertical",
            "children": [{"type": "worksheet", "name": n, "weight": 1} for n in worksheet_names],
        }

    # Group panels by row
    rows_map: dict[int, list[dict]] = {}
    for panel in panels:
        row = panel.get("row", 0)
        rows_map.setdefault(row, []).append(panel)

    # Sort each row's panels by column
    for row_panels in rows_map.values():
        row_panels.sort(key=lambda p: p.get("col", 0))

    # Build layout: stack rows vertically
    row_children: list[dict] = []
    ws_idx = 0

    for row_num in sorted(rows_map.keys()):
        row_panels = rows_map[row_num]

        # Calculate row weight from average height_pct
        row_height = sum(p.get("height_pct", 0.5) for p in row_panels) / len(row_panels)
        row_weight = max(1, round(row_height * 10))

        if len(row_panels) == 1 and ws_idx < len(worksheet_names):
            # Single panel in row
            p = row_panels[0]
            row_children.append({
                "type": "worksheet",
                "name": worksheet_names[ws_idx],
                "weight": row_weight,
            })
            ws_idx += 1
        else:
            # Multiple panels in row → horizontal container
            col_children: list[dict] = []
            for p in row_panels:
                if ws_idx >= len(worksheet_names):
                    break
                col_weight = max(1, round(p.get("width_pct", 0.5) * 10))
                col_children.append({
                    "type": "worksheet",
                    "name": worksheet_names[ws_idx],
                    "weight": col_weight,
                })
                ws_idx += 1

            if col_children:
                row_children.append({
                    "type": "container",
                    "direction": "horizontal",
                    "weight": row_weight,
                    "children": col_children,
                })

    # If there are remaining worksheets not mapped to panels, append them
    if ws_idx < len(worksheet_names):
        remaining = worksheet_names[ws_idx:]
        for name in remaining:
            row_children.append({"type": "worksheet", "name": name, "weight": 1})

    return {"type": "container", "direction": "vertical", "children": row_children}


def _analyze_with_anthropic(image_base64: str, api_key: str) -> dict:
    """Use Anthropic Claude vision for image analysis."""
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
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_base64,
                            },
                        },
                        {"type": "text", "text": _VISION_PROMPT},
                    ],
                }
            ],
        },
        timeout=60.0,
    )
    response.raise_for_status()
    text = response.json()["content"][0]["text"]
    return _extract_json(text)


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response, handling markdown fences."""
    import re
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    first = text.find('{')
    last = text.rfind('}')
    if first != -1 and last > first:
        return json.loads(text[first:last + 1])
    raise json.JSONDecodeError(f"No JSON found in response", text, 0)


def _analyze_with_openai(image_base64: str, api_key: str) -> dict:
    """Use OpenAI GPT-4 Vision for image analysis."""
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
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_base64}"
                            },
                        },
                        {"type": "text", "text": _VISION_PROMPT},
                    ],
                }
            ],
            "response_format": {"type": "json_object"},
        },
        timeout=60.0,
    )
    response.raise_for_status()
    text = response.json()["choices"][0]["message"]["content"]
    return _extract_json(text)
