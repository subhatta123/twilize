"""Bridge module for selective Tableau Document API integration.

The Document API (``tableaudocumentapi``) handles connections and
datasource metadata well, but does NOT support chart building, pane
markup, or dashboard layout. This bridge uses the Document API where
it excels (listing datasource fields, updating connections) and falls
back to lxml for everything else.

Install with: ``pip install tableaudocumentapi``
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_HAS_DOCAPI = False
try:
    from tableaudocumentapi import Workbook as DocApiWorkbook
    from tableaudocumentapi import Datasource as DocApiDatasource
    from tableaudocumentapi import Field as DocApiField

    _HAS_DOCAPI = True
except ImportError:
    pass


def is_available() -> bool:
    """Check whether the Document API package is installed."""
    return _HAS_DOCAPI


def list_datasource_fields(twb_path: str | Path) -> list[dict]:
    """List all fields in the first datasource using the Document API.

    Falls back to lxml-based field enumeration if the Document API
    is not installed.

    Args:
        twb_path: Path to a .twb or .twbx file.

    Returns:
        List of dicts with keys: name, datatype, role, type, caption.
    """
    twb_path = Path(twb_path)

    if _HAS_DOCAPI:
        return _list_fields_docapi(twb_path)
    return _list_fields_lxml(twb_path)


def _list_fields_docapi(twb_path: Path) -> list[dict]:
    """Use Document API to enumerate fields."""
    wb = DocApiWorkbook(str(twb_path))
    fields = []

    for ds in wb.datasources:
        for name, field_obj in ds.fields.items():
            fields.append({
                "name": name,
                "datatype": getattr(field_obj, "datatype", ""),
                "role": getattr(field_obj, "role", ""),
                "type": getattr(field_obj, "type", ""),
                "caption": getattr(field_obj, "caption", "") or "",
            })
        break  # First datasource only

    return fields


def _list_fields_lxml(twb_path: Path) -> list[dict]:
    """Fallback: enumerate fields from XML using lxml."""
    import zipfile

    from lxml import etree

    if twb_path.suffix.lower() == ".twbx":
        with zipfile.ZipFile(twb_path) as zf:
            twb_names = [n for n in zf.namelist() if n.endswith(".twb")]
            if not twb_names:
                raise ValueError(f"No .twb inside {twb_path}")
            xml_bytes = zf.read(twb_names[0])
            tree = etree.fromstring(xml_bytes)
    else:
        tree = etree.parse(str(twb_path)).getroot()

    fields = []
    ds = tree.find(".//datasource[@hasconnection='true']")
    if ds is None:
        ds = tree.find(".//datasource")
    if ds is None:
        return fields

    for col in ds.findall("column"):
        fields.append({
            "name": col.get("name", "").strip("[]"),
            "datatype": col.get("datatype", ""),
            "role": col.get("role", ""),
            "type": col.get("type", ""),
            "caption": col.get("caption", "") or "",
        })

    return fields


def update_connection(
    twb_path: str | Path,
    server: str = "",
    dbname: str = "",
    username: str = "",
    port: str = "",
) -> str:
    """Update connection parameters using the Document API.

    Falls back to a no-op message if the Document API is not installed
    (in that case, use TWBEditor's connection methods directly).

    Args:
        twb_path: Path to .twb or .twbx file.
        server: New server address.
        dbname: New database name.
        username: New username.
        port: New port.

    Returns:
        Confirmation message.
    """
    if not _HAS_DOCAPI:
        return (
            "Document API not installed. Use TWBEditor's "
            "set_mysql_connection/set_hyper_connection instead."
        )

    twb_path = Path(twb_path)
    wb = DocApiWorkbook(str(twb_path))

    updated = 0
    for ds in wb.datasources:
        for conn in ds.connections:
            if server:
                conn.server = server
            if dbname:
                conn.dbname = dbname
            if username:
                conn.username = username
            if port:
                conn.port = port
            updated += 1

    wb.save()
    return f"Updated {updated} connection(s) in {twb_path}"


def get_connection_info(twb_path: str | Path) -> list[dict]:
    """Get connection details from a workbook.

    Args:
        twb_path: Path to .twb or .twbx file.

    Returns:
        List of connection dicts with server, dbname, port, etc.
    """
    twb_path = Path(twb_path)

    if _HAS_DOCAPI:
        return _get_connections_docapi(twb_path)
    return _get_connections_lxml(twb_path)


def _get_connections_docapi(twb_path: Path) -> list[dict]:
    """Use Document API for connection info."""
    wb = DocApiWorkbook(str(twb_path))
    connections = []

    for ds in wb.datasources:
        for conn in ds.connections:
            connections.append({
                "class": getattr(conn, "connection_type", ""),
                "server": getattr(conn, "server", ""),
                "dbname": getattr(conn, "dbname", ""),
                "port": getattr(conn, "port", ""),
                "username": getattr(conn, "username", ""),
            })

    return connections


def _get_connections_lxml(twb_path: Path) -> list[dict]:
    """Fallback: read connection info from XML."""
    import zipfile

    from lxml import etree

    if twb_path.suffix.lower() == ".twbx":
        with zipfile.ZipFile(twb_path) as zf:
            twb_names = [n for n in zf.namelist() if n.endswith(".twb")]
            if not twb_names:
                return []
            xml_bytes = zf.read(twb_names[0])
            tree = etree.fromstring(xml_bytes)
    else:
        tree = etree.parse(str(twb_path)).getroot()

    connections = []
    for conn in tree.findall(".//connection"):
        conn_class = conn.get("class", "")
        if conn_class in ("federated", ""):
            continue
        connections.append({
            "class": conn_class,
            "server": conn.get("server", ""),
            "dbname": conn.get("dbname", ""),
            "port": conn.get("port", ""),
            "username": conn.get("username", ""),
        })

    return connections
