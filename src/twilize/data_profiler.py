"""Universal data profiler — same DataProfile output for any Tableau data source.

The profiler inspects field metadata and produces a ``DataProfile`` that the
template decider and chart suggester consume.  Source adapters:

* ``from_csv(path)``           — CSV file (reuses csv_to_hyper classifier)
* ``from_workbook_fields(editor)`` — **any** already-connected data source
* ``from_hyper(path)``         — standalone Hyper extract
* ``from_extension_api(json)`` — Tableau Extensions API field metadata
* ``from_classified_schema(schema)`` — existing ClassifiedSchema bridge

The ``from_workbook_fields`` adapter is the most important: it means any
data source that is already connected in the workbook (MySQL, Tableau Server,
Hyper, Excel, etc.) can be profiled without source-specific code.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ProfiledField:
    """A single profiled field."""

    name: str
    role: str                  # "dimension" | "measure"
    semantic_type: str         # "categorical" | "temporal" | "geographic" | "currency" | "rate" | "count" | "numeric"
    datatype: str = "string"   # "string" | "integer" | "real" | "date" | "datetime" | "boolean"
    cardinality: int = 0       # distinct value count (0 = unknown)
    null_pct: float = 0.0      # percentage of null values (0-100)
    aggregation: str = ""      # recommended aggregation ("SUM", "AVG", "COUNTD")
    geo_role: str = ""         # "state" | "city" | "country" | "zipcode" | "latitude" | "longitude" | ""


@dataclass
class DataProfile:
    """Unified profile of a data source — produced by any adapter."""

    source_type: str = "unknown"     # "csv" | "hyper" | "mysql" | "tableau_server" | "workbook_fields" | "extension_api"
    row_count: int = 0

    dimensions: list[ProfiledField] = field(default_factory=list)
    measures: list[ProfiledField] = field(default_factory=list)

    domain_hint: str = ""            # auto-detected domain: "retail_sales", "finance", "marketing", etc.

    # Computed signals — what the data is "good for"
    has_strong_temporal: bool = False     # ≥ 1 temporal field with good grain
    has_strong_geographic: bool = False   # geo field with coverage > 80%
    has_ranking_dimension: bool = False   # categorical dim with 5-15 values
    has_correlation_pair: bool = False    # ≥ 2 measures suitable for scatter
    has_part_to_whole: bool = False       # categorical dim with ≤ 5 values
    kpi_count: int = 0                   # number of KPI-worthy measures

    @property
    def temporal(self) -> list[ProfiledField]:
        return [f for f in self.dimensions if f.semantic_type == "temporal"]

    @property
    def geographic(self) -> list[ProfiledField]:
        return [f for f in self.dimensions if f.semantic_type == "geographic"]

    @property
    def categorical(self) -> list[ProfiledField]:
        return [f for f in self.dimensions if f.semantic_type == "categorical"]

    def good_filter_candidates(self, max_cardinality: int = 50) -> list[ProfiledField]:
        """Dimensions suitable for quick-filter controls."""
        candidates = []
        for d in self.dimensions:
            if d.semantic_type == "geographic":
                continue
            if d.semantic_type == "temporal":
                candidates.append(d)
                continue
            if 2 <= d.cardinality <= max_cardinality:
                candidates.append(d)
        return candidates


# ---------------------------------------------------------------------------
# Semantic detection heuristics
# ---------------------------------------------------------------------------

_GEO_KEYWORDS = {
    "country", "state", "province", "city", "zip", "zipcode", "postal",
    "region", "county", "district", "territory", "latitude", "longitude",
    "lat", "lng", "lon", "geo", "address", "location",
}

_GEO_ROLES = {
    "country": "country", "state": "state", "province": "state",
    "city": "city", "zip": "zipcode", "zipcode": "zipcode",
    "postal": "zipcode", "latitude": "latitude", "longitude": "longitude",
    "lat": "latitude", "lng": "longitude", "lon": "longitude",
}

_TEMPORAL_KEYWORDS = {
    "date", "time", "datetime", "timestamp", "year", "month", "quarter",
    "week", "day", "hour", "minute", "period", "fiscal",
}

_RATE_KEYWORDS = {
    "discount", "margin", "rate", "ratio", "percentage", "pct",
    "share", "yield", "efficiency", "utilization", "conversion",
    "bounce", "churn", "retention", "satisfaction", "score", "rating",
}

_CURRENCY_KEYWORDS = {
    "sales", "profit", "revenue", "cost", "price", "amount", "total",
    "budget", "spend", "income", "expense", "fee", "tax", "payment",
    "gdp", "wage", "salary",
}

_COUNT_KEYWORDS = {
    "count", "quantity", "number", "num_", "qty", "orders",
    "transactions", "visits", "clicks",
}

_DOMAIN_SIGNATURES: dict[str, list[str]] = {
    "retail_sales": ["sales", "profit", "order", "customer", "product", "category", "discount"],
    "finance": ["revenue", "budget", "fiscal", "quarter", "account", "invoice", "payment"],
    "marketing": ["click", "impression", "campaign", "conversion", "bounce", "session", "engagement"],
    "operations": ["sensor", "uptime", "latency", "cpu", "memory", "throughput", "error"],
    "hr": ["employee", "salary", "department", "hire", "headcount", "tenure"],
    "healthcare": ["patient", "diagnosis", "treatment", "hospital", "readmission"],
    "logistics": ["shipment", "delivery", "warehouse", "route", "fleet", "tracking"],
    "real_estate": ["property", "listing", "sqft", "bedroom", "mortgage", "rent"],
}


def _detect_semantic_type(name: str, datatype: str, role: str) -> str:
    """Detect the semantic type of a field from its name and datatype."""
    lower = name.lower()

    if role == "measure":
        if any(kw in lower for kw in _RATE_KEYWORDS):
            return "rate"
        if any(kw in lower for kw in _CURRENCY_KEYWORDS):
            return "currency"
        if any(kw in lower for kw in _COUNT_KEYWORDS):
            return "count"
        return "numeric"

    # Dimension semantics
    if datatype in ("date", "datetime") or any(kw in lower for kw in _TEMPORAL_KEYWORDS):
        return "temporal"
    if any(kw in lower for kw in _GEO_KEYWORDS):
        return "geographic"

    # Check for YEAR()/MONTH() wrappers (from Tableau Extensions API)
    if re.match(r'^(YEAR|MONTH|QUARTER|DAY|WEEK)\(', name, re.IGNORECASE):
        return "temporal"

    return "categorical"


def _detect_geo_role(name: str) -> str:
    """Detect geographic role from field name."""
    lower = name.lower()
    for keyword, role in _GEO_ROLES.items():
        if keyword in lower:
            return role
    return ""


def _smart_aggregation(name: str) -> str:
    """Choose aggregation based on field name semantics."""
    lower = name.lower()
    if any(kw in lower for kw in _RATE_KEYWORDS):
        return "AVG"
    if any(kw in lower for kw in {"id", "key", "code", "identifier", "uuid"}):
        return "COUNTD"
    return "SUM"


def _detect_domain(all_field_names: str) -> str:
    """Detect business domain from field name patterns."""
    lower = all_field_names.lower()
    best_domain = ""
    best_score = 0
    for domain, keywords in _DOMAIN_SIGNATURES.items():
        score = sum(1 for kw in keywords if kw in lower)
        if score > best_score:
            best_score = score
            best_domain = domain
    return best_domain if best_score >= 2 else ""


def _compute_signals(profile: DataProfile) -> None:
    """Compute boolean signals from the profiled fields."""
    # Temporal signal
    profile.has_strong_temporal = len(profile.temporal) >= 1

    # Geographic signal
    geo = profile.geographic
    if geo:
        best_geo = geo[0]
        profile.has_strong_geographic = (
            best_geo.null_pct < 20.0 and best_geo.cardinality >= 4
        )

    # Ranking dimension
    for d in profile.categorical:
        if 5 <= d.cardinality <= 15:
            profile.has_ranking_dimension = True
            break

    # Part-to-whole
    for d in profile.categorical:
        if 2 <= d.cardinality <= 5:
            profile.has_part_to_whole = True
            break

    # Correlation pair
    profile.has_correlation_pair = len(profile.measures) >= 2

    # KPI count
    profile.kpi_count = min(4, len(profile.measures))


# ---------------------------------------------------------------------------
# Adapter: from workbook fields (works for ANY connected data source)
# ---------------------------------------------------------------------------

def from_workbook_fields(editor: Any) -> DataProfile:
    """Profile from ``list_fields()`` output — works for any Tableau data source.

    This is the universal adapter: MySQL, Hyper, Tableau Server, CSV extracts,
    Excel — anything that is already connected and has fields in the workbook.
    """
    raw_fields = editor.list_fields()
    profile = DataProfile(source_type="workbook_fields")

    all_names: list[str] = []

    for f in raw_fields:
        name = f.get("name", "")
        all_names.append(name)
        datatype = f.get("datatype", "string")
        role = f.get("role", "dimension")
        semantic_role = f.get("semantic-role", "")

        semantic_type = _detect_semantic_type(name, datatype, role)

        # Override with Tableau's own semantic-role if present
        if semantic_role in ("state", "city", "country", "zipcode"):
            semantic_type = "geographic"

        geo_role = semantic_role if semantic_type == "geographic" else _detect_geo_role(name)
        agg = _smart_aggregation(name) if role == "measure" else ""

        pf = ProfiledField(
            name=name,
            role=role,
            semantic_type=semantic_type,
            datatype=datatype,
            cardinality=f.get("cardinality", 0),
            null_pct=f.get("null_pct", 0.0),
            aggregation=agg,
            geo_role=geo_role,
        )

        if role == "measure":
            profile.measures.append(pf)
        else:
            profile.dimensions.append(pf)

    profile.domain_hint = _detect_domain(" ".join(all_names))
    _compute_signals(profile)
    return profile


# ---------------------------------------------------------------------------
# Adapter: from CSV (bridges to existing csv_to_hyper classifier)
# ---------------------------------------------------------------------------

def from_csv(csv_path: str, sample_rows: int = 1000) -> DataProfile:
    """Profile a CSV file using the existing schema classifier."""
    from twilize.csv_to_hyper import classify_columns, infer_csv_schema

    raw_schema = infer_csv_schema(csv_path, sample_rows=sample_rows)
    classified = classify_columns(raw_schema)
    return from_classified_schema(classified, source_type="csv")


# ---------------------------------------------------------------------------
# Adapter: from ClassifiedSchema (bridge from existing code)
# ---------------------------------------------------------------------------

def from_classified_schema(classified: Any, source_type: str = "csv") -> DataProfile:
    """Convert an existing ClassifiedSchema to a DataProfile."""
    profile = DataProfile(
        source_type=source_type,
        row_count=getattr(classified, "row_count", 0),
    )

    all_names: list[str] = []

    for col in classified.dimensions:
        name = col.spec.name
        all_names.append(name)
        semantic = col.semantic_type
        geo_role = _detect_geo_role(name) if semantic == "geographic" else ""

        pf = ProfiledField(
            name=name,
            role="dimension",
            semantic_type=semantic,
            datatype=col.spec.inferred_type,
            cardinality=col.spec.cardinality,
            null_pct=(
                (col.spec.null_count / col.spec.total_rows * 100)
                if col.spec.total_rows else 0.0
            ),
            geo_role=geo_role,
        )
        profile.dimensions.append(pf)

    for col in classified.measures:
        name = col.spec.name
        all_names.append(name)
        semantic = _detect_semantic_type(name, col.spec.inferred_type, "measure")
        agg = _smart_aggregation(name)

        pf = ProfiledField(
            name=name,
            role="measure",
            semantic_type=semantic,
            datatype=col.spec.inferred_type,
            cardinality=col.spec.cardinality,
            null_pct=(
                (col.spec.null_count / col.spec.total_rows * 100)
                if col.spec.total_rows else 0.0
            ),
            aggregation=agg,
        )
        profile.measures.append(pf)

    profile.domain_hint = _detect_domain(" ".join(all_names))
    _compute_signals(profile)
    return profile


# ---------------------------------------------------------------------------
# Adapter: from Hyper file
# ---------------------------------------------------------------------------

def from_hyper(hyper_path: str) -> DataProfile:
    """Profile a standalone Hyper extract file."""
    from twilize.connections import inspect_hyper_schema

    schema_info = inspect_hyper_schema(hyper_path)
    profile = DataProfile(source_type="hyper")

    all_names: list[str] = []

    for table in schema_info.get("tables", []):
        for col in table.get("columns", []):
            name = col["name"]
            all_names.append(name)
            dtype = col.get("type", "").lower()

            # Map Hyper types to our types
            if "int" in dtype:
                datatype = "integer"
            elif "double" in dtype or "float" in dtype or "numeric" in dtype:
                datatype = "real"
            elif "date" in dtype or "timestamp" in dtype:
                datatype = "date"
            elif "bool" in dtype:
                datatype = "boolean"
            else:
                datatype = "string"

            # Determine role
            if datatype in ("integer", "real") and not any(
                kw in name.lower() for kw in ("id", "key", "code", "year", "month")
            ):
                role = "measure"
            else:
                role = "dimension"

            semantic = _detect_semantic_type(name, datatype, role)
            geo_role = _detect_geo_role(name) if semantic == "geographic" else ""
            agg = _smart_aggregation(name) if role == "measure" else ""

            pf = ProfiledField(
                name=name,
                role=role,
                semantic_type=semantic,
                datatype=datatype,
                geo_role=geo_role,
                aggregation=agg,
            )
            if role == "measure":
                profile.measures.append(pf)
            else:
                profile.dimensions.append(pf)

    profile.domain_hint = _detect_domain(" ".join(all_names))
    _compute_signals(profile)
    return profile


# ---------------------------------------------------------------------------
# Adapter: from Tableau Extensions API
# ---------------------------------------------------------------------------

def from_extension_api(fields_json: dict[str, Any] | list[dict[str, Any]]) -> DataProfile:
    """Profile from Tableau Extensions API field metadata.

    Accepts either a list of field dicts or a dict with a ``fields`` key.
    Each field dict should have: name, dataType, role (optional).
    """
    profile = DataProfile(source_type="extension_api")

    if isinstance(fields_json, dict):
        fields_list = fields_json.get("fields", [])
    else:
        fields_list = fields_json

    all_names: list[str] = []

    for f in fields_list:
        name = f.get("name", f.get("fieldName", ""))
        all_names.append(name)
        dtype_raw = f.get("dataType", f.get("type", "string")).lower()

        if "int" in dtype_raw:
            datatype = "integer"
        elif "float" in dtype_raw or "real" in dtype_raw:
            datatype = "real"
        elif "date" in dtype_raw:
            datatype = "date"
        elif "bool" in dtype_raw:
            datatype = "boolean"
        else:
            datatype = "string"

        role = f.get("role", "").lower()
        if not role:
            role = "measure" if datatype in ("integer", "real") else "dimension"

        semantic = _detect_semantic_type(name, datatype, role)
        geo_role = _detect_geo_role(name) if semantic == "geographic" else ""
        agg = _smart_aggregation(name) if role == "measure" else ""

        pf = ProfiledField(
            name=name,
            role=role,
            semantic_type=semantic,
            datatype=datatype,
            cardinality=f.get("cardinality", 0),
            null_pct=f.get("nullPct", f.get("null_pct", 0.0)),
            aggregation=agg,
            geo_role=geo_role,
        )
        if role == "measure":
            profile.measures.append(pf)
        else:
            profile.dimensions.append(pf)

    profile.domain_hint = _detect_domain(" ".join(all_names))
    _compute_signals(profile)
    return profile


# ---------------------------------------------------------------------------
# Human-readable summary
# ---------------------------------------------------------------------------

def format_profile(profile: DataProfile) -> str:
    """Format a DataProfile as a human-readable summary."""
    lines = [
        "=== DATA PROFILE ===",
        f"Source type: {profile.source_type}",
        f"Row count: {profile.row_count or 'unknown'}",
        f"Domain hint: {profile.domain_hint or 'none detected'}",
        "",
        "SIGNALS:",
        f"  Strong temporal data:  {'YES' if profile.has_strong_temporal else 'no'}",
        f"  Strong geographic data: {'YES' if profile.has_strong_geographic else 'no'}",
        f"  Ranking dimension:     {'YES' if profile.has_ranking_dimension else 'no'}",
        f"  Correlation pair:      {'YES' if profile.has_correlation_pair else 'no'}",
        f"  Part-to-whole:         {'YES' if profile.has_part_to_whole else 'no'}",
        f"  KPI-worthy measures:   {profile.kpi_count}",
        "",
        f"DIMENSIONS ({len(profile.dimensions)}):",
    ]
    for d in profile.dimensions:
        extra = ""
        if d.geo_role:
            extra = f" [geo: {d.geo_role}]"
        lines.append(f"  {d.name} ({d.semantic_type}, {d.datatype}, card={d.cardinality}){extra}")

    lines.append(f"\nMEASURES ({len(profile.measures)}):")
    for m in profile.measures:
        lines.append(f"  {m.name} ({m.semantic_type}, {m.datatype}, agg={m.aggregation})")

    lines.append(f"\nFILTER CANDIDATES ({len(profile.good_filter_candidates())}):")
    for f in profile.good_filter_candidates():
        lines.append(f"  {f.name} (card={f.cardinality})")

    return "\n".join(lines)
