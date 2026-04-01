"""Named layout templates for dashboard generation.

Layout patterns extracted from professional Tableau dashboard template c.3
(1200x800px). Key design principles:
- Card-based: white #ffffff cards on #e6e6e6 gray background
- No borders — separation via background contrast + 4px margins
- KPIs: 100px row, equal-width, white background, distribute-evenly
- Filters: dark navy #192f3e, 30px horizontal bar
- Charts: 2-column grid with distribute-evenly strategy
- Title: 70px, 20pt bold, white background
- FLAT structure: ONE root container, all sections as direct children
"""

from __future__ import annotations

from typing import Any


# ── Constants matching template c.3 exactly ──────────────────────────
_DASH_BG = "#e6e6e6"          # Dashboard background
_CARD_BG = "#ffffff"           # Chart/KPI card background
_FILTER_BG = "#192f3e"         # Filter bar background (dark navy)
_FILTER_TEXT = "#ffffff"        # Filter text color
_TEXT_COLOR = "#111e29"         # Standard text color
_CARD_MARGIN = "4"             # Gap between cards (inner)
_OUTER_MARGIN = "8"            # Side gutter margin (edges)
_KPI_HEIGHT = 120              # KPI row fixed height (default; overridden by rules)
_FILTER_HEIGHT = 30            # Filter bar fixed height
_TITLE_HEIGHT = 70             # Title bar fixed height
# Chart area uses weight=1 to fill remaining space after fixed sections


# ── Building blocks matching c.3 zone patterns ──────────────────────

def _title_zone(title: str) -> dict[str, Any]:
    """Title bar: 70px fixed, 20pt bold, white bg, matches c.3 zone 2/5."""
    return {
        "type": "text", "text": title, "font_size": "20",
        "font_color": _TEXT_COLOR, "bold": True, "fixed_size": _TITLE_HEIGHT,
        "style": {
            "background-color": _CARD_BG,
            "margin-right": _OUTER_MARGIN,
            "margin-left": _OUTER_MARGIN,
        },
    }


def _filter_row(
    filters: list[dict[str, Any]],
    worksheet_name: str,
) -> dict[str, Any]:
    """Filter bar: 30px fixed, dark navy #192f3e, matches c.3 zone 3."""
    return {
        "type": "container", "direction": "horizontal",
        "fixed_size": _FILTER_HEIGHT,
        "style": {
            "background-color": _FILTER_BG,
            "margin-right": _OUTER_MARGIN,
            "margin-left": _OUTER_MARGIN,
        },
        "children": [{
            "type": "filter", "field": f.get("field") or f.get("column", ""),
            "worksheet": worksheet_name,
            "mode": "multiplevalues", "weight": 1, "show_title": True,
        } for f in filters],
    }


def _kpi_row(kpi_names: list[str], row_height: int = _KPI_HEIGHT) -> dict[str, Any]:
    """KPI row: fixed height, distribute-evenly, matches c.3 zone 16."""
    children = []
    for i, name in enumerate(kpi_names):
        # Margins: first gets margin-left=8, last gets margin-right=8,
        # inner ones get margin=4 (matching c.3 pattern)
        style: dict[str, Any] = {"background-color": _CARD_BG}
        if i == 0:
            style["margin-left"] = _OUTER_MARGIN
            style["margin-right"] = _CARD_MARGIN
        elif i == len(kpi_names) - 1:
            style["margin-right"] = _OUTER_MARGIN
            style["margin-left"] = _CARD_MARGIN
        else:
            style["margin-left"] = _CARD_MARGIN
            style["margin-right"] = _CARD_MARGIN

        children.append({
            "type": "worksheet", "name": name, "weight": 1,
            "style": style,
        })

    return {
        "type": "container", "direction": "horizontal",
        "fixed_size": row_height,
        "layout_strategy": "distribute-evenly",
        "children": children,
    }


def _views_2col(names: list[str]) -> dict[str, Any]:
    """2-column chart grid: distribute-evenly columns, matches c.3 zone 9.

    Uses weight=1 to fill ALL remaining space after fixed sections
    (title 70 + filter 30 + KPI 100 = 200px fixed, leaving 600px
    for charts in an 800px dashboard). Charts are placed directly
    in columns (no wrapper containers) matching c.3's zone structure.
    """
    if not names:
        return {"type": "empty", "fixed_size": 10}

    mid = (len(names) + 1) // 2
    left_names = names[:mid]
    right_names = names[mid:]

    def _col_children(col_names: list[str], is_left: bool) -> list[dict[str, Any]]:
        result = []
        for j, n in enumerate(col_names):
            style: dict[str, Any] = {"background-color": _CARD_BG}
            if is_left:
                style["margin-left"] = _OUTER_MARGIN
                style["margin-right"] = _CARD_MARGIN
            else:
                style["margin-right"] = _OUTER_MARGIN
                style["margin-left"] = _CARD_MARGIN
            if j == len(col_names) - 1:
                style["margin-bottom"] = _OUTER_MARGIN
            result.append({
                "type": "worksheet", "name": n, "weight": 1,
                "style": style,
            })
        return result

    left_col: dict[str, Any] = {
        "type": "container", "direction": "vertical",
        "layout_strategy": "distribute-evenly", "weight": 1,
        "children": _col_children(left_names, is_left=True),
    }

    if not right_names:
        # Single column — fill the entire width
        return {
            "type": "container", "direction": "horizontal",
            "weight": 1,
            "layout_strategy": "distribute-evenly",
            "children": [left_col],
        }

    right_col: dict[str, Any] = {
        "type": "container", "direction": "vertical",
        "layout_strategy": "distribute-evenly", "weight": 1,
        "children": _col_children(right_names, is_left=False),
    }

    return {
        "type": "container", "direction": "horizontal",
        "weight": 1,
        "layout_strategy": "distribute-evenly",
        "children": [left_col, right_col],
    }


def _filter_sidebar(
    filters: list[dict[str, Any]],
    worksheet_name: str,
) -> dict[str, Any]:
    """Vertical filter sidebar: 200px fixed, dark navy."""
    return {
        "type": "container", "direction": "vertical",
        "fixed_size": 200,
        "style": {
            "background-color": _FILTER_BG,
            "margin-top": "12",
            "margin-bottom": "24",
        },
        "children": [{
            "type": "filter",
            "field": f.get("field") or f.get("column", ""),
            "worksheet": worksheet_name,
            "mode": "multiplevalues", "fixed_size": 44, "show_title": True,
        } for f in filters],
    }


def _empty_layout() -> dict[str, Any]:
    return {"type": "container", "direction": "vertical", "children": []}


# ── Template definitions ─────────────────────────────────────────────
# ALL templates now build a COMPLETE flat layout: one root vertical
# container with title, filters, KPIs, and charts as direct children.
# This matches the c.3 template structure exactly and prevents the
# deeply-nested container issue that caused Tableau to hide charts.

def _executive_summary(
    worksheet_names: list[str],
    title: str = "",
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """C3-style: Title + Filters + KPI row + 2-column chart grid.

    Expects worksheet_names reordered with KPI/Text charts first.
    """
    names = list(worksheet_names)
    if not names:
        return _empty_layout()

    children: list[dict[str, Any]] = []

    # Title bar (70px)
    if title:
        children.append(_title_zone(title))

    # Filter bar (30px)
    filter_ws = names[-1] if len(names) > 1 else names[0]
    if filters:
        children.append(_filter_row(filters, filter_ws))

    # Split KPIs from analytical charts.
    # Ensure at least 4 charts in the detail grid (matches c.3 pattern).
    kpi_count = min(5, max(2, len(names) - 4))
    if kpi_count >= len(names):
        kpi_count = max(1, len(names) - 1)

    # KPI row (100px)
    children.append(_kpi_row(names[:kpi_count]))

    # Chart grid (fills remaining space)
    detail = names[kpi_count:]
    if detail:
        children.append(_views_2col(detail))

    return {
        "type": "container", "direction": "vertical",
        "style": {"background-color": _DASH_BG},
        "children": children,
    }


def _kpi_detail(
    worksheet_names: list[str],
    title: str = "",
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """KPI row + featured chart + detail row (template f.1 pattern)."""
    names = list(worksheet_names)
    if not names:
        return _empty_layout()

    children: list[dict[str, Any]] = []

    if title:
        children.append(_title_zone(title))

    filter_ws = names[-1] if len(names) > 1 else names[0]
    if filters:
        children.append(_filter_row(filters, filter_ws))

    kpi_count = min(5, len(names) - 1) if len(names) >= 4 else min(2, len(names) - 1)
    if kpi_count < 1:
        kpi_count = 1

    children.append(_kpi_row(names[:kpi_count]))

    main = names[kpi_count:]
    if len(main) == 1:
        children.append({
            "type": "worksheet", "name": main[0], "weight": 1,
            "style": {"background-color": _CARD_BG, "margin": _CARD_MARGIN},
        })
    elif len(main) == 2:
        children.append({
            "type": "container", "direction": "horizontal",
            "weight": 1,
            "layout_strategy": "distribute-evenly",
            "children": [{
                "type": "worksheet", "name": n, "weight": 1,
                "style": {"background-color": _CARD_BG, "margin": _CARD_MARGIN},
            } for n in main],
        })
    elif main:
        # Featured chart top, rest in row below
        children.append({
            "type": "worksheet", "name": main[0], "weight": 2,
            "style": {
                "background-color": _CARD_BG,
                "margin-left": _OUTER_MARGIN,
                "margin-right": _OUTER_MARGIN,
            },
        })
        children.append({
            "type": "container", "direction": "horizontal",
            "weight": 1,
            "layout_strategy": "distribute-evenly",
            "children": [{
                "type": "worksheet", "name": n, "weight": 1,
                "style": {"background-color": _CARD_BG, "margin": _CARD_MARGIN},
            } for n in main[1:]],
        })

    return {
        "type": "container", "direction": "vertical",
        "style": {"background-color": _DASH_BG},
        "children": children,
    }


def _left_filter_panel(
    worksheet_names: list[str],
    title: str = "",
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Left filter sidebar + main content (template h.1 pattern)."""
    names = list(worksheet_names)
    if not names:
        return _empty_layout()

    # Build the main content column
    main_children: list[dict[str, Any]] = []

    if title:
        main_children.append(_title_zone(title))

    kpi_count = min(4, max(2, len(names) - 1))
    if kpi_count >= len(names):
        kpi_count = max(1, len(names) - 1)

    main_children.append(_kpi_row(names[:kpi_count]))

    detail = names[kpi_count:]
    if detail:
        main_children.append(_views_2col(detail))

    main_content: dict[str, Any] = {
        "type": "container", "direction": "vertical", "weight": 1,
        "style": {"background-color": _DASH_BG},
        "children": main_children,
    }

    # If filters, add sidebar on the left
    filter_ws = names[-1] if len(names) > 1 else names[0]
    if filters:
        sidebar = _filter_sidebar(filters, filter_ws)
        return {
            "type": "container", "direction": "horizontal",
            "style": {"background-color": _DASH_BG},
            "children": [sidebar, main_content],
        }

    return main_content


def _featured_detail(
    worksheet_names: list[str],
    title: str = "",
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Large chart left + stacked right (template d.1 pattern)."""
    names = list(worksheet_names)
    if not names:
        return _empty_layout()

    children: list[dict[str, Any]] = []

    if title:
        children.append(_title_zone(title))

    filter_ws = names[-1] if len(names) > 1 else names[0]
    if filters:
        children.append(_filter_row(filters, filter_ws))

    if len(names) == 1:
        children.append({
            "type": "worksheet", "name": names[0], "weight": 1,
            "style": {"background-color": _CARD_BG, "margin": _CARD_MARGIN},
        })
    else:
        featured = {
            "type": "worksheet", "name": names[0], "weight": 3,
            "style": {
                "background-color": _CARD_BG,
                "margin-left": _OUTER_MARGIN,
                "margin-right": _CARD_MARGIN,
            },
        }
        right_stack = {
            "type": "container", "direction": "vertical",
            "layout_strategy": "distribute-evenly", "weight": 2,
            "children": [{
                "type": "worksheet", "name": n, "weight": 1,
                "style": {
                    "background-color": _CARD_BG,
                    "margin-right": _OUTER_MARGIN,
                    "margin-left": _CARD_MARGIN,
                },
            } for n in names[1:]],
        }
        children.append({
            "type": "container", "direction": "horizontal",
            "weight": 1,
            "children": [featured, right_stack],
        })

    return {
        "type": "container", "direction": "vertical",
        "style": {"background-color": _DASH_BG},
        "children": children,
    }


def _comparison(
    worksheet_names: list[str],
    title: str = "",
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Side-by-side equal charts."""
    names = list(worksheet_names)
    if not names:
        return _empty_layout()

    children: list[dict[str, Any]] = []

    if title:
        children.append(_title_zone(title))

    filter_ws = names[-1] if len(names) > 1 else names[0]
    if filters:
        children.append(_filter_row(filters, filter_ws))

    if len(names) <= 3:
        children.append({
            "type": "container", "direction": "horizontal",
            "layout_strategy": "distribute-evenly",
            "weight": 1,
            "children": [{
                "type": "worksheet", "name": n, "weight": 1,
                "style": {"background-color": _CARD_BG, "margin": _CARD_MARGIN},
            } for n in names],
        })
    else:
        children.append(_views_2col(names))

    return {
        "type": "container", "direction": "vertical",
        "style": {"background-color": _DASH_BG},
        "children": children,
    }


def _overview(
    worksheet_names: list[str],
    title: str = "",
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Featured chart top + detail row below."""
    names = list(worksheet_names)
    if not names:
        return _empty_layout()

    children: list[dict[str, Any]] = []

    if title:
        children.append(_title_zone(title))

    filter_ws = names[-1] if len(names) > 1 else names[0]
    if filters:
        children.append(_filter_row(filters, filter_ws))

    # Featured chart
    children.append({
        "type": "worksheet", "name": names[0], "weight": 2,
        "style": {
            "background-color": _CARD_BG,
            "margin-left": _OUTER_MARGIN,
            "margin-right": _OUTER_MARGIN,
        },
    })

    # Detail row
    if len(names) > 1:
        children.append({
            "type": "container", "direction": "horizontal",
            "weight": 1,
            "layout_strategy": "distribute-evenly",
            "children": [{
                "type": "worksheet", "name": n, "weight": 1,
                "style": {"background-color": _CARD_BG, "margin": _CARD_MARGIN},
            } for n in names[1:]],
        })

    return {
        "type": "container", "direction": "vertical",
        "style": {"background-color": _DASH_BG},
        "children": children,
    }


def _grid(
    worksheet_names: list[str],
    title: str = "",
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Balanced 2-column grid layout."""
    names = list(worksheet_names)
    if not names:
        return _empty_layout()

    children: list[dict[str, Any]] = []

    if title:
        children.append(_title_zone(title))

    filter_ws = names[-1] if len(names) > 1 else names[0]
    if filters:
        children.append(_filter_row(filters, filter_ws))

    children.append(_views_2col(names))

    return {
        "type": "container", "direction": "vertical",
        "style": {"background-color": _DASH_BG},
        "children": children,
    }


# ── Registry ─────────────────────────────────────────────────────────

_TEMPLATES: dict[str, tuple[callable, str]] = {
    "executive-summary": (_executive_summary, "KPI row + 2-col chart grid (c.3)"),
    "kpi-detail": (_kpi_detail, "KPI row + featured chart focus"),
    "left-filter": (_left_filter_panel, "Left filter sidebar + KPIs + charts"),
    "featured-detail": (_featured_detail, "Large chart left + stacked right"),
    "comparison": (_comparison, "Side-by-side equal charts"),
    "overview": (_overview, "Featured chart top + details below"),
    "grid": (_grid, "Balanced 2-column grid layout"),
}

TEMPLATE_NAMES: list[str] = list(_TEMPLATES.keys())


def get_template(
    name: str,
    worksheet_names: list[str],
    title: str = "",
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Get a layout dict for the named template.

    Templates now build the COMPLETE layout internally (title, filters,
    KPIs, charts) in a single flat container — matching the c.3 template
    structure. No additional wrapping is done here.
    """
    entry = _TEMPLATES.get(name)
    if entry is None:
        entry = _TEMPLATES["executive-summary"]
    factory, _ = entry
    return factory(worksheet_names, title=title, filters=filters)


def list_templates() -> list[dict[str, str]]:
    """List available templates with descriptions."""
    return [{"name": n, "description": d} for n, (_, d) in _TEMPLATES.items()]


# ── Legacy aliases for backward compatibility ────────────────────────

def _build_filter_row(
    filters: list[dict[str, Any]],
    worksheet_name: str,
) -> dict[str, Any]:
    """Legacy alias."""
    return _filter_row(filters, worksheet_name)


def _build_filter_sidebar(
    filters: list[dict[str, Any]],
    worksheet_name: str,
) -> dict[str, Any]:
    """Legacy alias."""
    return _filter_sidebar(filters, worksheet_name)


def _wrap_with_title(inner: dict[str, Any], title: str) -> dict[str, Any]:
    """Legacy alias — prefer using templates directly."""
    title_z = _title_zone(title)
    return {
        "type": "container", "direction": "vertical",
        "children": [title_z, {**inner, "weight": inner.get("weight", 1)}],
    }
