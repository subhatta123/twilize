"""Regression tests for the 0.33.0 fixes.

Two bugs being guarded:

1. KPI display calcs emitted by ``_prepare_enhanced_kpis`` inherited
   ``role="measure"`` from ``_infer_calculated_field_semantics`` because their
   formulas embed ``SUM(...)`` internally. Tableau then applied an outer SUM
   wrapper to a string-typed calc, rendering the field invalid (red "!" in the
   Data pane).

2. ``_SafeFastMCP.tool`` caught every exception raised by a tool and *returned*
   a string ``"Error in <tool>: <exc>"``. FastMCP treats a returned string as a
   successful tool result, so MCP clients could not distinguish a genuine
   success from a failure — a worksheet/dashboard creation could fail while the
   caller was told it succeeded.
"""

from __future__ import annotations

import pytest

from twilize.twb_editor import TWBEditor


def test_string_kpi_with_explicit_dimension_role_emits_dimension_column():
    """The call-site fix in pipeline.py forwards role/type to add_calculated_field.

    This test exercises the editor directly to lock in the behaviour the
    pipeline now relies on: when a caller passes ``role="dimension"`` /
    ``field_type="nominal"`` for a string calc whose formula contains SUM(...),
    those overrides must win over the aggregate-function heuristic.
    """
    editor = TWBEditor("")

    editor.add_calculated_field(
        field_name="_kpi_Sales",
        formula="'SALES' + CHAR(10) + STR(SUM([Profit]))",
        datatype="string",
        role="dimension",
        field_type="nominal",
    )

    col = editor._datasource.find("column[@caption='_kpi_Sales']")
    assert col is not None, "KPI calculated field was not added to the datasource"
    assert col.get("datatype") == "string"
    assert col.get("role") == "dimension", (
        "Explicit role override must win over aggregate-heuristic; "
        "otherwise Tableau will apply SUM to a string and mark the field invalid."
    )
    assert col.get("type") == "nominal"


def test_string_kpi_without_overrides_still_misclassifies_as_measure():
    """Characterise the underlying inference bug so we notice if it's ever fixed upstream.

    If ``_infer_calculated_field_semantics`` is later corrected to treat
    string-typed formulas as dimensions regardless of embedded SUM tokens, this
    test will fail — at which point the explicit override in ``pipeline.py``
    becomes redundant and can be removed. Until then, the override is required.
    """
    editor = TWBEditor("")

    editor.add_calculated_field(
        field_name="_kpi_Naive",
        formula="'X' + STR(SUM([Profit]))",
        datatype="string",
    )

    col = editor._datasource.find("column[@caption='_kpi_Naive']")
    assert col is not None
    # Current (buggy-by-heuristic) behaviour: string + SUM(...) -> measure/nominal.
    # If this starts returning "dimension", revisit the pipeline override.
    assert col.get("role") == "measure"


def test_safe_fast_mcp_reraises_tool_exceptions():
    """Failing tools must raise so FastMCP emits a real error response.

    The previous implementation caught and returned ``f"Error in ..."``, which
    the MCP protocol serialises as a successful string return value. That made
    silent failures indistinguishable from successes.
    """
    from twilize.mcp.app import _SafeFastMCP

    server = _SafeFastMCP("test")

    @server.tool()
    def broken_tool() -> str:
        raise RuntimeError("boom")

    fn = server._tool_manager._tools["broken_tool"].fn
    with pytest.raises(RuntimeError, match="boom"):
        fn()


def test_safe_fast_mcp_passes_successful_returns_through():
    """Non-raising tools must still return their value normally."""
    from twilize.mcp.app import _SafeFastMCP

    server = _SafeFastMCP("test")

    @server.tool()
    def happy_tool() -> str:
        return "ok"

    fn = server._tool_manager._tools["happy_tool"].fn
    assert fn() == "ok"


def test_add_dashboard_rejects_duplicate_names():
    """0.33.1 regression: Tableau's DashboardParser crashes with a null-pointer
    dereference inside DashboardUtils::FetchImage (error 0x00BF554A "Internal
    Error") when a .twb contains two <dashboard> elements with the same name.

    add_dashboard() must reject the duplicate up front with a clear error
    instead of writing a workbook Tableau cannot open.
    """
    editor = TWBEditor("")
    editor.add_worksheet("Sheet1")
    editor.add_dashboard("Exec", worksheet_names=["Sheet1"])

    with pytest.raises(ValueError, match="already exists"):
        editor.add_dashboard("Exec", worksheet_names=["Sheet1"])


def test_dashboard_name_colliding_with_worksheet_is_rejected():
    """0.33.1 regression: a dashboard sharing a worksheet's name produces
    either a Tableau XSD error (D2E8DA72 "windows declares duplicate identity
    constraint unique values", because <windows> is name-unique regardless of
    class) or — if the worksheet window is silently wiped — a FetchImage null
    deref (0x00BF554A). The only safe answer is to reject the collision.
    """
    editor = TWBEditor("")
    editor.add_worksheet("Top Customers by Profit")

    with pytest.raises(ValueError, match="collides with an existing worksheet"):
        editor.add_dashboard(
            "Top Customers by Profit",
            worksheet_names=["Top Customers by Profit"],
            layout="vertical",
        )
