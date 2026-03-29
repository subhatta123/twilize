"""FastMCP server singleton for the twilize MCP server.

This module creates the single FastMCP `server` instance that all tool and
resource modules register against via @server.tool() and @server.resource().

Import order matters: app.py must be imported before tools_*.py and resources.py
so that `server` exists when the decorators run.  The entry point (typically
run via `mcp run` or `python -m twilize.mcp`) imports all tool modules, which
self-register, and then starts the server transport.

The `instructions` string is what AI agents read when they first connect —
it summarises the required call order and points agents to skill resources.
"""

from typing import Any

from pydantic import create_model
from mcp.server.fastmcp.utilities import func_metadata as _fm
from mcp.server.fastmcp import FastMCP

# Monkey-patch: MCP SDK 1.26.0 calls create_model(name, result=type) which
# Pydantic v2 rejects — it needs create_model(name, result=(type, ...)).
_original_create_wrapped = _fm._create_wrapped_model


def _patched_create_wrapped(func_name: str, annotation: Any) -> type:
    model_name = f"{func_name}Output"
    return create_model(model_name, result=(annotation, ...))


_fm._create_wrapped_model = _patched_create_wrapped


server = FastMCP(
    "twilize",
    instructions=(
        "Tableau Workbook (.twb/.twbx) generation MCP Server.\n\n"
        "CRITICAL: You MUST use the provided tools to build workbooks. "
        "NEVER write Tableau XML directly — hand-written XML will fail to "
        "open in Tableau Desktop. The tools handle all XML generation.\n\n"
        "Required workflow:\n"
        "  1. create_workbook() or open_workbook() — start a session\n"
        "  2. list_fields() — see available datasource fields\n"
        "  3. add_worksheet() + configure_chart() — build each chart\n"
        "  4. add_dashboard() — combine worksheets into a dashboard\n"
        "  5. save_workbook() — write the .twb or .twbx file\n\n"
        "For CSV data: use csv_to_dashboard() for an end-to-end pipeline, "
        "or csv_to_hyper() + set_hyper_connection() for manual control.\n\n"
        "Use list_capabilities or describe_capability to check whether a "
        "chart type or feature is supported before attempting it."
    ),
)
