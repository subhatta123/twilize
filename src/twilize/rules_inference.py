"""Data-driven rules inference engine.

Analyzes a ClassifiedSchema to automatically generate number format rules,
aggregation rules, and chart recommendations — without relying on hardcoded
field-name keywords like "sales" or "discount".

The inferred rules are merged ON TOP of the YAML defaults, so:
  1. YAML defaults provide the baseline (admin-configurable)
  2. Schema inference overrides per-field formats based on actual data
  3. User/admin can still override via rules_yaml parameter

This makes the system work with ANY dataset (healthcare, education,
manufacturing, etc.) not just sales/retail.
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)


def infer_rules_from_schema(
    classified,  # ClassifiedSchema
    base_rules: dict[str, Any],
) -> dict[str, Any]:
    """Infer formatting and chart rules from actual data characteristics.

    Analyzes measure statistics (value ranges, decimals, cardinality) and
    column names to generate per-field number format overrides. Merges
    the inferences on top of ``base_rules``.

    Args:
        classified: ClassifiedSchema with dimension/measure/temporal info.
        base_rules: The loaded YAML rules (defaults + user overrides).

    Returns:
        Enhanced rules dict with data-driven ``kpi.number_formats`` entries
        prepended to the match list, and per-field ``_field_formats`` map.
    """
    from twilize.dashboard_rules import _deep_merge

    inferred: dict[str, Any] = {}
    field_formats: dict[str, str] = {}  # field_name → Tableau format string
    field_aggs: dict[str, str] = {}     # field_name → aggregation

    for col in classified.measures:
        name = col.spec.name
        samples = col.spec.sample_values
        fmt, agg = _infer_field_format(name, samples, col.spec)
        if fmt:
            field_formats[name] = fmt
        if agg:
            field_aggs[name] = agg

    # Build inferred kpi section
    if field_formats:
        inferred["_field_formats"] = field_formats
        logger.info("Inferred field formats: %s", field_formats)

    if field_aggs:
        inferred["_field_aggs"] = field_aggs
        logger.info("Inferred field aggregations: %s", field_aggs)

    # Merge inferred on top of base
    merged = _deep_merge(base_rules, inferred)
    return merged


def _infer_field_format(
    field_name: str,
    sample_values: list[str],
    spec: Any,
) -> tuple[str, str]:
    """Infer the best Tableau number format and aggregation for a field.

    Uses a priority chain:
      1. Value pattern analysis ($ prefix, % suffix, decimal places)
      2. Value range analysis (large numbers → abbreviate, small → full)
      3. Field name heuristics (fallback to keyword matching)

    Returns:
        (format_string, aggregation) — either may be empty if uncertain.
    """
    fmt = ""
    agg = ""

    # Parse numeric values from samples
    parsed = _parse_numeric_samples(sample_values)
    if not parsed:
        return fmt, agg

    # --- Pattern 1: Currency symbols in raw values ---
    raw_strings = [str(v).strip() for v in sample_values if v and str(v).strip()]
    has_dollar = any(s.startswith("$") or s.startswith("-$") for s in raw_strings)
    has_euro = any(s.startswith("\u20ac") for s in raw_strings)
    has_pound = any(s.startswith("\u00a3") for s in raw_strings)
    has_percent = any(s.endswith("%") for s in raw_strings)

    if has_dollar or has_euro or has_pound:
        prefix = "$" if has_dollar else ("\u20ac" if has_euro else "\u00a3")
        fmt = _currency_format(parsed, prefix)
        agg = "SUM"
        return fmt, agg

    if has_percent:
        fmt = "0.0%"
        agg = "AVG"
        return fmt, agg

    # --- Pattern 2: Value range analysis ---
    abs_values = [abs(v) for v in parsed if v != 0]
    if abs_values:
        median_val = sorted(abs_values)[len(abs_values) // 2]
        max_val = max(abs_values)
        min_val = min(abs_values)

        # All values between 0 and 1 (exclusive) → likely a rate/ratio
        if max_val < 1.0 and min_val >= 0:
            fmt = "0.0%"
            agg = "AVG"
            return fmt, agg

        # Small integers (0-100 range, no decimals) → likely counts/scores
        has_decimals = any(v != int(v) for v in parsed)
        if not has_decimals and max_val <= 1000:
            fmt = "#,##0"
            agg = "SUM"
            return fmt, agg

        # Large numbers (>10K) → abbreviate
        if median_val > 10000:
            fmt = "#,##0,K"
            agg = "SUM"
            return fmt, agg

        # Medium numbers with decimals
        if has_decimals:
            # Count typical decimal places
            dec_places = _typical_decimal_places(parsed)
            if dec_places <= 1:
                fmt = "#,##0.0"
            elif dec_places == 2:
                fmt = "#,##0.00"
            else:
                fmt = "#,##0.00"
            agg = "AVG" if median_val < 100 else "SUM"
            return fmt, agg

        # Default: integer with comma separator
        fmt = "#,##0"
        agg = "SUM"
        return fmt, agg

    # --- Pattern 3: Field name heuristics (fallback) ---
    # This is the existing keyword-based approach, kept as last resort
    from twilize.chart_suggester import smart_aggregation, _is_rate_field, _is_currency_field
    agg = smart_aggregation(field_name)
    if _is_rate_field(field_name):
        fmt = "0.0%"
    elif _is_currency_field(field_name):
        fmt = "$#,##0,K"
    else:
        fmt = "#,##0"

    return fmt, agg


def _parse_numeric_samples(samples: list[str]) -> list[float]:
    """Parse sample values into floats, stripping currency/percentage symbols."""
    parsed = []
    for v in samples:
        s = str(v).strip()
        if not s:
            continue
        # Strip common symbols
        s = s.replace(",", "").replace("$", "").replace("\u20ac", "").replace("\u00a3", "")
        s = s.replace("%", "").replace(" ", "")
        try:
            parsed.append(float(s))
        except (ValueError, TypeError):
            continue
    return parsed


def _currency_format(values: list[float], prefix: str = "$") -> str:
    """Choose a currency format based on value magnitude."""
    if not values:
        return f"{prefix}#,##0"
    abs_vals = [abs(v) for v in values if v != 0]
    if not abs_vals:
        return f"{prefix}#,##0"
    median = sorted(abs_vals)[len(abs_vals) // 2]
    if median >= 1_000_000:
        return f"{prefix}#,##0,,M"  # millions
    if median >= 10_000:
        return f"{prefix}#,##0,K"  # thousands
    if any(v != int(v) for v in values):
        return f"{prefix}#,##0.00"  # cents
    return f"{prefix}#,##0"


def _typical_decimal_places(values: list[float]) -> int:
    """Estimate the typical number of decimal places in parsed values."""
    dec_counts = []
    for v in values:
        if v == int(v):
            dec_counts.append(0)
        else:
            s = f"{v:.10f}".rstrip("0")
            dec_part = s.split(".")[-1] if "." in s else ""
            dec_counts.append(len(dec_part))
    if not dec_counts:
        return 0
    # Return the mode (most common)
    from collections import Counter
    return Counter(dec_counts).most_common(1)[0][0]


def infer_kpi_number_format(
    field_name: str,
    aggregation: str,
    rules: dict[str, Any],
) -> str:
    """Get the number format for a field, respecting YAML overrides.

    Priority:
      1. ``rules['kpi']['aggregation_overrides']`` — COUNT/COUNTD always win
      2. ``rules['kpi']['number_formats']`` — YAML keyword rules (admin-set)
      3. ``rules['_field_formats'][field_name]`` — data-inferred format
      4. ``rules['kpi']['default_format']`` — fallback

    YAML keyword rules take precedence over data inference because they
    represent intentional admin/user configuration (e.g. "sales → $#,##0,K").
    Data inference is a best-guess that can be wrong when values lack
    currency symbols or have ambiguous decimal patterns.
    """
    from twilize.dashboard_rules import kpi_number_format

    # Check YAML keyword rules first (includes aggregation overrides)
    kpi = rules.get("kpi", {})
    default_fmt = kpi.get("default_format", "#,##0")

    yaml_fmt = kpi_number_format(field_name, aggregation, rules)
    if yaml_fmt != default_fmt:
        # YAML had a specific keyword or aggregation match — use it
        return yaml_fmt

    # Fall back to data-inferred formats
    field_fmts = rules.get("_field_formats", {})
    if field_name in field_fmts:
        return field_fmts[field_name]

    # No specific match anywhere — use YAML default
    return default_fmt


def infer_aggregation(
    field_name: str,
    rules: dict[str, Any],
) -> str:
    """Get the aggregation for a field, checking inferred aggs first.

    Priority:
      1. ``rules['_field_aggs'][field_name]`` — data-inferred aggregation
      2. ``smart_aggregation(field_name)`` — keyword-based fallback
    """
    field_aggs = rules.get("_field_aggs", {})
    if field_name in field_aggs:
        return field_aggs[field_name]

    from twilize.chart_suggester import smart_aggregation
    return smart_aggregation(field_name)
