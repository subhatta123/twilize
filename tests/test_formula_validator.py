"""Tests for the Tableau-function formula validator.

The validator is the guard rail that caught the 0.33.1 CHR regression: KPI
formulas were emitting CHR(10) for a newline, but Tableau only has CHAR. Every
calculated field now runs through assert_valid_formula() before being written,
so any typo or unsupported function fails loud at build time instead of
producing a red-"!" field in Tableau.
"""

from __future__ import annotations

import pytest

from twilize.formula_validator import (
    assert_valid_formula,
    validate_formula,
)


def test_chr_is_rejected_with_char_suggestion():
    """The exact symptom from the CHR bug: CHR isn't a Tableau function, but
    CHAR is, and the error must name the fix."""
    unknown = validate_formula("'SALES' + CHR(10) + STR(SUM([Sales]))")
    assert unknown == ["CHR"]
    with pytest.raises(ValueError, match="CHR") as excinfo:
        assert_valid_formula("CHR(10)", field_name="_kpi_Sales")
    msg = str(excinfo.value)
    assert "CHAR" in msg
    assert "_kpi_Sales" in msg


def test_valid_formula_with_aggregates_and_keywords_passes():
    """Keywords (IF/THEN/AND) and valid functions coexist without false
    positives. This is the shape of the real KPI formulas."""
    formula = (
        "IF SUM([Sales]) >= SUM([Profit]) "
        "THEN '▲ ' + STR(ROUND(ABS(SUM([Sales])), 1)) "
        "ELSE '▼' END + CHAR(10) + IFNULL(STR(AVG([Discount])), '')"
    )
    assert validate_formula(formula) == []
    assert_valid_formula(formula)  # no raise


def test_string_literal_does_not_trigger_false_positive():
    """A function-like token inside a string literal must not be flagged."""
    formula = "'CHR(10)' + STR(SUM([Sales]))"  # CHR is literal text
    assert validate_formula(formula) == []


def test_bracket_reference_does_not_trigger_false_positive():
    """Calculation refs like [Calculation_AAF1(x)] must not be flagged even if
    the name contains parentheses."""
    formula = "SUM([Calculation_AAF1F63B9A54406A89BB163C5B786F29]) + 1"
    assert validate_formula(formula) == []


def test_multiple_unknowns_reported_together():
    unknown = validate_formula("CHR(10) + MYFUNC([x])")
    assert set(unknown) == {"CHR", "MYFUNC"}


def test_add_calculated_field_rejects_chr_formula():
    """End-to-end: writing a field with CHR() must raise before any XML is
    emitted — this is the regression guard for the 0.33.1 bug."""
    from twilize.twb_editor import TWBEditor

    editor = TWBEditor("")
    with pytest.raises(ValueError, match="CHAR"):
        editor.add_calculated_field(
            field_name="_kpi_Sales",
            formula="'SALES' + CHR(10) + STR(SUM([Sales]))",
            datatype="string",
            role="dimension",
            field_type="nominal",
        )
