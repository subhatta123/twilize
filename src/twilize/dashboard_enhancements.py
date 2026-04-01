"""Automatic dashboard enhancements — filters, actions, and validation.

Post-processing layer that both pipelines call after creating a dashboard
to add interactivity (filters, cross-sheet actions) and validate chart
suggestions (remove invalid maps, enforce limits, deduplicate).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from twilize.chart_suggester import (
    ChartSuggestion,
    DashboardSuggestion,
    ShelfAssignment,
    deduplicate_charts,
    smart_aggregation,
)
from twilize.csv_to_hyper import ClassifiedSchema

if TYPE_CHECKING:
    from twilize.twb_editor import TWBEditor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 8A — Auto-filter selection
# ---------------------------------------------------------------------------

def select_auto_filters(
    classified: ClassifiedSchema,
    max_filters: int = 5,
) -> list[dict]:
    """Pick the best filter candidates from the classified schema.

    Returns filter dicts compatible with ``configure_chart()``'s *filters*
    kwarg.  Empty ``values`` means "show all" — the field appears as a
    quick-filter control in Tableau.

    Selection heuristics:
    * Categorical dimensions with cardinality 3–20 (good dropdowns).
    * Temporal fields (date range filters).
    * Skip geographic fields (not useful as quick filters).
    * Rank by cardinality "sweetness" (prefer 4–12).
    """
    candidates: list[tuple[int, dict]] = []
    # Collect temporal fields separately — we only keep the best one
    # to avoid confusing overlapping date filters (e.g. Order Date AND
    # Ship Date together create contradictory UX).
    temporal_candidates: list[tuple[int, dict]] = []

    import re as _re

    def _is_temporal_field(col) -> bool:
        """Detect temporal fields — both native date types and YEAR()/MONTH() wrappers.

        When data arrives from Tableau Extensions API, fields like YEAR(Order Date)
        come as integers, not dates.  We still need to group them as temporal.
        """
        if col.semantic_type == "temporal":
            return True
        # Detect YEAR(...), MONTH(...), QUARTER(...) wrapper patterns
        name = col.spec.name
        if _re.match(r'^(YEAR|MONTH|QUARTER|DAY|WEEK)\(', name, _re.IGNORECASE):
            return True
        return False

    for col in classified.dimensions:
        # Skip geographic fields
        if col.semantic_type == "geographic":
            continue

        card = col.spec.cardinality

        if _is_temporal_field(col):
            # Prefer "order" dates over "ship"/"delivery" dates; generic
            # date fields get a middle score.
            lower_name = col.spec.name.lower()
            if "order" in lower_name or "purchase" in lower_name:
                score = 90
            elif "ship" in lower_name or "deliver" in lower_name:
                score = 50
            else:
                score = 80
            temporal_candidates.append((score, {
                "type": "categorical",
                "field": col.spec.name,
                "values": [],
            }))
            continue

        # Categorical dims — prefer moderate cardinality
        if 2 <= card <= 50:
            # "Sweet spot" scoring: peak at 4–12
            if 4 <= card <= 12:
                score = 100
            elif card <= 20:
                score = 70
            elif card <= 3:
                score = 60
            else:
                score = 40  # Higher cardinality still useful
            candidates.append((score, {
                "type": "categorical",
                "field": col.spec.name,
                "values": [],
            }))

    # Add only the best temporal field (avoid contradictory date filters)
    if temporal_candidates:
        temporal_candidates.sort(key=lambda x: x[0], reverse=True)
        candidates.append(temporal_candidates[0])

    # Sort by score descending
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [c[1] for c in candidates[:max_filters]]


# ---------------------------------------------------------------------------
# 8B — Auto dashboard actions (cross-sheet filter / highlight)
# ---------------------------------------------------------------------------

def auto_add_actions(
    editor: TWBEditor,
    dashboard_name: str,
    worksheet_names: list[str],
    classified: ClassifiedSchema,
) -> list[str]:
    """Wire cross-sheet filter and highlight actions automatically.

    Adds:
    * 1 filter action — clicking the first worksheet filters all others
      by the best shared categorical dimension.
    * 1 highlight action — hovering highlights related marks across sheets.

    Returns list of confirmation messages.
    """
    if len(worksheet_names) < 2:
        return []

    # Find the best categorical dimension for cross-filtering
    best_dim = _best_action_dimension(classified)
    if not best_dim:
        return []

    results: list[str] = []

    # Filter action: first worksheet → all others
    source = worksheet_names[0]
    for target in worksheet_names[1:]:
        try:
            msg = editor.add_dashboard_action(
                dashboard_name=dashboard_name,
                action_type="filter",
                source_sheet=source,
                target_sheet=target,
                fields=[best_dim],
                event_type="on-select",
                caption=f"Filter by {best_dim}",
            )
            results.append(msg)
        except Exception as exc:
            logger.warning("Failed to add filter action: %s", exc)

    # Highlight action: first worksheet → all others
    for target in worksheet_names[1:]:
        try:
            msg = editor.add_dashboard_action(
                dashboard_name=dashboard_name,
                action_type="highlight",
                source_sheet=source,
                target_sheet=target,
                fields=[best_dim],
                event_type="on-select",
                caption=f"Highlight {best_dim}",
            )
            results.append(msg)
        except Exception as exc:
            logger.warning("Failed to add highlight action: %s", exc)

    return results


def _best_action_dimension(classified: ClassifiedSchema) -> str:
    """Pick the best categorical dimension for cross-sheet actions.

    Prefers moderate cardinality (3–15), non-geographic, non-temporal.
    """
    best_name = ""
    best_score = -1

    for col in classified.dimensions:
        if col.semantic_type in ("geographic", "temporal"):
            continue
        card = col.spec.cardinality
        if card < 2:
            continue
        if 3 <= card <= 15:
            score = 100
        elif card <= 2:
            score = 30
        else:
            score = max(10, 80 - card)

        if score > best_score:
            best_score = score
            best_name = col.spec.name

    return best_name


# ---------------------------------------------------------------------------
# 8D — Suggestion validation
# ---------------------------------------------------------------------------

def validate_suggestion(
    suggestion: DashboardSuggestion,
    classified: ClassifiedSchema,
    max_charts: int = 5,
    rules: dict | None = None,
) -> DashboardSuggestion:
    """Validate and clean up a dashboard suggestion.

    1. Remove Map charts when no geographic fields exist **or** when
       geographic data quality is poor (>threshold null/unknown values).
       Replaced maps get a Bar-chart alternative.
    2. Deduplicate charts (same type + same fields).
    3. Enforce max_charts limit.
    """
    charts = list(suggestion.charts)

    # Resolve map null thresholds from rules
    from twilize.dashboard_rules import (
        map_latlong_null_threshold as _latlong_thresh,
        map_null_threshold as _name_thresh,
    )
    latlong_threshold = _latlong_thresh(rules) if rules else 0.20
    name_threshold = _name_thresh(rules) if rules else 0.10

    # --- Remove invalid or low-quality maps ---
    has_geo = bool(classified.geographic)
    geo_quality_ok = False
    removal_reason = "no geographic fields in data"

    if has_geo:
        geo_col = classified.geographic[0]
        total = geo_col.spec.total_rows or classified.row_count

        # Check if geo field has actual lat/long numeric data vs just names
        # String-type geo fields rely on Tableau geocoding which may fail
        geo_name_lower = geo_col.spec.name.lower()
        _LATLONG_KW = {"lat", "latitude", "lng", "lon", "longitude"}
        is_latlong = any(kw in geo_name_lower for kw in _LATLONG_KW)

        if total > 0:
            null_ratio = geo_col.spec.null_count / total
            # Stricter threshold for non-lat/long fields (geocoded names)
            threshold = latlong_threshold if is_latlong else name_threshold
            geo_quality_ok = null_ratio < threshold
            if not geo_quality_ok:
                removal_reason = (
                    f"geographic field '{geo_col.spec.name}' has "
                    f"{null_ratio:.0%} null/unknown values (threshold: {threshold:.0%})"
                )
        elif not is_latlong and geo_col.spec.cardinality == 0:
            # No data and not lat/long — can't geocode
            geo_quality_ok = False
            removal_reason = f"geographic field '{geo_col.spec.name}' has no data"
        else:
            geo_quality_ok = True  # can't determine quality; allow maps

    if not has_geo or not geo_quality_ok:
        map_charts = [c for c in charts if c.chart_type == "Map"]
        if map_charts:
            logger.info(
                "Removed %d Map chart(s) — %s",
                len(map_charts),
                removal_reason,
            )
            # Replace each removed Map with a Bar-chart alternative
            for mc in map_charts:
                replacement = _map_replacement(mc, classified)
                if replacement:
                    charts.append(replacement)
        charts = [c for c in charts if c.chart_type != "Map"]

    # Deduplicate — one chart per non-KPI type for dashboard variety
    charts = deduplicate_charts(charts, max_per_type=1)

    # Enforce max
    charts = charts[:max_charts]

    return DashboardSuggestion(
        charts=charts,
        layout=suggestion.layout,
        title=suggestion.title,
        template=suggestion.template,
        layout_dict=suggestion.layout_dict,
    )


def _map_replacement(
    map_chart: ChartSuggestion,
    classified: ClassifiedSchema,
) -> ChartSuggestion | None:
    """Create a Bar-chart replacement for a removed Map chart.

    Preserves the measure from the original map and finds the best
    categorical dimension for the rows shelf.
    """
    geo_field: str | None = None
    measure_shelf: ShelfAssignment | None = None

    for sh in map_chart.shelves:
        if sh.shelf == "detail":
            geo_field = sh.field_name
        elif sh.aggregation:
            measure_shelf = sh

    if not measure_shelf:
        return None

    # Prefer a non-geographic categorical dim with moderate cardinality
    cat_dims = [
        c for c in classified.dimensions
        if c.semantic_type == "categorical" and c.spec.cardinality <= 20
    ]
    dim_name = cat_dims[0].spec.name if cat_dims else (geo_field or "")
    if not dim_name:
        return None

    return ChartSuggestion(
        chart_type="Bar",
        title=f"Which {dim_name} has the highest {measure_shelf.field_name}?",
        shelves=[
            ShelfAssignment(dim_name, "rows"),
            ShelfAssignment(
                measure_shelf.field_name, "columns", measure_shelf.aggregation,
            ),
        ],
        reason="Replaced Map (poor geographic data quality) with Bar chart",
        priority=map_chart.priority,
    )
