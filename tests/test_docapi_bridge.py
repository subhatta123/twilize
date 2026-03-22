"""Tests for the Document API bridge module."""

import pytest

from twilize.docapi_bridge import (
    get_connection_info,
    is_available,
    list_datasource_fields,
)
from twilize.twb_editor import TWBEditor


@pytest.fixture
def sample_twb(tmp_path):
    """Create a sample .twb file from the default template."""
    editor = TWBEditor("")
    out = tmp_path / "test.twb"
    editor.save(str(out))
    return out


class TestDocapiBridge:
    def test_is_available_returns_bool(self):
        result = is_available()
        assert isinstance(result, bool)

    def test_list_fields_lxml_fallback(self, sample_twb):
        fields = list_datasource_fields(sample_twb)
        assert isinstance(fields, list)
        # Default template has some fields
        if fields:
            assert "name" in fields[0]
            assert "datatype" in fields[0]

    def test_get_connections_lxml_fallback(self, sample_twb):
        conns = get_connection_info(sample_twb)
        assert isinstance(conns, list)

    def test_list_fields_nonexistent_file(self):
        with pytest.raises(Exception):
            list_datasource_fields("/nonexistent/file.twb")
