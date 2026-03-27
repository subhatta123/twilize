"""Compatibility entrypoint for twilize's MCP server."""

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
    """Run the MCP server via stdio transport."""

    server.run(transport="stdio")


if __name__ == "__main__":
    main()
