"""Validate and repair role/datatype mismatches on calculated fields.

Motivation
----------
Workbooks authored by KPI templates (e.g. the "_kpi_Sales", "_kpi_Quantity",
"_kpi_Discount" label calcs in the Superstore sample) frequently ship with
``role="measure"`` on string-typed calculated fields. The calc returns a
formatted label like ``'SALES\\n$2.3M'``, which is categorical, but Tableau
inherits the "measure" role and then cannot aggregate it — producing the
famous red ``!`` ("can't be displayed in Tooltips because it can't be
converted to a measure using ATTR()") or a broken "_kpi_Sales (Sum)" alias
in the data pane.

The canonical fix is to demote such fields to ``role="dimension"``. String
calculations are dimensions; numeric calculations can stay measures.

This module exposes:

    find_mismatches(root) -> list[FieldIssue]
        Scan the workbook tree for role/datatype mismatches and return a
        structured list of issues.

    repair_mismatches(root, apply=True) -> list[FieldIssue]
        Same scan, but flip the role to ``dimension`` for each string-typed
        ``role="measure"`` calculated field. Returns the list of issues
        that were (or would be) repaired.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from lxml import etree


Severity = Literal["error", "warning"]


@dataclass
class FieldIssue:
    """One detected role/datatype mismatch on a field definition."""

    caption: str
    name: str
    datatype: str
    role: str
    reason: str
    severity: Severity = "error"
    fix: str = ""

    def to_line(self) -> str:
        head = f"[{self.severity.upper()}] {self.caption or self.name}"
        return f"{head}\n    datatype={self.datatype} role={self.role}\n    {self.reason}" + (
            f"\n    FIX: {self.fix}" if self.fix else ""
        )


# Tableau string-ish datatypes. A measure must be numeric/date; these cannot
# be aggregated with SUM/AVG/etc., so role="measure" is a bug when the column
# is defined as one of these.
_STRING_DATATYPES = frozenset({"string", "nominal", "boolean"})


def _iter_calc_columns(root: etree._Element):
    """Yield ``<column>`` elements that carry a Tableau calculation."""

    for col in root.iter("column"):
        if col.find("calculation") is not None:
            yield col


def find_mismatches(root: etree._Element) -> list[FieldIssue]:
    """Return the list of role/datatype mismatches in the workbook.

    Currently detects:
      - String-typed calculated field declared as ``role="measure"``.
        Tableau will refuse to aggregate it (SUM/AVG/ATTR) and surfaces a
        red ``!`` on the field in the Data pane.
    """

    issues: list[FieldIssue] = []
    for col in _iter_calc_columns(root):
        datatype = (col.get("datatype") or "").lower()
        role = (col.get("role") or "").lower()
        caption = col.get("caption") or ""
        name = col.get("name") or ""

        if role == "measure" and datatype in _STRING_DATATYPES:
            issues.append(
                FieldIssue(
                    caption=caption,
                    name=name,
                    datatype=datatype,
                    role=role,
                    reason=(
                        f"Calculated field has datatype={datatype!r} but "
                        f'role="measure". String calcs cannot be aggregated '
                        f"— Tableau will flag it as broken."
                    ),
                    severity="error",
                    fix='Set role="dimension"',
                )
            )
    return issues


def repair_mismatches(root: etree._Element, apply: bool = True) -> list[FieldIssue]:
    """Fix mismatches in-place. Returns the list that was (or would be) fixed.

    When ``apply=False`` this is equivalent to :func:`find_mismatches` — no
    mutation happens, the caller just sees what would change.
    """

    issues = find_mismatches(root)
    if not apply:
        return issues

    by_name = {i.name: i for i in issues if i.name}
    for col in _iter_calc_columns(root):
        name = col.get("name") or ""
        if name in by_name and (col.get("role") or "").lower() == "measure":
            col.set("role", "dimension")
            # `type` should follow suit — "quantitative" makes no sense for
            # a string dimension. "nominal" is already the Tableau default
            # for string dimensions.
            if (col.get("type") or "").lower() == "quantitative":
                col.set("type", "nominal")

    return issues


def format_report(issues: list[FieldIssue], repaired: bool = False) -> str:
    """Render a human-readable report for the MCP tool output."""

    if not issues:
        return "OK  No role/datatype mismatches found on calculated fields."

    header = (
        f"REPAIRED {len(issues)} field(s):"
        if repaired
        else f"Found {len(issues)} role/datatype mismatch(es):"
    )
    body = "\n\n".join(i.to_line() for i in issues)
    return f"{header}\n\n{body}"
