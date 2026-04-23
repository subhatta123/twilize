"""Tests for calc_field_validator: role/datatype mismatch detection + repair.

Guards the fix for the recurring "_kpi_* (Sum)" red-bang bug in imported
workbooks: a string-typed calc that ships with ``role="measure"`` cannot be
aggregated by Tableau and is flagged invalid in the Data pane and rejected
on tooltips/shelves with "can't be converted to a measure using ATTR()".
"""

from __future__ import annotations

from lxml import etree

from twilize.calc_field_validator import (
    find_mismatches,
    format_report,
    repair_mismatches,
)


def _make_root_with_calc(datatype: str, role: str, caption: str = "_kpi_Sales") -> etree._Element:
    xml = f"""
    <workbook>
      <datasources>
        <datasource>
          <column caption="{caption}" datatype="{datatype}" role="{role}" type="quantitative"
                  name="[Calc_X]">
            <calculation class="tableau" formula="'SALES' + STR(SUM([Sales]))"/>
          </column>
        </datasource>
      </datasources>
    </workbook>
    """
    return etree.fromstring(xml)


def test_detects_string_calc_with_measure_role():
    root = _make_root_with_calc("string", "measure")
    issues = find_mismatches(root)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.caption == "_kpi_Sales"
    assert issue.datatype == "string"
    assert issue.role == "measure"
    assert issue.severity == "error"
    assert 'role="dimension"' in issue.fix


def test_no_issue_when_string_calc_is_already_dimension():
    root = _make_root_with_calc("string", "dimension")
    assert find_mismatches(root) == []


def test_no_issue_when_numeric_calc_is_measure():
    root = _make_root_with_calc("real", "measure")
    assert find_mismatches(root) == []


def test_repair_flips_role_to_dimension_and_type_to_nominal():
    root = _make_root_with_calc("string", "measure")
    repaired = repair_mismatches(root, apply=True)
    assert len(repaired) == 1

    col = root.find(".//column[@caption='_kpi_Sales']")
    assert col.get("role") == "dimension"
    assert col.get("type") == "nominal"

    # Idempotent — a second pass finds nothing.
    assert find_mismatches(root) == []


def test_repair_with_apply_false_is_read_only():
    root = _make_root_with_calc("string", "measure")
    issues = repair_mismatches(root, apply=False)
    assert len(issues) == 1
    col = root.find(".//column[@caption='_kpi_Sales']")
    # Nothing was changed.
    assert col.get("role") == "measure"


def test_format_report_empty_case():
    assert "OK" in format_report([])


def test_format_report_includes_caption_and_fix():
    root = _make_root_with_calc("string", "measure", caption="_kpi_Discount")
    issues = find_mismatches(root)
    text = format_report(issues, repaired=False)
    assert "_kpi_Discount" in text
    assert "dimension" in text
