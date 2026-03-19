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


_VISION_PROMPT = """Analyze this dashboard screenshot. Extract:
1. Layout structure (grid, vertical, horizontal, mixed)
2. For each panel/chart: position (row, col), chart type (bar, line, pie, scatter, map, heatmap, text/KPI, area, treemap)
3. Color scheme (list of hex colors if visible)
4. Any notable design patterns

Respond with JSON only:
{
  "layout_type": "grid",
  "panels": [
    {"row": 0, "col": 0, "chart_type": "bar", "description": "Sales by category"},
    {"row": 0, "col": 1, "chart_type": "line", "description": "Revenue trend"}
  ],
  "color_scheme": ["#4E79A7", "#F28E2B"],
  "notes": "Any additional observations"
}"""


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
        timeout=30.0,
    )
    response.raise_for_status()
    text = response.json()["content"][0]["text"]
    return json.loads(text)


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
        timeout=30.0,
    )
    response.raise_for_status()
    text = response.json()["choices"][0]["message"]["content"]
    return json.loads(text)
