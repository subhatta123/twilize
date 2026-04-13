"""Natural-language prompt interpreter for Tableau workbook generation.

Parses a free-text description and returns a structured ``PromptInterpretation``
that can be fed directly into ``TWBEditor`` to produce a ``.twb`` / ``.twbx``
file with the requested chart type, colour scheme, and layout — no LLM required.

Typical usage::

    from twilize.prompt_interpreter import interpret_prompt, create_from_prompt

    # Just parse — inspect before committing
    interp = interpret_prompt(
        "Create a bar chart showing Sales by Category with blue colours and a vertical layout"
    )

    # Or go straight to a saved workbook
    path = create_from_prompt(
        "Make a dark-themed line chart of Revenue over Month",
        output_path="revenue.twb",
    )
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Keyword maps
# ---------------------------------------------------------------------------

# chart-type keyword → mark_type (matches configure_chart mark_type values)
_CHART_TYPE_MAP: dict[str, str] = {
    # bar
    "bar chart": "Bar",
    "bar graph": "Bar",
    "bar plot": "Bar",
    "bar": "Bar",
    "column chart": "Bar",
    "column graph": "Bar",
    # line
    "line chart": "Line",
    "line graph": "Line",
    "line plot": "Line",
    "line": "Line",
    "trend chart": "Line",
    "trend line": "Line",
    # area
    "area chart": "Area",
    "area graph": "Area",
    "area plot": "Area",
    "area": "Area",
    "stacked area": "Area",
    # pie
    "pie chart": "Pie",
    "pie graph": "Pie",
    "pie": "Pie",
    "donut chart": "Pie",
    "doughnut chart": "Pie",
    "donut": "Pie",
    # scatter
    "scatter plot": "Circle",
    "scatter chart": "Circle",
    "scatterplot": "Circle",
    "scatter": "Circle",
    "bubble chart": "Circle",
    "bubble": "Circle",
    # heatmap
    "heat map": "Square",
    "heatmap": "Square",
    "heat chart": "Square",
    # map
    "map": "Map",
    "choropleth": "Map",
    "geographic": "Map",
    "geo chart": "Map",
    # text / KPI
    "text table": "Text",
    "text chart": "Text",
    "cross tab": "Text",
    "crosstab": "Text",
    "kpi": "Text",
    "scorecard": "Text",
    "table": "Text",
}

# Longest-match ordering (longer phrases first so "bar chart" beats "bar")
_SORTED_CHART_KEYS = sorted(_CHART_TYPE_MAP, key=len, reverse=True)

# colour keyword → hex (or palette name)
_COLOR_MAP: dict[str, str] = {
    "blue": "#4E79A7",
    "light blue": "#A0CBE8",
    "dark blue": "#1f5fa6",
    "navy": "#003f7f",
    "red": "#E15759",
    "dark red": "#B2182B",
    "orange": "#F28E2B",
    "yellow": "#EDC948",
    "gold": "#B6992D",
    "green": "#59A14F",
    "light green": "#8CD17D",
    "dark green": "#1B7837",
    "teal": "#499894",
    "cyan": "#76B7B2",
    "purple": "#B07AA1",
    "pink": "#FF9DA7",
    "magenta": "#D37295",
    "brown": "#9C755F",
    "grey": "#BAB0AC",
    "gray": "#BAB0AC",
    "dark grey": "#79706E",
    "dark gray": "#79706E",
    "black": "#000000",
    "white": "#FFFFFF",
}

_SORTED_COLOR_KEYS = sorted(_COLOR_MAP, key=len, reverse=True)

# named palette → palette_name argument
_PALETTE_MAP: dict[str, str] = {
    "tableau10": "tableau10",
    "tableau 10": "tableau10",
    "tableau20": "tableau20",
    "tableau 20": "tableau20",
    "blue red": "blue-red",
    "blue-red": "blue-red",
    "diverging blue red": "blue-red",
    "green gold": "green-gold",
    "green-gold": "green-gold",
}

# theme keyword → style_presets theme name
_THEME_MAP: dict[str, str] = {
    "dark": "dark",
    "dark theme": "dark",
    "dark mode": "dark",
    "light": "modern-light",
    "light theme": "modern-light",
    "modern": "modern-light",
    "modern light": "modern-light",
    "corporate": "corporate-blue",
    "corporate blue": "corporate-blue",
    "professional": "corporate-blue",
    "minimal": "minimal",
    "minimalist": "minimal",
    "clean": "minimal",
    "vibrant": "vibrant",
    "colourful": "vibrant",
    "colorful": "vibrant",
    "bold": "vibrant",
}

_SORTED_THEME_KEYS = sorted(_THEME_MAP, key=len, reverse=True)

# layout keyword → layout string
_LAYOUT_MAP: dict[str, str] = {
    "vertical layout": "vertical",
    "vertical": "vertical",
    "stacked vertically": "vertical",
    "stacked": "vertical",
    "horizontal layout": "horizontal",
    "horizontal": "horizontal",
    "side by side": "horizontal",
    "side-by-side": "horizontal",
    "grid": "grid-2x2",
    "2x2 grid": "grid-2x2",
    "2 by 2": "grid-2x2",
    "2x2": "grid-2x2",
    "grid layout": "grid-2x2",
}

_SORTED_LAYOUT_KEYS = sorted(_LAYOUT_MAP, key=len, reverse=True)

# Prepositions that introduce field mentions
_FIELD_PREPOSITIONS = (
    "showing", "of", "for", "with", "displaying", "by", "vs", "versus",
    "on", "over", "across", "per", "about", "based on",
)

# Aggregation words commonly preceding measure names
_AGGREGATION_WORDS = ("sum of", "total", "average", "avg", "count of", "count",
                      "sum", "max", "min", "median")


# ---------------------------------------------------------------------------
# Data-classes
# ---------------------------------------------------------------------------

@dataclass
class PromptInterpretation:
    """Structured result of ``interpret_prompt()``.

    All fields are optional — only those confidently detected from the prompt
    are populated.  Callers should inspect the ``warnings`` list for anything
    the parser could not resolve.
    """

    # Chart configuration
    chart_type: str = "Bar"                     # mark_type for configure_chart
    worksheet_name: str = "Sheet 1"

    # Field guesses (positional / keyword-extracted)
    rows: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    color_field: str = ""
    size_field: str = ""
    label_field: str = ""
    detail_field: str = ""

    # Colour / theme
    colors: list[str] = field(default_factory=list)  # hex list
    palette_name: str = ""                            # named palette
    theme: str = ""                                   # named theme preset

    # Layout
    layout: str = "vertical"                   # dashboard layout string

    # Dashboard
    dashboard_name: str = "Dashboard 1"
    title: str = ""

    # Parser diagnostics
    warnings: list[str] = field(default_factory=list)

    # Raw prompt, preserved for debugging
    raw_prompt: str = ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def interpret_prompt(prompt: str) -> PromptInterpretation:
    """Parse a free-text *prompt* and return a :class:`PromptInterpretation`.

    The parser uses **keyword matching** — it does not call any external AI
    service.  Fields that cannot be resolved are left at their defaults and a
    note is added to ``result.warnings``.

    Args:
        prompt: Natural-language description, e.g.
            ``"Create a bar chart showing Sales by Category with blue colours"``.

    Returns:
        A :class:`PromptInterpretation` with every detected attribute populated.
    """
    result = PromptInterpretation(raw_prompt=prompt)
    lower = prompt.lower()

    # --- chart type --------------------------------------------------------
    result.chart_type = _detect_chart_type(lower)

    # --- theme -------------------------------------------------------------
    result.theme = _detect_theme(lower)

    # --- palette / colour --------------------------------------------------
    palette = _detect_palette(lower)
    if palette:
        result.palette_name = palette
    else:
        colours = _detect_colors(lower)
        if colours:
            result.colors = colours

    # --- layout ------------------------------------------------------------
    result.layout = _detect_layout(lower)

    # --- field extraction --------------------------------------------------
    rows, columns, color_field = _extract_fields(prompt, result.chart_type)
    result.rows = rows
    result.columns = columns
    result.color_field = color_field

    # --- title / worksheet name --------------------------------------------
    result.title = _extract_title(prompt)
    if result.title:
        result.worksheet_name = result.title[:40]
        result.dashboard_name = f"{result.title[:36]} (db)"

    # --- warnings ----------------------------------------------------------
    if not result.rows and not result.columns:
        result.warnings.append(
            "No field names detected in the prompt.  "
            "Add fields manually via configure_chart() after calling create_from_prompt()."
        )

    return result


def create_from_prompt(
    prompt: str,
    output_path: str | Path = "output.twb",
    template_path: str | Path = "",
    *,
    apply_theme: bool = True,
    add_dashboard: bool = True,
) -> Path:
    """Build a Tableau workbook from a natural-language *prompt* and save it.

    This is a convenience wrapper that calls :func:`interpret_prompt` and then
    drives ``TWBEditor`` to produce a ready-to-open ``.twb`` / ``.twbx`` file.

    Args:
        prompt:        Free-text description (chart type, colours, layout, …).
        output_path:   Destination file.  Use ``.twbx`` extension to bundle
                       the workbook as a packaged archive.
        template_path: Path to a ``.twb`` or ``.twbx`` template.  Pass an
                       empty string (default) to use the built-in blank template.
        apply_theme:   When *True* (default), apply the detected theme / palette.
        add_dashboard: When *True* (default), wrap all worksheets in a dashboard.

    Returns:
        Resolved :class:`pathlib.Path` to the saved file.
    """
    from .twb_editor import TWBEditor
    from .style_presets import apply_theme_to_editor

    interp = interpret_prompt(prompt)
    editor = TWBEditor(template_path)

    # --- worksheet + chart -------------------------------------------------
    ws_name = interp.worksheet_name
    editor.add_worksheet(ws_name)

    chart_kwargs: dict = {"mark_type": interp.chart_type}
    if interp.rows:
        chart_kwargs["rows"] = interp.rows
    if interp.columns:
        chart_kwargs["columns"] = interp.columns
    if interp.color_field:
        chart_kwargs["color"] = [interp.color_field]

    try:
        editor.configure_chart(ws_name, **chart_kwargs)
    except (ValueError, KeyError):
        # Extracted field names may not exist in the connected datasource.
        # Fall back to a chart with no shelf assignments — the user can wire
        # up their own fields after opening the workbook.
        editor.configure_chart(ws_name, mark_type=interp.chart_type)

    # --- dashboard ---------------------------------------------------------
    db_name = interp.dashboard_name
    if add_dashboard:
        editor.add_dashboard(
            db_name,
            worksheet_names=[ws_name],
            layout=interp.layout,
        )

    # --- colour / theme ----------------------------------------------------
    if apply_theme:
        if interp.palette_name:
            try:
                editor.apply_color_palette(palette_name=interp.palette_name)
            except Exception:
                pass
        elif interp.colors:
            try:
                editor.apply_color_palette(colors=interp.colors)
            except Exception:
                pass
        if interp.theme and add_dashboard:
            try:
                apply_theme_to_editor(editor, interp.theme, db_name)
            except Exception:
                pass  # theme not critical — workbook still valid

    # --- save --------------------------------------------------------------
    output_path = Path(output_path)
    editor.save(str(output_path))
    return output_path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_chart_type(lower: str) -> str:
    """Return the best-matching mark_type string from the prompt."""
    for key in _SORTED_CHART_KEYS:
        if key in lower:
            return _CHART_TYPE_MAP[key]
    return "Bar"  # sensible default


def _detect_theme(lower: str) -> str:
    """Return the best-matching theme name, or empty string."""
    for key in _SORTED_THEME_KEYS:
        if key in lower:
            return _THEME_MAP[key]
    return ""


def _detect_palette(lower: str) -> str:
    """Return a named palette if one is explicitly mentioned."""
    for key, val in _PALETTE_MAP.items():
        if key in lower:
            return val
    return ""


def _detect_colors(lower: str) -> list[str]:
    """Extract named colour words from the prompt as hex strings."""
    found: list[str] = []
    for key in _SORTED_COLOR_KEYS:
        if re.search(r"\b" + re.escape(key) + r"\b", lower):
            hex_val = _COLOR_MAP[key]
            if hex_val not in found:
                found.append(hex_val)
    return found


def _detect_layout(lower: str) -> str:
    """Return the best-matching layout string."""
    for key in _SORTED_LAYOUT_KEYS:
        if key in lower:
            return _LAYOUT_MAP[key]
    return "vertical"  # sensible default


def _extract_fields(prompt: str, chart_type: str) -> tuple[list[str], list[str], str]:
    """Heuristically extract row, column, and colour field names from the prompt.

    Strategy
    --------
    1. Look for patterns like "showing X by Y", "of X", "by Y", etc.
    2. Strip common stop-words and aggregation prefixes.
    3. Assign positional fields: for most charts the first extracted token
       goes to *rows* (dimension axis) and the second to *columns* (measure).
       For pie charts the first token is the *column* (wedge size) and the
       second is the *row* (dimension).

    Returns:
        (rows, columns, color_field) — any of which may be empty lists / strings.
    """
    # Remove colour words to avoid confusing them with field names
    cleaned = prompt
    for colour_word in _SORTED_COLOR_KEYS:
        cleaned = re.sub(r"\b" + re.escape(colour_word) + r"\b", "", cleaned,
                         flags=re.IGNORECASE)
    # Also remove palette/theme/layout modifiers
    cleaned = re.sub(
        r"\b(palette|theme|layout|style|mode|chart|graph|plot|table|map|"
        r"workbook|dashboard|create|make|build|generate|show|display|add|produce|"
        r"using|with|in|a|an|the|and|or|of|on|for|to|from|by|over|across|per|"
        r"vs|versus|this|that|colour|color|colors|colours|"
        r"vertical|horizontal|grid|dark|light|modern|minimal|vibrant|colou?rful|bold|"
        r"corporate|professional|stacked|side|tableau|2x2|some|all|each|every)\b",
        " ", cleaned, flags=re.IGNORECASE,
    )

    # Pull out quoted field names first (highest confidence)
    quoted = re.findall(r'["\']([^"\']+)["\']', cleaned)
    remaining = re.sub(r'["\'][^"\']+["\']', "", cleaned)

    # Grab individual capitalized words as field hints (no multi-word with spaces
    # to avoid accidentally capturing entire sentences)
    cap_words = re.findall(r"\b[A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*)?\b", remaining)

    # De-dupe, filter stop words, and clean up
    _stop = re.compile(
        r"^(bar|line|area|pie|donut|doughnut|scatter|bubble|heatmap|heat|"
        r"kpi|scorecard|crosstab|map|text|column|trend|circle|square|gantt|"
        r"lollipop|butterfly|calendar|vs|and|or|the|a|an|by|of|for|in|on|"
        r"over|per|with|from|to|across|about|showing|displaying|"
        r"sum|avg|average|count|min|max|median|total)$",
        re.IGNORECASE,
    )

    raw_fields: list[str] = []
    for token in quoted + cap_words:
        token = token.strip()
        if not token:
            continue
        if _stop.fullmatch(token):
            continue
        # Strip leading aggregation words
        for agg in _AGGREGATION_WORDS:
            if token.lower().startswith(agg + " "):
                token = token[len(agg) + 1:].strip()
        if len(token) < 2:
            continue
        if token not in raw_fields:
            raw_fields.append(token)

    # Positional assignment
    rows: list[str] = []
    columns: list[str] = []
    color_field: str = ""

    if chart_type == "Pie":
        # Pie: first = dimension (rows), second = measure (columns)
        if raw_fields:
            rows = [raw_fields[0]]
        if len(raw_fields) > 1:
            columns = [f"SUM({raw_fields[1]})"]
        if len(raw_fields) > 2:
            color_field = raw_fields[2]
    elif chart_type in ("Map",):
        # Maps: first field = geographic dimension
        if raw_fields:
            rows = [raw_fields[0]]
        if len(raw_fields) > 1:
            columns = [f"SUM({raw_fields[1]})"]
    elif chart_type == "Text":
        # Text tables: all fields in rows
        rows = raw_fields
    else:
        # Bar / Line / Area / Circle / Square: first = dimension, rest = measures
        if raw_fields:
            rows = [raw_fields[0]]
        if len(raw_fields) > 1:
            columns = [f"SUM({raw_fields[1]})"]
        if len(raw_fields) > 2:
            color_field = raw_fields[2]

    return rows, columns, color_field


def _extract_title(prompt: str) -> str:
    """Try to derive a concise title from the prompt."""
    # Remove leading instruction verbs
    cleaned = re.sub(
        r"^\s*(create|make|build|generate|show|display|produce|add)\s+(a|an|the)?\s*",
        "",
        prompt,
        flags=re.IGNORECASE,
    ).strip()
    # Truncate at first colour/theme/layout keyword (these are modifiers, not part of title)
    for stop in ("with ", "using ", "in a ", "using a ", " colour", " color",
                 " theme", " palette", " layout", " and "):
        idx = cleaned.lower().find(stop)
        if idx > 5:
            cleaned = cleaned[:idx].strip()
    # Title-case and truncate
    title = cleaned[:60].strip(" ,.")
    return title.title() if title else "Dashboard"
