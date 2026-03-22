"""Tests for Phase 1 enhancements: undo/rollback, fuzzy-match errors,
session validation, and chart builder error collection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from twilize.server import (
    add_worksheet,
    configure_chart,
    create_workbook,
    save_workbook,
)
from twilize.mcp.state import get_editor, get_snapshot_manager, set_editor
from twilize.mcp.tools_workbook import undo_last_change
from twilize.field_registry import FieldRegistry
from twilize.validator import validate_editor_state
from twilize.charts.dispatcher import configure_chart as dispatch_configure_chart

TEMPLATE = Path("templates/twb/superstore.twb")


@pytest.fixture(autouse=True)
def fresh_workbook():
    create_workbook(str(TEMPLATE), "Error Handling Test")


# ================================================================
# Phase 1A: Undo / Rollback
# ================================================================


class TestUndoRollback:
    def test_undo_restores_worksheet_count(self):
        editor = get_editor()
        assert len(editor.list_worksheets()) == 0

        add_worksheet("Sheet 1")
        assert len(editor.list_worksheets()) == 1

        result = undo_last_change()
        assert "Undone" in result
        assert len(editor.list_worksheets()) == 0

    def test_undo_when_nothing_to_undo(self):
        # Clear any snapshots from fixture
        get_snapshot_manager().clear()
        result = undo_last_change()
        assert "Nothing to undo" in result

    def test_multiple_undos(self):
        editor = get_editor()
        add_worksheet("A")
        add_worksheet("B")
        add_worksheet("C")
        assert len(editor.list_worksheets()) == 3

        undo_last_change()
        assert editor.list_worksheets() == ["A", "B"]

        undo_last_change()
        assert editor.list_worksheets() == ["A"]

    def test_snapshot_count_tracks_operations(self):
        mgr = get_snapshot_manager()
        initial = mgr.undo_count
        add_worksheet("X")
        assert mgr.undo_count == initial + 1

    def test_set_editor_clears_snapshots(self):
        add_worksheet("Before Reset")
        mgr = get_snapshot_manager()
        assert mgr.undo_count > 0

        # Re-create workbook clears snapshots
        create_workbook(str(TEMPLATE), "Reset Test")
        assert mgr.undo_count == 0


# ================================================================
# Phase 1B: Fuzzy-Match Error Messages
# ================================================================


class TestFuzzyMatchErrors:
    def test_suggests_similar_field_names(self):
        editor = get_editor()
        registry = editor.field_registry

        with pytest.raises(KeyError, match="Did you mean"):
            registry._find_field("Saels")  # typo for "Sales"

    def test_no_suggestion_for_completely_wrong_name(self):
        editor = get_editor()
        registry = editor.field_registry

        with pytest.raises(KeyError, match="Unknown field"):
            registry._find_field("xyzzy_not_a_field_12345")

    def test_exact_match_still_works(self):
        editor = get_editor()
        fi = editor.field_registry._find_field("Sales")
        assert fi.display_name == "Sales"

    def test_case_insensitive_match_still_works(self):
        editor = get_editor()
        fi = editor.field_registry._find_field("sales")
        assert fi.display_name == "Sales"


# ================================================================
# Phase 1C: Session Validation
# ================================================================


class TestSessionValidation:
    def test_configure_chart_rejects_missing_worksheet(self):
        with pytest.raises(ValueError, match="not found"):
            configure_chart(
                worksheet_name="NonExistent",
                mark_type="Bar",
                columns=["Category"],
                rows=["SUM(Sales)"],
            )

    def test_configure_chart_works_with_existing_worksheet(self):
        add_worksheet("Valid Sheet")
        result = configure_chart(
            worksheet_name="Valid Sheet",
            mark_type="Bar",
            columns=["Category"],
            rows=["SUM(Sales)"],
        )
        assert "Valid Sheet" in result

    def test_validate_editor_state_clean(self):
        editor = get_editor()
        add_worksheet("Test")
        configure_chart(
            worksheet_name="Test",
            mark_type="Bar",
            columns=["Category"],
            rows=["SUM(Sales)"],
        )
        issues = validate_editor_state(editor)
        assert len(issues) == 0

    def test_validate_editor_state_detects_orphaned_window(self, tmp_path):
        """Manually create an inconsistency and verify detection."""
        from lxml import etree

        editor = get_editor()
        add_worksheet("Real Sheet")

        # Manually add a window referencing a non-existent worksheet
        windows = editor.root.find("windows")
        if windows is None:
            windows = etree.SubElement(editor.root, "windows")
        ghost_win = etree.SubElement(windows, "window")
        ghost_win.set("class", "worksheet")
        ghost_win.set("name", "Ghost Sheet")
        etree.SubElement(ghost_win, "simple-id", uuid="fake")

        issues = validate_editor_state(editor)
        assert any("Ghost Sheet" in issue for issue in issues)


# ================================================================
# Phase 1D: Chart Builder Error Collection
# ================================================================


class TestChartBuilderErrors:
    def test_multiple_bad_fields_reported_at_once(self):
        editor = get_editor()
        add_worksheet("ErrorSheet")

        with pytest.raises(ValueError, match="field error") as exc_info:
            dispatch_configure_chart(
                editor,
                worksheet_name="ErrorSheet",
                mark_type="Bar",
                columns=["NonExistentField1"],
                rows=["NonExistentField2"],
            )
        # Both bad fields should be mentioned
        msg = str(exc_info.value)
        assert "NonExistentField1" in msg
        assert "NonExistentField2" in msg

    def test_valid_mark_type_aliases_work(self):
        """Ensure pattern aliases like Scatterplot route correctly."""
        editor = get_editor()
        add_worksheet("ScatterSheet")

        result = dispatch_configure_chart(
            editor,
            worksheet_name="ScatterSheet",
            mark_type="Scatterplot",
            columns=["SUM(Sales)"],
            rows=["SUM(Profit)"],
        )
        assert "ScatterSheet" in result
