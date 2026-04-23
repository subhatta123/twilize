"""Capability catalog and workbook analysis MCP tools.

These tools help an AI agent understand what twilize can and cannot do before
attempting to build or migrate a workbook.

TOOL INVENTORY
--------------
  list_capabilities()
      Return the full capability catalog from capability_registry.py as a
      formatted text table.  Shows every declared chart type, encoding, and
      feature with its support level (core / advanced / recipe / unsupported).
      Call this at the start of a session to know what is possible.

  describe_capability(kind, name)
      Return details for a single capability entry — level, description, and
      any caveats.  Use when the catalog shows something unexpected.

  analyze_twb(file_path)
      Parse an existing .twb file and report:
        - Which chart types and encodings it uses.
        - Which capabilities are core, advanced, recipe-level, or unsupported.
        - The full capability gap section (features used that twilize cannot yet
          reproduce automatically).
      Combines twb_analyzer.to_text() + to_gap_text() in one call.

  diff_template_gap(file_path)
      Return only the capability gap section (a subset of analyze_twb output).
      Useful when you already understand the workbook structure and just need
      to know what the SDK cannot handle.

  validate_workbook(file_path=None)
      Validate a saved .twb/.twbx file — or the current in-memory editor —
      against the official Tableau XSD schema (2026.1).  Failures are
      informational: Tableau Desktop is the true validator.

  validate_calc_fields()
      Scan the currently open workbook for calculated fields whose
      declared role/datatype disagree (most commonly a string-typed
      calc carrying role="measure", which Tableau cannot aggregate and
      shows with a red ``!`` in the Data pane).

  repair_calc_fields()
      Same scan plus in-place fix: demote the offending string-typed
      measures to dimensions. Returns the list of fields that were
      repaired.
"""

from __future__ import annotations

from typing import Optional

from ..capability_registry import format_capability_catalog, format_capability_detail
from ..twb_analyzer import analyze_workbook
from .app import server
from .state import get_editor


@server.tool()
def list_capabilities() -> str:
    """List twilize's declared capability boundary."""

    return format_capability_catalog()


@server.tool()
def describe_capability(kind: str, name: str) -> str:
    """Describe one declared capability and its support tier."""

    return format_capability_detail(kind, name)


@server.tool()
def analyze_twb(file_path: str) -> str:
    """Analyze a TWB file against twilize's declared capabilities."""

    report = analyze_workbook(file_path)
    return report.to_text() + "\n\n" + report.to_gap_text()


@server.tool()
def diff_template_gap(file_path: str) -> str:
    """Summarize the non-core capability gap of a TWB template."""

    report = analyze_workbook(file_path)
    return report.to_gap_text()


@server.tool()
def validate_workbook(file_path: Optional[str] = None) -> str:
    """Run an informational XSD schema check on a workbook (2026.1 schema).

    This is a non-blocking, advisory check only.  Tableau Desktop is the
    true validator — it routinely produces workbooks that deviate from the
    published XSD schema, so deviations reported here do NOT indicate the
    workbook is broken.  The workbook will almost certainly open correctly
    in Tableau regardless of any deviations found.

    Args:
        file_path: Path to a .twb or .twbx file to check. If omitted,
                   checks the currently open workbook (in memory).

    Returns:
        Informational summary of schema deviations (if any).
    """
    from ..validator import validate_against_schema

    if file_path:
        import io
        import zipfile
        from pathlib import Path
        from lxml import etree

        p = Path(file_path)
        if not p.exists():
            return f"ERROR  File not found: {file_path}"

        parser = etree.XMLParser(remove_blank_text=False)
        if p.suffix.lower() == ".twbx":
            with zipfile.ZipFile(p) as zf:
                twb_names = [n for n in zf.namelist() if n.lower().endswith(".twb")]
                if not twb_names:
                    return f"ERROR  No .twb found inside {file_path}"
                tree = etree.parse(io.BytesIO(zf.read(twb_names[0])), parser)
        else:
            tree = etree.parse(str(p), parser)

        result = validate_against_schema(tree.getroot())
    else:
        editor = get_editor()
        result = validate_against_schema(editor.root)

    return result.to_text()


@server.tool()
def validate_calc_fields() -> str:
    """Scan open workbook for role/datatype mismatches on calculated fields.

    Catches the common failure mode where a string-typed calculation ships
    with ``role="measure"``. Tableau cannot aggregate a string, so the field
    shows a red ``!`` in the Data pane and any SUM/AVG/ATTR reference to it
    is rejected with "can't be converted to a measure using ATTR()".

    This is a read-only check. To fix detected issues in place call
    :func:`repair_calc_fields`.
    """
    from ..calc_field_validator import find_mismatches, format_report

    editor = get_editor()
    issues = find_mismatches(editor.root)
    return format_report(issues, repaired=False)


@server.tool()
def repair_calc_fields() -> str:
    """Fix role/datatype mismatches on calculated fields in the open workbook.

    Demotes string-typed calculations that carry ``role="measure"`` to
    ``role="dimension"`` (with ``type="nominal"``). Use this after
    :func:`open_workbook` when the sidebar shows red ``!`` markers on KPI
    label calcs. Call :func:`save_workbook` afterwards to persist the fix.
    """
    from ..calc_field_validator import repair_mismatches, format_report

    editor = get_editor()
    issues = repair_mismatches(editor.root, apply=True)
    return format_report(issues, repaired=True)
