"""Named layout templates for dashboard generation.

Each template is a factory function that takes a list of worksheet names
and returns a FlexNode-compatible layout dict consumable by
``resolve_dashboard_layout()`` in ``dashboard_layouts.py``.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Template definitions
# ---------------------------------------------------------------------------

def _executive_summary(worksheet_names: list[str]) -> dict[str, Any]:
    """Title bar + KPI row + chart grid below.

    Best for dashboards with KPI/Text charts plus detail charts.
    """
    names = list(worksheet_names)
    if not names:
        return {"type": "container", "direction": "vertical", "children": []}

    # Separate KPI-style names (short titles or first 1-2) from detail charts
    # Put first chart as featured, rest in grid
    children: list[dict] = []

    if len(names) == 1:
        children.append({"type": "worksheet", "name": names[0], "weight": 1})
    elif len(names) == 2:
        # Top chart, bottom chart
        children.append({"type": "worksheet", "name": names[0], "weight": 1})
        children.append({"type": "worksheet", "name": names[1], "weight": 1})
    else:
        # First chart as header-area, remaining in a 2-col grid
        children.append({"type": "worksheet", "name": names[0], "weight": 1, "fixed_size": 200})
        rows = _make_grid_rows(names[1:], cols=2)
        children.append({
            "type": "container",
            "direction": "vertical",
            "weight": 3,
            "children": rows,
        })

    return {"type": "container", "direction": "vertical", "children": children}


def _kpi_detail(worksheet_names: list[str]) -> dict[str, Any]:
    """Left sidebar with KPIs, main chart area on right.

    Best when first 1-2 charts are KPIs and the rest are detail views.
    """
    names = list(worksheet_names)
    if not names:
        return {"type": "container", "direction": "horizontal", "children": []}

    if len(names) == 1:
        return {"type": "worksheet", "name": names[0], "weight": 1}

    # Sidebar: first chart (or first 2 if >=4 total)
    sidebar_count = 2 if len(names) >= 4 else 1
    sidebar_names = names[:sidebar_count]
    main_names = names[sidebar_count:]

    sidebar = {
        "type": "container",
        "direction": "vertical",
        "weight": 1,
        "children": [{"type": "worksheet", "name": n, "weight": 1} for n in sidebar_names],
    }

    if len(main_names) == 1:
        main = {"type": "worksheet", "name": main_names[0], "weight": 3}
    else:
        rows = _make_grid_rows(main_names, cols=2)
        main = {
            "type": "container",
            "direction": "vertical",
            "weight": 3,
            "children": rows,
        }

    return {"type": "container", "direction": "horizontal", "children": [sidebar, main]}


def _comparison(worksheet_names: list[str]) -> dict[str, Any]:
    """Side-by-side equal-weight charts for comparison.

    Wraps to 2 rows if more than 3 charts.
    """
    names = list(worksheet_names)
    if not names:
        return {"type": "container", "direction": "horizontal", "children": []}

    if len(names) <= 3:
        return {
            "type": "container",
            "direction": "horizontal",
            "children": [{"type": "worksheet", "name": n, "weight": 1} for n in names],
        }

    # Wrap into rows of 2-3
    rows = _make_grid_rows(names, cols=3)
    return {"type": "container", "direction": "vertical", "children": rows}


def _overview(worksheet_names: list[str]) -> dict[str, Any]:
    """Featured chart on top, detail charts in a row below.

    Classic "big chart + details" pattern.
    """
    names = list(worksheet_names)
    if not names:
        return {"type": "container", "direction": "vertical", "children": []}

    if len(names) == 1:
        return {"type": "worksheet", "name": names[0], "weight": 1}

    featured = {"type": "worksheet", "name": names[0], "weight": 2}
    detail_row = {
        "type": "container",
        "direction": "horizontal",
        "weight": 1,
        "children": [{"type": "worksheet", "name": n, "weight": 1} for n in names[1:]],
    }
    return {"type": "container", "direction": "vertical", "children": [featured, detail_row]}


def _grid(worksheet_names: list[str]) -> dict[str, Any]:
    """Balanced grid layout.

    2 columns for 2-4 charts, 3 columns for 5+.
    """
    names = list(worksheet_names)
    if not names:
        return {"type": "container", "direction": "vertical", "children": []}

    if len(names) == 1:
        return {"type": "worksheet", "name": names[0], "weight": 1}

    cols = 3 if len(names) >= 5 else 2
    rows = _make_grid_rows(names, cols=cols)
    return {"type": "container", "direction": "vertical", "children": rows}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_grid_rows(names: list[str], cols: int) -> list[dict[str, Any]]:
    """Split worksheet names into horizontal rows of ``cols`` items."""
    rows: list[dict[str, Any]] = []
    for i in range(0, len(names), cols):
        chunk = names[i : i + cols]
        if len(chunk) == 1:
            rows.append({"type": "worksheet", "name": chunk[0], "weight": 1})
        else:
            rows.append({
                "type": "container",
                "direction": "horizontal",
                "weight": 1,
                "children": [{"type": "worksheet", "name": n, "weight": 1} for n in chunk],
            })
    return rows


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, tuple[callable, str]] = {
    "executive-summary": (_executive_summary, "Title + KPI row + chart grid below"),
    "kpi-detail": (_kpi_detail, "Left KPI sidebar + main chart area"),
    "comparison": (_comparison, "Side-by-side equal charts for comparison"),
    "overview": (_overview, "Big chart on top, details below"),
    "grid": (_grid, "Balanced grid layout"),
}

TEMPLATE_NAMES: list[str] = list(_TEMPLATES.keys())


def get_template(name: str, worksheet_names: list[str]) -> dict[str, Any]:
    """Get a layout dict for the named template.

    Args:
        name: Template name (see ``TEMPLATE_NAMES``).
        worksheet_names: List of worksheet names to place in the layout.

    Returns:
        FlexNode-compatible layout dict.

    Raises:
        ValueError: If the template name is not recognized.
    """
    entry = _TEMPLATES.get(name)
    if entry is None:
        raise ValueError(
            f"Unknown template '{name}'. Available: {', '.join(TEMPLATE_NAMES)}"
        )
    factory, _ = entry
    return factory(worksheet_names)


def list_templates() -> list[dict[str, str]]:
    """List available templates with descriptions."""
    return [
        {"name": name, "description": desc}
        for name, (_, desc) in _TEMPLATES.items()
    ]
