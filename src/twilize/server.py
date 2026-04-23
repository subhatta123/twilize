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
    repair_calc_fields,
    validate_calc_fields,
    validate_workbook,
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


def _wrap_with_api_key_auth(app, valid_keys, open_paths):
    """Return an ASGI app that 401s requests missing a valid API key.

    Pure ASGI middleware (no Starlette dependency) so it works regardless of
    what FastMCP returns from streamable_http_app() / sse_app().
    """

    async def auth_app(scope, receive, send):
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in open_paths:
            await app(scope, receive, send)
            return

        provided = None
        for raw_name, raw_value in scope.get("headers", []):
            name = raw_name.decode("latin-1").lower()
            if name == "authorization":
                value = raw_value.decode("latin-1")
                if value.lower().startswith("bearer "):
                    provided = value[7:].strip()
                    break
            elif name == "x-api-key":
                provided = raw_value.decode("latin-1").strip()
                break

        if provided and provided in valid_keys:
            await app(scope, receive, send)
            return

        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b'Bearer realm="twilize"'),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": b'{"error":"unauthorized","detail":"missing or invalid API key"}',
        })

    return auth_app


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

    # MCP SDK enables DNS rebinding protection by default and only allows
    # localhost. On a public cloud deployment that rejects every request with
    # 421 "Invalid Host header". Honor MCP_ALLOWED_HOSTS / MCP_ALLOWED_ORIGINS
    # if provided, otherwise allow all (the deploy is already public).
    sec = server.settings.transport_security
    allowed_hosts = os.environ.get("MCP_ALLOWED_HOSTS", "*")
    allowed_origins = os.environ.get("MCP_ALLOWED_ORIGINS", "*")
    sec.allowed_hosts = [h.strip() for h in allowed_hosts.split(",") if h.strip()]
    sec.allowed_origins = [o.strip() for o in allowed_origins.split(",") if o.strip()]
    if sec.allowed_hosts == ["*"] and sec.allowed_origins == ["*"]:
        sec.enable_dns_rebinding_protection = False

    if transport == "streamable-http":
        app = server.streamable_http_app()
    else:
        app = server.sse_app()

    # Optional shared-secret auth. Set MCP_API_KEY to require every request
    # to carry one of:
    #   Authorization: Bearer <key>
    #   X-API-Key: <key>
    # Multiple comma-separated keys are allowed (e.g. one for Smithery, one
    # for direct clients) so secrets can be rotated without downtime.
    api_keys_raw = os.environ.get("MCP_API_KEY", "").strip()
    if api_keys_raw:
        valid_keys = {k.strip() for k in api_keys_raw.split(",") if k.strip()}
        # Health check / well-known paths stay open so platforms can probe.
        open_paths = {"/", "/health", "/healthz", "/.well-known/health"}
        app = _wrap_with_api_key_auth(app, valid_keys, open_paths)

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
