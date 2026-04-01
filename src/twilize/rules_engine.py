"""Dashboard creation rules engine.

Loads rules from ``rules/dashboard_rules.yaml`` (or a user-supplied path)
and validates tool arguments **before** execution.  Returns structured
violations (errors block the call, warnings are appended to the response).

The engine also provides auto-fix capabilities: when a rule violation is
fixable (e.g. adding ``sort_descending`` to a bar chart), the engine
modifies the arguments in-place so the tool produces correct output.

Usage from MCP tools::

    engine = get_rules_engine()
    violations = engine.check_configure_chart(tool_kwargs)
    # errors  → block execution, return violation messages
    # warnings → append to tool response after execution
    fixed_kwargs = engine.auto_fix_configure_chart(tool_kwargs)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# YAML loader — stdlib only, no PyYAML dependency required
# ---------------------------------------------------------------------------

try:
    import yaml as _yaml  # type: ignore[import-untyped]

    def _load_yaml(path: Path) -> dict:
        with path.open("r", encoding="utf-8") as f:
            return _yaml.safe_load(f) or {}

except ImportError:
    import json as _json
    import re as _re

    def _load_yaml(path: Path) -> dict:  # type: ignore[misc]
        """Minimal YAML subset parser — handles the dashboard_rules.yaml
        structure without requiring PyYAML.  Supports scalars, lists,
        nested dicts, and comments.  Falls back to JSON if available."""
        json_path = path.with_suffix(".json")
        if json_path.exists():
            with json_path.open("r", encoding="utf-8") as f:
                return _json.load(f)

        # Simple line-by-line YAML parser for our known structure
        result: dict[str, Any] = {}
        stack: list[tuple[int, dict]] = [(-1, result)]

        with path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.rstrip()
                stripped = line.lstrip()

                # Skip empty lines and comments
                if not stripped or stripped.startswith("#"):
                    continue

                indent = len(line) - len(stripped)

                # Pop stack to correct nesting level
                while len(stack) > 1 and indent <= stack[-1][0]:
                    stack.pop()

                current = stack[-1][1]

                # List item
                if stripped.startswith("- "):
                    value = stripped[2:].strip().strip('"').strip("'")
                    # Find the parent key's list
                    if isinstance(current, dict):
                        # Find last key that should hold a list
                        for k in reversed(list(current.keys())):
                            if isinstance(current[k], list):
                                current[k].append(_parse_scalar(value))
                                break
                    continue

                # Key: value pair
                if ":" in stripped:
                    key, _, val = stripped.partition(":")
                    key = key.strip().strip('"').strip("'")
                    val = val.strip()

                    if not val:
                        # Nested dict or list — create empty dict, check next lines
                        new_dict: dict[str, Any] = {}
                        current[key] = new_dict
                        stack.append((indent, new_dict))
                    elif val.startswith("[") and val.endswith("]"):
                        # Inline list
                        items = [
                            _parse_scalar(v.strip().strip('"').strip("'"))
                            for v in val[1:-1].split(",") if v.strip()
                        ]
                        current[key] = items
                    else:
                        current[key] = _parse_scalar(val.strip('"').strip("'"))

        return result


def _parse_scalar(val: str) -> Any:
    """Parse a YAML scalar value."""
    if val.lower() == "true":
        return True
    if val.lower() == "false":
        return False
    if val.lower() in ("null", "~", ""):
        return None
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val.strip('"').strip("'")


# ---------------------------------------------------------------------------
# Rule violation data structure
# ---------------------------------------------------------------------------

@dataclass
class RuleViolation:
    """A single rule violation found during validation."""

    rule_id: str
    severity: str          # "error" | "warning" | "info"
    message: str           # Plain-English explanation
    suggestion: str        # What to do instead
    auto_fixable: bool = False

    def format(self) -> str:
        prefix = {"error": "ERROR", "warning": "WARNING", "info": "INFO"}.get(
            self.severity, "INFO"
        )
        lines = [f"  [{prefix}] {self.rule_id}: {self.message}"]
        if self.suggestion:
            lines.append(f"           -> {self.suggestion}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rules Engine
# ---------------------------------------------------------------------------

# Package-level default rules path — look inside the package first (pip install),
# then fall back to the repo root (development mode).
_PACKAGE_RULES_PATH = Path(__file__).resolve().parent / "rules" / "dashboard_rules.yaml"
_REPO_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "rules" / "dashboard_rules.yaml"
_DEFAULT_RULES_PATH = (
    _PACKAGE_RULES_PATH if _PACKAGE_RULES_PATH.exists() else _REPO_RULES_PATH
)


class RulesEngine:
    """Loads and evaluates dashboard creation rules.

    Instantiate once per session (or per server start) and call the
    ``check_*`` methods before each tool execution.
    """

    def __init__(self, rules_path: str | Path | None = None):
        self._path = Path(rules_path) if rules_path else _DEFAULT_RULES_PATH
        if self._path.exists():
            self.rules: dict[str, Any] = _load_yaml(self._path)
            logger.info("Rules engine loaded from %s", self._path)
        else:
            self.rules = {}
            logger.warning("Rules file not found at %s — running without rules", self._path)

    # -- helpers ----------------------------------------------------------

    def _r(self, section: str) -> dict[str, Any]:
        """Get a rules section, returning empty dict if absent.

        Supports both the legacy key format (``kpi_rules``, ``bar_chart_rules``)
        and the unified format (``kpi``, ``charts``, ``layout``).  The unified
        format uses shorter top-level keys that match the accessor functions
        in ``dashboard_rules.py``.
        """
        val = self.rules.get(section)
        if val is not None:
            return val
        # Fallback mappings: new → legacy and legacy → new
        _ALIASES: dict[str, str] = {
            "kpi_rules": "kpi",
            "chart_count_rules": "charts",
            "layout_rules": "layout",
            "kpi": "kpi_rules",
            "charts": "chart_count_rules",
            "layout": "layout_rules",
        }
        alias = _ALIASES.get(section, "")
        if alias:
            return self.rules.get(alias, {})
        return {}

    def _severity(self, section: str, default: str = "warning") -> str:
        return self._r(section).get("severity", default)

    # =====================================================================
    # CHECK: configure_chart
    # =====================================================================

    def check_configure_chart(self, kwargs: dict[str, Any]) -> list[RuleViolation]:
        """Validate configure_chart arguments against rules."""
        violations: list[RuleViolation] = []
        mark_type = kwargs.get("mark_type", "")

        # --- Bar chart rules ---
        if mark_type == "Bar":
            br = self._r("bar_chart_rules")
            if br:
                # Sort enforcement
                if br.get("must_sort_descending") and not kwargs.get("sort_descending"):
                    measure_cols = kwargs.get("columns", [])
                    suggestion = ""
                    if measure_cols:
                        suggestion = f"Add sort_descending='{measure_cols[0]}'"
                    violations.append(RuleViolation(
                        rule_id="bar.unsorted",
                        severity=self._severity("bar_chart_rules"),
                        message="Bar charts must be sorted descending by the measure.",
                        suggestion=suggestion,
                        auto_fixable=bool(measure_cols),
                    ))

                # Time series guard
                if br.get("never_for_time_series"):
                    all_fields = (
                        kwargs.get("columns", []) + kwargs.get("rows", [])
                    )
                    temporal_keywords = ("MONTH(", "YEAR(", "QUARTER(", "WEEK(", "DAY(")
                    for f in all_fields:
                        if any(f.upper().startswith(kw) for kw in temporal_keywords):
                            violations.append(RuleViolation(
                                rule_id="bar.temporal",
                                severity="error",
                                message=f"Bar charts should not be used for time series (field: {f}).",
                                suggestion="Use a Line or Area chart for temporal data.",
                            ))
                            break

        # --- Map rules ---
        if mark_type == "Map":
            mr = self._r("map_rules")
            if mr:
                if not kwargs.get("geographic_field") and not kwargs.get("detail"):
                    violations.append(RuleViolation(
                        rule_id="map.no_geo_field",
                        severity="error",
                        message="Map chart requires a geographic_field.",
                        suggestion="Add geographic_field parameter or use a Bar chart instead.",
                    ))
                required = mr.get("required_encodings", [])
                if "color" in required and not kwargs.get("color"):
                    violations.append(RuleViolation(
                        rule_id="map.no_color",
                        severity=self._severity("map_rules"),
                        message="Map charts should use color encoding for a measure.",
                        suggestion="Add color='<measure>' for a heat-map effect.",
                        auto_fixable=False,
                    ))
                if "tooltip" in required and not kwargs.get("tooltip"):
                    violations.append(RuleViolation(
                        rule_id="map.no_tooltip",
                        severity=self._severity("map_rules"),
                        message="Map charts should include a tooltip for detail-on-demand.",
                        suggestion="Add tooltip='<measure>' for hover detail.",
                        auto_fixable=False,
                    ))

        # --- Pie chart rules ---
        if mark_type == "Pie":
            pr = self._r("pie_chart_rules")
            if pr:
                max_slices = pr.get("max_slices", 5)
                # We can't know slice count from args alone, but we can warn
                violations.append(RuleViolation(
                    rule_id="pie.slice_reminder",
                    severity="info",
                    message=f"Pie charts must have {max_slices} or fewer slices.",
                    suggestion="If the dimension has more values, use a Bar chart instead.",
                ))

        # --- Line chart rules ---
        if mark_type in ("Line", "Area"):
            lr = self._r("line_chart_rules")
            if lr and lr.get("max_color_series"):
                max_series = lr["max_color_series"]
                violations.append(RuleViolation(
                    rule_id="line.series_reminder",
                    severity="info",
                    message=f"Limit color series to {max_series} lines to avoid spaghetti.",
                    suggestion="Use a filter or Top N to reduce series count.",
                ))

        # --- Scatter plot rules ---
        if mark_type == "Scatterplot":
            sr = self._r("scatter_plot_rules")
            if sr:
                violations.append(RuleViolation(
                    rule_id="scatter.data_points_reminder",
                    severity="info",
                    message=f"Scatter plots need >= {sr.get('min_data_points', 15)} distinct visual data points.",
                    suggestion="If aggregation produces fewer points, use a Bar chart.",
                ))

        # --- KPI / Text rules ---
        if mark_type == "Text":
            kr = self._r("kpi_rules")
            if kr:
                mv = kwargs.get("measure_values", [])
                max_metrics = kr.get("max_metrics_per_kpi_card", kr.get("max_kpis", 5))
                if len(mv) > max_metrics:
                    violations.append(RuleViolation(
                        rule_id="kpi.too_many_metrics",
                        severity=self._severity("kpi_rules"),
                        message=(
                            f"KPI card has {len(mv)} metrics but max is {max_metrics}. "
                            f"Too many metrics dilute impact."
                        ),
                        suggestion=f"Keep only the {max_metrics} most important metrics.",
                        auto_fixable=True,
                    ))

        return violations

    # =====================================================================
    # CHECK: add_dashboard
    # =====================================================================

    def check_add_dashboard(
        self,
        worksheet_names: list[str],
        layout: Any = None,
    ) -> list[RuleViolation]:
        """Validate add_dashboard arguments against rules."""
        violations: list[RuleViolation] = []

        # --- Chart count ---
        cc = self._r("chart_count_rules")
        if cc:
            max_charts = cc.get("max_charts_per_dashboard", cc.get("max_charts", 5))
            if len(worksheet_names) > max_charts:
                violations.append(RuleViolation(
                    rule_id="dashboard.too_many_charts",
                    severity=self._severity("chart_count_rules", "error"),
                    message=(
                        f"Dashboard has {len(worksheet_names)} worksheets but max is {max_charts}."
                    ),
                    suggestion="Remove the least important charts to stay within the limit.",
                ))

        # --- Layout checks ---
        lr = self._r("layout_rules")
        if lr and lr.get("require_filter_sidebar"):
            # Check if layout dict contains a filter zone
            if isinstance(layout, dict) and not _layout_has_filter(layout):
                violations.append(RuleViolation(
                    rule_id="layout.no_filters",
                    severity=self._severity("layout_rules"),
                    message="Dashboard layout has no filter sidebar or filter bar.",
                    suggestion="Add a filter zone to the layout for user interactivity.",
                ))

        return violations

    # =====================================================================
    # AUTO-FIX: configure_chart
    # =====================================================================

    def auto_fix_configure_chart(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Apply automatic fixes to configure_chart arguments.

        Returns a (possibly modified) copy of kwargs.
        """
        fixed = dict(kwargs)
        mark_type = fixed.get("mark_type", "")

        # Bar chart: auto-add sort_descending
        if mark_type == "Bar":
            br = self._r("bar_chart_rules")
            if br and br.get("auto_fix_sort") and not fixed.get("sort_descending"):
                measure_cols = fixed.get("columns", [])
                if measure_cols:
                    fixed["sort_descending"] = measure_cols[0]
                    logger.info("Auto-fix: added sort_descending='%s'", measure_cols[0])

        # KPI: truncate excess metrics
        if mark_type == "Text":
            kr = self._r("kpi_rules")
            if kr:
                mv = fixed.get("measure_values", [])
                max_metrics = kr.get("max_metrics_per_kpi_card", 5)
                if len(mv) > max_metrics:
                    fixed["measure_values"] = mv[:max_metrics]
                    logger.info(
                        "Auto-fix: trimmed measure_values from %d to %d",
                        len(mv), max_metrics,
                    )

        return fixed

    # =====================================================================
    # AUTO-FIX: KPI title shortening
    # =====================================================================

    def shorten_kpi_title(self, title: str) -> str:
        """Apply shorten_names mappings to a KPI worksheet title."""
        kr = self._r("kpi_rules")
        if not kr:
            return title
        mappings = kr.get("shorten_names", {})
        for long_name, short_name in mappings.items():
            if long_name in title:
                title = title.replace(long_name, short_name)
        return title

    # =====================================================================
    # THEME: get required theme
    # =====================================================================

    def get_required_theme(self) -> str | None:
        """Return the default theme name if theme is required, else None."""
        tr = self._r("theme_rules")
        if tr and tr.get("require_theme"):
            return tr.get("default_theme", "modern-light")
        return None

    # =====================================================================
    # FORMAT: violations as text
    # =====================================================================

    def format_violations(self, violations: list[RuleViolation]) -> str:
        """Format violations into a human-readable block."""
        if not violations:
            return ""
        lines = ["RULE VIOLATIONS:"]
        for v in violations:
            lines.append(v.format())
        return "\n".join(lines)

    def errors(self, violations: list[RuleViolation]) -> list[RuleViolation]:
        return [v for v in violations if v.severity == "error"]

    def warnings(self, violations: list[RuleViolation]) -> list[RuleViolation]:
        return [v for v in violations if v.severity == "warning"]

    # =====================================================================
    # SUMMARY: dump active rules as text (for MCP tool)
    # =====================================================================

    def summarize(self) -> str:
        """Return a human-readable summary of all active rules."""
        if not self.rules:
            return "No rules loaded."

        lines = [
            "=== ACTIVE DASHBOARD CREATION RULES ===",
            f"Source: {self._path}",
            f"Version: {self.rules.get('version', 'unknown')}",
            "",
        ]

        section_labels = {
            "kpi_rules": "KPI / Big Ass Number",
            "chart_count_rules": "Chart Count & Composition",
            "map_rules": "Map Usage",
            "theme_rules": "Theme & Styling",
            "bar_chart_rules": "Bar Charts",
            "line_chart_rules": "Line Charts",
            "pie_chart_rules": "Pie Charts",
            "scatter_plot_rules": "Scatter Plots",
            "layout_rules": "Layout & Information Hierarchy",
            "aggregation_rules": "Aggregation",
            "number_format_rules": "Number Formatting",
        }

        for key, label in section_labels.items():
            section = self._r(key)
            if not section:
                continue
            sev = section.get("severity", "warning")
            lines.append(f"## {label}  [severity: {sev}]")
            for k, v in section.items():
                if k == "severity":
                    continue
                if isinstance(v, dict):
                    lines.append(f"  {k}:")
                    for sk, sv in v.items():
                        lines.append(f"    {sk}: {sv}")
                elif isinstance(v, list):
                    lines.append(f"  {k}: {', '.join(str(i) for i in v)}")
                else:
                    lines.append(f"  {k}: {v}")
            lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helper: check if a layout dict contains filter zones
# ---------------------------------------------------------------------------

def _layout_has_filter(layout: dict[str, Any]) -> bool:
    """Recursively check whether a layout dict contains a filter zone."""
    if layout.get("type") in ("filter", "paramctrl", "color", "filter_panel"):
        return True
    # C3 template marker
    if layout.get("_c3_template") and layout.get("_filters"):
        return True
    for child in layout.get("children", []):
        if isinstance(child, dict) and _layout_has_filter(child):
            return True
    return False


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_engine: RulesEngine | None = None


def get_rules_engine(rules_path: str | Path | None = None) -> RulesEngine:
    """Return the singleton RulesEngine, creating it on first call."""
    global _engine
    if _engine is None:
        _engine = RulesEngine(rules_path)
    return _engine


def reset_rules_engine() -> None:
    """Reset the singleton (useful for testing or hot-reload)."""
    global _engine
    _engine = None
