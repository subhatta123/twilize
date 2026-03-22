"""Session snapshot manager for undo/rollback in the MCP server.

Snapshots the TWBEditor state (lxml tree, field registry, parameters,
zone ID counter) before each mutating tool call. On error or explicit
undo, the latest snapshot is restored.

Usage in tools_workbook.py:
    from .snapshot import take_snapshot, rollback

    # Before a mutating operation
    take_snapshot("configure_chart")

    # To undo the last change
    rollback()
"""

from __future__ import annotations

import copy
import io
import logging
from collections import deque
from dataclasses import dataclass
from typing import Optional

from lxml import etree

logger = logging.getLogger(__name__)

MAX_SNAPSHOTS = 20


@dataclass
class Snapshot:
    """A frozen point-in-time copy of the editor state."""

    label: str
    tree_bytes: bytes
    fields: dict  # shallow copy of field_registry._fields
    parameters: dict  # shallow copy of _parameters
    zone_id_counter: int
    twbx_source: object  # Path | None
    twbx_twb_name: str | None
    template_path: object  # Path
    is_default_template: bool


class SessionSnapshotManager:
    """Manages a bounded stack of editor snapshots for undo support."""

    def __init__(self) -> None:
        self._snapshots: deque[Snapshot] = deque(maxlen=MAX_SNAPSHOTS)

    def take_snapshot(self, editor, label: str = "") -> None:
        """Serialize the current editor state and push onto the stack."""
        buf = io.BytesIO()
        editor.tree.write(buf, xml_declaration=True, encoding="utf-8", pretty_print=False)
        tree_bytes = buf.getvalue()

        snap = Snapshot(
            label=label,
            tree_bytes=tree_bytes,
            fields=copy.deepcopy(editor.field_registry._fields),
            parameters=copy.deepcopy(editor._parameters),
            zone_id_counter=editor._zone_id_counter,
            twbx_source=editor._twbx_source,
            twbx_twb_name=editor._twbx_twb_name,
            template_path=editor.template_path,
            is_default_template=getattr(editor, "_is_default_template", False),
        )
        self._snapshots.append(snap)
        logger.debug("Snapshot taken: %s (stack depth: %d)", label, len(self._snapshots))

    def rollback(self, editor) -> str:
        """Pop the most recent snapshot and restore the editor to that state.

        Args:
            editor: The current TWBEditor instance to restore in-place.

        Returns:
            Description of what was undone.

        Raises:
            RuntimeError: If no snapshots are available.
        """
        if not self._snapshots:
            raise RuntimeError("No snapshots available for undo.")

        snap = self._snapshots.pop()

        # Restore the lxml tree
        parser = etree.XMLParser(remove_blank_text=False)
        editor.tree = etree.parse(io.BytesIO(snap.tree_bytes), parser)
        editor.root = editor.tree.getroot()

        # Restore the datasource reference
        editor._datasource = editor._get_datasource()

        # Restore field registry
        editor.field_registry._fields = snap.fields
        editor.field_registry.datasource_name = editor._datasource.get("name", "")

        # Restore parameters and zone counter
        editor._parameters = snap.parameters
        editor._zone_id_counter = snap.zone_id_counter

        # Restore twbx metadata
        editor._twbx_source = snap.twbx_source
        editor._twbx_twb_name = snap.twbx_twb_name

        label = snap.label or "last change"
        logger.info("Rolled back to before: %s (remaining snapshots: %d)", label, len(self._snapshots))
        return f"Undone: {label}"

    @property
    def undo_count(self) -> int:
        """Number of available undo steps."""
        return len(self._snapshots)

    def clear(self) -> None:
        """Discard all snapshots (called on workbook reset)."""
        self._snapshots.clear()
