"""Compatibility entrypoint for twilize's MCP server."""

import os

from .mcp.app import server
from .mcp.resources import read_skill, read_skills_index, read_tableau_functions
from .mcp.tools_layout import generate_layout_json
from .mcp.tools_migration import (
    apply_twb_migration,
    inspect_target_schema,
    migrate_twb_guided,
    profile_twb_for_migration,
    propose_field_mapping,
    preview_twb_migration,
)
from .mcp.tools_support import (
    analyze_twb,
    describe_capability,
    diff_template_gap,
    list_capabilities,
)
from .mcp.tools_pipeline import (
    csv_to_dashboard,
    csv_to_hyper,
    inspect_csv,
    suggest_charts_for_csv,
)
from .mcp.tools_intelligence import (
    get_active_rules,
    list_gallery_templates,
    profile_csv,
    profile_data_source,
    recommend_template,
    recommend_template_for_csv,
)
from .mcp.tools_workbook import (
    add_calculated_field,
    add_dashboard,
    add_dashboard_action,
    add_parameter,
    add_reference_band,
    add_reference_line,
    add_trend_line,
    add_worksheet,
    apply_color_palette,
    apply_dashboard_theme,
    configure_chart,
    configure_chart_recipe,
    configure_dual_axis,
    configure_worksheet_style,
    create_workbook,
    list_dashboards,
    list_fields,
    list_worksheets,
    open_workbook,
    remove_calculated_field,
    save_workbook,
    set_hyper_connection,
    set_mysql_connection,
    set_tableauserver_connection,
    undo_last_change,
)


def main():
    """Run the MCP server.

    Transport is selected via the ``MCP_TRANSPORT`` env var:

    - ``stdio`` (default): local subprocess transport for Claude Desktop / uvx
    - ``streamable-http`` / ``http``: hosted remote MCP server (e.g. Railway)
    - ``sse``: legacy Server-Sent Events transport

    For HTTP/SSE modes the server binds to ``0.0.0.0`` on ``$PORT`` (Railway,
    Fly.io, Render, etc. all set this) or ``MCP_PORT`` if provided.
    """

    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport == "http":
        transport = "streamable-http"

    if transport == "stdio":
        server.run(transport="stdio")
        return

    if transport not in ("streamable-http", "sse"):
        raise ValueError(f"Unsupported MCP_TRANSPORT: {transport}")

    # HTTP / SSE: run uvicorn ourselves so we can enable proxy_headers, which
    # is required when fronted by Railway / Fly / Render / Cloudflare. Without
    # this, FastMCP's redirects use http:// instead of https:// and the
    # forwarded Host header gets rejected with "Invalid Host header".
    import uvicorn

    port = int(os.environ.get("PORT") or os.environ.get("MCP_PORT") or 8000)
    server.settings.host = "0.0.0.0"
    server.settings.port = port

    if transport == "streamable-http":
        app = server.streamable_http_app()
    else:
        app = server.sse_app()

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level=os.environ.get("MCP_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
