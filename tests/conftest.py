"""Shared pytest fixtures for twilize tests."""

import pytest
from pathlib import Path

from twilize.twb_editor import TWBEditor


@pytest.fixture
def editor():
    """Provide a clean TWBEditor instance with the default template."""
    return TWBEditor("")


@pytest.fixture
def editor_superstore():
    """Provide a TWBEditor instance loaded with the Superstore template."""
    template_path = Path(__file__).parent.parent / "templates" / "twb" / "superstore.twb"
    if template_path.exists():
        return TWBEditor(template_path)
    # Fallback to default template
    return TWBEditor("")
