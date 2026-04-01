"""YAML-based dashboard rules loader.

Loads dashboard generation rules from a YAML file, with a built-in
default shipped inside the package.  Users can override rules by
placing a ``dashboard_rules.yaml`` file next to their data source
or in the current working directory.

Search order (first found wins):
    1. ``<data_dir>/dashboard_rules.yaml``
    2. ``<cwd>/dashboard_rules.yaml``
    3. Built-in ``references/dashboard_rules.yaml``

Rules are auto-generated from the built-in defaults and enforced
automatically by the pipeline.  Users can edit the built-in
``references/dashboard_rules.yaml`` or place overrides locally.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_RULES_FILENAME = "dashboard_rules.yaml"
_BUILTIN_PATH = Path(__file__).parent / "references" / _RULES_FILENAME

# Cached default rules (loaded once).
_default_rules: dict[str, Any] | None = None


def _load_yaml(path: Path) -> dict[str, Any]:
    """Read and parse a YAML file, returning an empty dict on failure."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Failed to load rules from %s: %s", path, exc)
        return {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into a copy of *base*."""
    merged = dict(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def get_default_rules() -> dict[str, Any]:
    """Return the built-in default rules (cached)."""
    global _default_rules
    if _default_rules is None:
        _default_rules = _load_yaml(_BUILTIN_PATH)
    return dict(_default_rules)  # shallow copy


def load_rules(data_path: str | Path | None = None) -> dict[str, Any]:
    """Load dashboard rules, checking user overrides first.

    Parameters
    ----------
    data_path:
        Path to the data source file (CSV, Hyper, etc.).  If provided,
        ``<data_dir>/dashboard_rules.yaml`` is checked first.  Also
        accepts the legacy ``csv_path`` name for backwards compatibility.

    Returns
    -------
    dict
        Merged rules (user overrides on top of built-in defaults).
    """
    defaults = get_default_rules()

    # 1. Check next to data source
    if data_path:
        candidate = Path(data_path).parent / _RULES_FILENAME
        if candidate.is_file():
            logger.info("Using dashboard rules from %s", candidate)
            return _deep_merge(defaults, _load_yaml(candidate))

    # 2. Check current working directory
    cwd_candidate = Path.cwd() / _RULES_FILENAME
    if cwd_candidate.is_file():
        logger.info("Using dashboard rules from %s", cwd_candidate)
        return _deep_merge(defaults, _load_yaml(cwd_candidate))

    # 3. Built-in defaults
    return defaults


# ── Convenience accessors ────────────────────────────────────────────

def kpi_number_format(field_name: str, aggregation: str, rules: dict[str, Any]) -> str:
    """Resolve a Tableau number format string for a KPI field.

    Evaluates ``kpi.number_formats`` rules top-to-bottom; first match wins.
    Falls back to ``kpi.aggregation_overrides`` then ``kpi.default_format``.
    """
    kpi = rules.get("kpi", {})

    # Aggregation-based override (COUNT/COUNTD → always "#,##0")
    agg_overrides = kpi.get("aggregation_overrides", {})
    if aggregation in agg_overrides:
        return agg_overrides[aggregation]

    # Field-name keyword matching
    lower = field_name.lower()
    for rule in kpi.get("number_formats", []):
        keywords = rule.get("match", [])
        if any(kw in lower for kw in keywords):
            return rule.get("format", kpi.get("default_format", "#,##0"))

    return kpi.get("default_format", "#,##0")


def kpi_font_size(rules: dict[str, Any]) -> int:
    """Return the KPI font size from rules."""
    return rules.get("kpi", {}).get("font_size", 15)


def kpi_row_height(rules: dict[str, Any]) -> int:
    """Return the KPI row height (px) from rules."""
    return rules.get("kpi", {}).get("row_height", 100)


def kpi_max(rules: dict[str, Any]) -> int:
    """Return the max number of KPIs from rules."""
    return rules.get("kpi", {}).get("max_kpis", 4)


def layout_template(rules: dict[str, Any]) -> str:
    """Return the preferred layout template name."""
    return rules.get("layout", {}).get("template", "executive-summary")


def max_charts(rules: dict[str, Any]) -> int:
    """Return the max charts per dashboard."""
    return rules.get("charts", {}).get("max_charts", 9)


def theme_name(rules: dict[str, Any]) -> str:
    """Return the theme preset name."""
    return rules.get("charts", {}).get("theme", "modern-light")


def max_filters(rules: dict[str, Any]) -> int:
    """Return the max number of quick-filters."""
    return rules.get("layout", {}).get("filters", {}).get("max_filters", 3)


def title_settings(rules: dict[str, Any]) -> dict[str, Any]:
    """Return title bar settings."""
    return rules.get("layout", {}).get("title", {})


def filter_settings(rules: dict[str, Any]) -> dict[str, Any]:
    """Return filter bar settings."""
    return rules.get("layout", {}).get("filters", {})


def bar_top_n(rules: dict[str, Any]) -> int:
    """Return the Top-N threshold for bar chart categories."""
    return rules.get("charts", {}).get("bar_top_n", 10)


def pie_max_slices(rules: dict[str, Any]) -> int:
    """Return the maximum pie chart slices (viz best practice: 5)."""
    return rules.get("charts", {}).get("pie_max_slices", 5)


def scatter_min_points(rules: dict[str, Any]) -> int:
    """Return the minimum data points for a meaningful scatter plot."""
    return rules.get("charts", {}).get("scatter_min_points", 15)


def map_null_threshold(rules: dict[str, Any]) -> float:
    """Return the max null ratio for geocoded-name map fields."""
    return rules.get("charts", {}).get("map_null_threshold", 0.10)


def map_latlong_null_threshold(rules: dict[str, Any]) -> float:
    """Return the max null ratio for lat/long map fields."""
    return rules.get("charts", {}).get("map_latlong_null_threshold", 0.20)


def dashboard_background(rules: dict[str, Any]) -> str:
    """Return the dashboard canvas background color."""
    return rules.get("layout", {}).get("background_color", "#e6e6e6")
