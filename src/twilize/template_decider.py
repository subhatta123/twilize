"""Template Decider — Brain 3.

Scores every template in the gallery against the current DataProfile
and chart suggestions, then returns a ranked list with reasoning.

The decider evaluates six scoring dimensions:
1. **chart_count_fit** — Does the template have the right number of zones?
2. **kpi_support**     — Does it accommodate the KPI count?
3. **geographic_support** — Does it have a large zone for maps?
4. **temporal_emphasis** — Does it prioritise trend charts?
5. **filter_placement** — Does the filter style match data needs?
6. **domain_affinity**  — Does the template suit this business domain?

Each dimension scores 0–20 (total 0–100 after normalization).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .data_profiler import DataProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# YAML loader (reuse from rules_engine)
# ---------------------------------------------------------------------------

try:
    import yaml as _yaml  # type: ignore[import-untyped]

    def _load_yaml(path: Path) -> dict:
        with path.open("r", encoding="utf-8") as f:
            return _yaml.safe_load(f) or {}
except ImportError:
    from .rules_engine import _load_yaml  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Gallery template data structure
# ---------------------------------------------------------------------------

@dataclass
class GalleryTemplate:
    """A single template loaded from the gallery."""

    name: str
    description: str = ""
    author: str = "twilize"
    recommended_theme: str = "modern-light"
    source_path: str = ""

    # Suitability criteria
    requires: dict[str, Any] = field(default_factory=dict)
    prefers: dict[str, Any] = field(default_factory=dict)
    suited_for_domains: list[str] = field(default_factory=list)

    # Zones
    zones: list[dict[str, Any]] = field(default_factory=list)

    # Auto-actions
    auto_actions: list[dict[str, Any]] = field(default_factory=list)

    # Derived properties
    @property
    def has_kpi_row(self) -> bool:
        return any(z.get("type") == "kpi_container" for z in self.zones)

    @property
    def kpi_slots(self) -> int:
        for z in self.zones:
            if z.get("type") == "kpi_container":
                return z.get("max_slots", 4)
        return 0

    @property
    def has_featured_zone(self) -> bool:
        """Has a single large worksheet zone (for maps, main charts)."""
        for z in self.zones:
            if z.get("type") == "worksheet" and z.get("weight", 0) >= 2:
                return True
        return False

    @property
    def chart_zone_count(self) -> int:
        """Count of chart slots (worksheet + chart_container slots)."""
        count = 0
        for z in self.zones:
            if z.get("type") == "worksheet":
                count += 1
            elif z.get("type") == "chart_container":
                count += z.get("slots", 2)
        return count

    @property
    def has_filter_sidebar(self) -> bool:
        return any(
            z.get("type") == "filter_panel" and z.get("position") in ("left", "right")
            for z in self.zones
        )

    @property
    def has_filter_bar(self) -> bool:
        return any(
            z.get("type") == "filter_panel" and z.get("position") == "top"
            for z in self.zones
        )

    @property
    def preferred_chart_types(self) -> set[str]:
        """All preferred chart types across all zones."""
        types: set[str] = set()
        for z in self.zones:
            for ct in z.get("preferred_chart_types", []):
                types.add(ct)
        return types


# ---------------------------------------------------------------------------
# Template Gallery — loads YAML templates from disk
# ---------------------------------------------------------------------------

# Look inside the package first (pip install), then repo root (dev mode).
_PACKAGE_GALLERY_DIR = Path(__file__).resolve().parent / "gallery"
_REPO_GALLERY_DIR = Path(__file__).resolve().parent.parent.parent / "templates" / "gallery"
_DEFAULT_GALLERY_DIR = (
    _PACKAGE_GALLERY_DIR if _PACKAGE_GALLERY_DIR.exists() else _REPO_GALLERY_DIR
)


class TemplateGallery:
    """Loads and manages template definitions from YAML files."""

    def __init__(self, gallery_dir: str | Path | None = None):
        self._dir = Path(gallery_dir) if gallery_dir else _DEFAULT_GALLERY_DIR
        self._templates: dict[str, GalleryTemplate] = {}
        self._load()

    def _load(self) -> None:
        if not self._dir.exists():
            logger.warning("Gallery directory not found: %s", self._dir)
            return

        for yaml_path in sorted(self._dir.glob("*.yaml")):
            try:
                raw = _load_yaml(yaml_path)
                tmpl = GalleryTemplate(
                    name=raw.get("name", yaml_path.stem),
                    description=raw.get("description", ""),
                    author=raw.get("author", "twilize"),
                    recommended_theme=raw.get("recommended_theme", "modern-light"),
                    source_path=str(yaml_path),
                    requires=raw.get("suitability", {}).get("requires", {}) or {},
                    prefers=raw.get("suitability", {}).get("prefers", {}) or {},
                    suited_for_domains=raw.get("suitability", {}).get("suited_for_domains", []),
                    zones=raw.get("zones", []),
                    auto_actions=raw.get("auto_actions", []),
                )
                self._templates[tmpl.name] = tmpl
                logger.debug("Loaded gallery template: %s", tmpl.name)
            except Exception as exc:
                logger.warning("Failed to load template %s: %s", yaml_path, exc)

    def all_templates(self) -> list[GalleryTemplate]:
        return list(self._templates.values())

    def get(self, name: str) -> GalleryTemplate | None:
        return self._templates.get(name)

    def names(self) -> list[str]:
        return list(self._templates.keys())

    def __len__(self) -> int:
        return len(self._templates)


# ---------------------------------------------------------------------------
# Template Score
# ---------------------------------------------------------------------------

@dataclass
class TemplateScore:
    """Scoring result for one template."""

    template_name: str
    total_score: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)
    reasoning: str = ""
    recommended_theme: str = "modern-light"


# ---------------------------------------------------------------------------
# Template Decider
# ---------------------------------------------------------------------------

class TemplateDecider:
    """Scores templates against a DataProfile + chart list."""

    def __init__(self, gallery: TemplateGallery | None = None):
        self.gallery = gallery or TemplateGallery()

    def decide(
        self,
        profile: DataProfile,
        chart_types: list[str] | None = None,
        kpi_count: int | None = None,
    ) -> list[TemplateScore]:
        """Score every gallery template and return ranked list.

        Args:
            profile: DataProfile from any adapter.
            chart_types: List of chart mark_types being built
                         (e.g. ["Bar", "Line", "Text", "Text"]).
            kpi_count: Override KPI count (else derived from chart_types).

        Returns:
            List of TemplateScore sorted best-first.
        """
        if chart_types is None:
            chart_types = []

        actual_kpis = kpi_count if kpi_count is not None else sum(
            1 for ct in chart_types if ct == "Text"
        )
        actual_charts = sum(1 for ct in chart_types if ct != "Text")
        non_text_types = [ct for ct in chart_types if ct != "Text"]

        scores: list[TemplateScore] = []

        for tmpl in self.gallery.all_templates():
            score = self._score(tmpl, profile, actual_kpis, actual_charts, non_text_types)
            scores.append(score)

        scores.sort(key=lambda s: s.total_score, reverse=True)
        return scores

    def _score(
        self,
        tmpl: GalleryTemplate,
        profile: DataProfile,
        actual_kpis: int,
        actual_charts: int,
        chart_types: list[str],
    ) -> TemplateScore:
        breakdown: dict[str, float] = {}
        reasons: list[str] = []

        # 1. Chart count fit (0–20)
        zone_count = tmpl.chart_zone_count
        diff = abs(zone_count - actual_charts)
        if diff == 0:
            breakdown["chart_count_fit"] = 20
            reasons.append(f"Perfect chart slot match ({zone_count} zones)")
        elif diff == 1:
            breakdown["chart_count_fit"] = 14
            reasons.append(f"Close chart slot match ({zone_count} zones vs {actual_charts} charts)")
        elif diff == 2:
            breakdown["chart_count_fit"] = 8
        else:
            breakdown["chart_count_fit"] = 2

        # 2. KPI support (0–20)
        if actual_kpis > 0:
            if tmpl.has_kpi_row:
                if tmpl.kpi_slots >= actual_kpis:
                    breakdown["kpi_support"] = 20
                    reasons.append(f"KPI row fits all {actual_kpis} KPIs")
                else:
                    breakdown["kpi_support"] = 12
                    reasons.append(f"KPI row has {tmpl.kpi_slots} slots but {actual_kpis} KPIs needed")
            else:
                breakdown["kpi_support"] = 2
        else:
            # No KPIs — slight preference for templates without large KPI rows
            if not tmpl.has_kpi_row:
                breakdown["kpi_support"] = 16
            else:
                breakdown["kpi_support"] = 10

        # 3. Geographic support (0–15)
        has_map = "Map" in chart_types
        if has_map:
            if tmpl.has_featured_zone and "Map" in tmpl.preferred_chart_types:
                breakdown["geographic_support"] = 15
                reasons.append("Featured zone ideal for map")
            elif tmpl.has_featured_zone:
                breakdown["geographic_support"] = 10
            else:
                breakdown["geographic_support"] = 3
        else:
            breakdown["geographic_support"] = 8  # neutral

        # 4. Temporal emphasis (0–15)
        line_count = sum(1 for ct in chart_types if ct in ("Line", "Area"))
        if line_count >= 2:
            if "grid" in tmpl.name or "comparison" in tmpl.name:
                breakdown["temporal_emphasis"] = 15
                reasons.append("Grid/comparison suits multiple trend charts")
            elif tmpl.has_featured_zone:
                breakdown["temporal_emphasis"] = 12
            else:
                breakdown["temporal_emphasis"] = 6
        elif line_count == 1:
            if tmpl.has_featured_zone:
                breakdown["temporal_emphasis"] = 15
                reasons.append("Featured zone suits primary trend chart")
            else:
                breakdown["temporal_emphasis"] = 8
        else:
            breakdown["temporal_emphasis"] = 8  # neutral

        # 5. Filter placement (0–15)
        filter_count = len(profile.good_filter_candidates())
        if filter_count > 3:
            if tmpl.has_filter_sidebar:
                breakdown["filter_placement"] = 15
                reasons.append("Filter sidebar accommodates many filters")
            elif tmpl.has_filter_bar:
                breakdown["filter_placement"] = 8
            else:
                breakdown["filter_placement"] = 3
        elif filter_count >= 1:
            if tmpl.has_filter_bar:
                breakdown["filter_placement"] = 15
            elif tmpl.has_filter_sidebar:
                breakdown["filter_placement"] = 10
            else:
                breakdown["filter_placement"] = 5
        else:
            breakdown["filter_placement"] = 10  # neutral

        # 6. Domain affinity (0–15)
        if profile.domain_hint and profile.domain_hint in tmpl.suited_for_domains:
            breakdown["domain_affinity"] = 15
            reasons.append(f"Template suited for {profile.domain_hint} domain")
        elif not profile.domain_hint:
            breakdown["domain_affinity"] = 8  # neutral
        else:
            breakdown["domain_affinity"] = 4

        # --- Hard requirements check ---
        # If a template has hard requirements that aren't met, penalize heavily
        req = tmpl.requires
        if req:
            if req.get("has_strong_geographic") and not profile.has_strong_geographic:
                breakdown["geographic_support"] = 0
                reasons.append("PENALTY: requires strong geographic data")
            if req.get("has_strong_temporal") and not profile.has_strong_temporal:
                breakdown["temporal_emphasis"] = 0
                reasons.append("PENALTY: requires strong temporal data")
            if req.get("has_ranking_dimension") and not profile.has_ranking_dimension:
                breakdown["chart_count_fit"] = max(0, breakdown.get("chart_count_fit", 0) - 5)

        total = sum(breakdown.values())
        reasoning = "; ".join(reasons) if reasons else "Default scoring"

        return TemplateScore(
            template_name=tmpl.name,
            total_score=total,
            breakdown=breakdown,
            reasoning=reasoning,
            recommended_theme=tmpl.recommended_theme,
        )


# ---------------------------------------------------------------------------
# Human-readable formatting
# ---------------------------------------------------------------------------

def format_recommendation(scores: list[TemplateScore], top_n: int = 3) -> str:
    """Format template recommendations as human-readable text."""
    lines = ["=== TEMPLATE RECOMMENDATIONS ===", ""]

    for i, s in enumerate(scores[:top_n], 1):
        rank = {1: "BEST", 2: "2nd", 3: "3rd"}.get(i, f"{i}th")
        lines.append(f"  {rank}: {s.template_name}  (score: {s.total_score:.0f}/100)")
        lines.append(f"        Theme: {s.recommended_theme}")
        lines.append(f"        Reason: {s.reasoning}")
        # Breakdown
        dims = sorted(s.breakdown.items(), key=lambda x: x[1], reverse=True)
        dim_parts = [f"{k}={v:.0f}" for k, v in dims]
        lines.append(f"        Breakdown: {', '.join(dim_parts)}")
        lines.append("")

    if len(scores) > top_n:
        others = [f"{s.template_name} ({s.total_score:.0f})" for s in scores[top_n:]]
        lines.append(f"  Also considered: {', '.join(others)}")

    return "\n".join(lines)


def format_gallery_listing(gallery: TemplateGallery) -> str:
    """Format all gallery templates as a readable listing."""
    lines = [
        "=== TEMPLATE GALLERY ===",
        f"Templates: {len(gallery)}",
        f"Location: {gallery._dir}",
        "",
    ]
    for tmpl in gallery.all_templates():
        lines.append(f"  {tmpl.name}")
        lines.append(f"    {tmpl.description}")
        lines.append(f"    Theme: {tmpl.recommended_theme}")
        lines.append(f"    Zones: {len(tmpl.zones)} | Chart slots: {tmpl.chart_zone_count} | KPI slots: {tmpl.kpi_slots}")
        lines.append(f"    Domains: {', '.join(tmpl.suited_for_domains) or 'any'}")
        if tmpl.requires:
            reqs = [f"{k}={v}" for k, v in tmpl.requires.items()]
            lines.append(f"    Requires: {', '.join(reqs)}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Singleton accessors
# ---------------------------------------------------------------------------

_gallery: TemplateGallery | None = None
_decider: TemplateDecider | None = None


def get_gallery(gallery_dir: str | Path | None = None) -> TemplateGallery:
    """Return the singleton TemplateGallery."""
    global _gallery
    if _gallery is None:
        _gallery = TemplateGallery(gallery_dir)
    return _gallery


def get_decider(gallery_dir: str | Path | None = None) -> TemplateDecider:
    """Return the singleton TemplateDecider."""
    global _decider
    if _decider is None:
        _decider = TemplateDecider(get_gallery(gallery_dir))
    return _decider
