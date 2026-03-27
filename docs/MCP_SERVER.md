# twilize MCP Server — Complete Reference

> **Model Context Protocol (MCP) server for Tableau Workbook (.twb/.twbx) generation**
>
> Equivalent to a Tableau Extension `.trex` manifest but for AI agent workflows.

## Server Identity

| Field | Value |
|---|---|
| **ID** | `com.twilize.mcp-server` |
| **Name** | `twilize` |
| **Version** | 0.13.0 |
| **Transport** | stdio |
| **Runtime** | Python >= 3.10 |
| **License** | AGPL-3.0-or-later |
| **Author** | Suddhasheel Bhattacharya |
| **Repository** | [github.com/subhatta123/twilize](https://github.com/subhatta123/twilize) |

## Installation

```bash
# From PyPI
pip install twilize

# Run directly (no install required)
uvx twilize

# With optional features
pip install "twilize[pipeline]"    # CSV-to-Hyper conversion
pip install "twilize[examples]"    # Hyper-backed examples
pip install "twilize[extension]"   # Tableau Dashboard Extension backend
```

## Client Configuration

### Claude Desktop

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "twilize": {
      "command": "uvx",
      "args": ["twilize"]
    }
  }
}
```

### Claude Code

```bash
claude mcp add twilize -- uvx twilize
```

### Cursor IDE

1. **Settings** → **Features** → **MCP**
2. Click **Add New MCP Server**
3. Type: `command` | Name: `twilize` | Command: `uvx twilize`

### VS Code

Add to `.vscode/mcp.json` or user MCP config:

```json
{
  "servers": {
    "twilize": {
      "command": "uvx",
      "args": ["twilize"]
    }
  }
}
```

### Docker

```bash
docker build -t twilize .
docker run -i twilize
```

## Permissions

| Permission | Purpose |
|---|---|
| **file-read** | Read template .twb/.twbx files, Excel datasources, CSV files, Hyper extracts |
| **file-write** | Write generated .twb/.twbx workbooks, layout JSON, migration reports |

The server operates locally — no network access is required for core functionality.

---

## Stateful Session Model

The MCP server holds a single `TWBEditor` instance per session. Tools must be called in order:

```
1. create_workbook(template)  OR  open_workbook(file)    ← required first
2. list_fields()                                          ← inspect available fields
3. add_calculated_field() / add_parameter()               ← optional setup
4. add_worksheet(name)                                    ← create worksheets
5. configure_chart() / configure_dual_axis()              ← configure visualizations
6. configure_worksheet_style()                            ← optional styling
7. add_dashboard(name, worksheets)                        ← compose dashboards
8. add_dashboard_action()                                 ← optional interactions
9. save_workbook(output_path)                             ← write to disk
```

Any tool that accesses the workbook will raise `RuntimeError` if step 1 was skipped.

---

## Tool Reference (40 tools)

### Workbook Management

#### `create_workbook`

Create a new workbook from a TWB or TWBX template file.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `template_path` | string | `""` | Path to .twb/.twbx template. Empty string uses built-in Superstore template |
| `workbook_name` | string | `""` | Optional display name for the workbook |

**Returns**: Field list from the template datasource.

#### `open_workbook`

Open an existing workbook for in-place editing, preserving all existing worksheets and dashboards.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_path` | string | yes | Path to .twb or .twbx file |

**Returns**: Workbook state summary with worksheet and dashboard lists.

#### `save_workbook`

Save the workbook to disk. Use `.twbx` extension to create a packaged workbook that bundles extracts and images.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `output_path` | string | yes | Output file path (.twb or .twbx) |

**Returns**: Confirmation with file path.

#### `list_fields`

List all available dimensions and measures in the current datasource.

**Returns**: Formatted field list grouped by dimensions and measures.

#### `list_worksheets`

List worksheet names in the active workbook.

**Returns**: Formatted worksheet name list.

#### `list_dashboards`

List dashboards and the worksheet zones they reference.

**Returns**: Dashboard names with zone membership details.

#### `undo_last_change`

Undo the last mutating operation. Maintains a stack of up to 20 snapshots.

**Returns**: Description of what was undone and remaining undo steps.

---

### Fields & Parameters

#### `add_calculated_field`

Add a calculated field with Tableau formula syntax.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `field_name` | string | — | Name for the calculated field |
| `formula` | string | — | Tableau calculation formula (e.g. `SUM([Profit])/SUM([Sales])`) |
| `datatype` | string | `"real"` | Data type: `real`, `integer`, `string`, `boolean`, `date`, `datetime` |
| `role` | string | `""` | Role override: `dimension` or `measure` |
| `field_type` | string | `""` | Field type: `nominal`, `ordinal`, `quantitative` |
| `default_format` | string | `""` | Default number format (e.g. `"0.0%"`, `"$#,##0"`) |

#### `remove_calculated_field`

Remove a previously added calculated field by name.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `field_name` | string | yes | Name of the field to remove |

#### `add_parameter`

Add an interactive parameter for what-if analysis.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | string | — | Parameter display name |
| `datatype` | string | `"real"` | Data type |
| `default_value` | string | `"0"` | Default/current value |
| `domain_type` | string | `"range"` | `"range"` or `"list"` |
| `min_value` | string | `""` | Minimum value (range only) |
| `max_value` | string | `""` | Maximum value (range only) |
| `granularity` | string | `""` | Step size (range only) |
| `allowed_values` | list[string] | null | Allowed values (list only) |
| `default_format` | string | `""` | Number format |

---

### Charts & Worksheets

#### `add_worksheet`

Add a new blank worksheet to the workbook.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `worksheet_name` | string | yes | Name for the new worksheet |

#### `configure_chart`

Configure chart type and field mappings for a worksheet.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `worksheet_name` | string | — | Target worksheet (must exist) |
| `mark_type` | string | `"Automatic"` | `Bar`, `Line`, `Area`, `Pie`, `Map`, `Circle`, `Square`, `Text`, `Automatic`, `Shape`, `Gantt Bar` |
| `columns` | list[string] | null | Column shelf fields |
| `rows` | list[string] | null | Row shelf fields |
| `color` | string | null | Color encoding field |
| `size` | string | null | Size encoding field |
| `label` | string | null | Label encoding field |
| `detail` | string | null | Detail level-of-detail field |
| `wedge_size` | string | null | Pie wedge size measure |
| `sort_descending` | string | null | Field to sort descending |
| `tooltip` | string or list[string] | null | Tooltip field(s) |
| `filters` | list[dict] | null | Filter definitions |
| `geographic_field` | string | null | Geographic role field for maps |
| `measure_values` | list[string] | null | Measure values list |
| `map_fields` | list[string] | null | Map detail fields |
| `mark_sizing_off` | bool | false | Disable automatic mark sizing |
| `axis_fixed_range` | dict | null | Fixed axis range `{min, max}` |
| `customized_label` | string | null | Custom label text |
| `color_map` | dict[string, string] | null | Value-to-color mapping |
| `text_format` | dict[string, string] | null | Text formatting options |
| `map_layers` | list[dict] | null | Map layer configuration |
| `label_runs` | list[dict] | null | Rich-text label runs for multi-style labels |
| `label_param` | string | null | Parameter for dynamic labels |

**Filter dict format**:
```json
{"field": "Category", "values": ["Furniture", "Technology"]}
{"field": "Sales", "min": 100, "max": 1000}
{"field": "Order Date", "relative": "last-12-months"}
```

#### `configure_dual_axis`

Configure a dual-axis chart with two overlaid mark types.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `worksheet_name` | string | — | Target worksheet |
| `mark_type_1` | string | `"Bar"` | Primary axis mark type |
| `mark_type_2` | string | `"Line"` | Secondary axis mark type |
| `columns` | list[string] | null | Column shelf |
| `rows` | list[string] | null | Row shelf (first two measures become dual axes) |
| `dual_axis_shelf` | string | `"rows"` | Which shelf holds the dual measures |
| `color_1/2` | string | null | Color for each axis |
| `size_1/2` | string | null | Size for each axis |
| `label_1/2` | string | null | Label for each axis |
| `detail_1/2` | string | null | Detail for each axis |
| `synchronized` | bool | true | Synchronize axes |
| `sort_descending` | string | null | Sort field |
| `filters` | list[dict] | null | Filters |
| `wedge_size_1/2` | string | null | Wedge size per axis |
| `show_labels` | bool | true | Show labels |
| `hide_axes` | bool | false | Hide axis headers |
| `hide_zeroline` | bool | false | Hide zero line |
| `mark_sizing_off` | bool | false | Disable mark sizing |
| `size_value_1/2` | string | null | Fixed size value per axis |
| `mark_color_1/2` | string | null | Fixed mark color per axis (hex) |
| `reverse_axis_1` | bool | false | Reverse primary axis |
| `color_map_1` | dict[string, string] | null | Color map for primary axis |

#### `configure_chart_recipe`

Configure a showcase recipe chart.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `worksheet_name` | string | — | Target worksheet |
| `recipe_name` | string | — | Recipe: `donut`, `lollipop`, `bullet`, `bump`, `butterfly`, `calendar` |
| `recipe_args` | dict[string, string] | null | Recipe-specific arguments |
| `auto_ensure_prerequisites` | bool | true | Auto-create required calc fields |

#### `configure_worksheet_style`

Apply worksheet-level styling.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `worksheet_name` | string | — | Target worksheet |
| `background_color` | string | null | Background color (hex) |
| `hide_axes` | bool | false | Hide axis headers |
| `hide_gridlines` | bool | false | Hide grid lines |
| `hide_zeroline` | bool | false | Hide zero line |
| `hide_borders` | bool | false | Hide borders |
| `hide_band_color` | bool | false | Hide alternating band color |
| `hide_col_field_labels` | bool | false | Hide column field labels |
| `hide_row_field_labels` | bool | false | Hide row field labels |
| `hide_droplines` | bool | false | Hide drop lines |
| `hide_reflines` | bool | false | Hide reference lines |
| `hide_table_dividers` | bool | false | Hide table dividers |
| `disable_tooltip` | bool | false | Disable tooltip |
| `pane_cell_style` | dict | null | Pane cell formatting |
| `pane_datalabel_style` | dict | null | Data label formatting |
| `pane_mark_style` | dict | null | Mark formatting |
| `pane_trendline_hidden` | bool | false | Hide trend lines |
| `label_formats` | list[dict] | null | Per-field label formats |
| `cell_formats` | list[dict] | null | Per-field cell formats |
| `header_formats` | list[dict] | null | Per-field header formats |
| `axis_style` | dict | null | Axis tick and style control |

#### `add_reference_line`

Add a reference line to a worksheet.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `worksheet_name` | string | — | Target worksheet |
| `axis_field` | string | — | Field on the axis to attach to |
| `value` | string | `""` | Constant value (for constant formula) |
| `formula` | string | `"constant"` | `constant`, `average`, `median`, `min`, `max` |
| `scope` | string | `"per-pane"` | `per-pane`, `per-cell`, `per-table` |
| `label_type` | string | `"automatic"` | `automatic`, `value`, `computation`, `custom`, `none` |
| `label` | string | `""` | Custom label text |
| `line_color` | string | `""` | Line color (hex) |

#### `add_reference_band`

Add a reference band (shaded region) to a worksheet.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `worksheet_name` | string | — | Target worksheet |
| `axis_field` | string | — | Field on the axis |
| `from_value` | string | `""` | Lower bound value |
| `to_value` | string | `""` | Upper bound value |
| `from_formula` | string | `"constant"` | Lower bound formula |
| `to_formula` | string | `"constant"` | Upper bound formula |
| `scope` | string | `"per-pane"` | Scope |
| `fill_color` | string | `"#E0E0E0"` | Band fill color (hex) |

#### `add_trend_line`

Add a trend line to a worksheet.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `worksheet_name` | string | — | Target worksheet |
| `fit` | string | `"linear"` | `linear`, `polynomial`, `logarithmic`, `exponential`, `power` |
| `degree` | int | 2 | Polynomial degree (polynomial fit only) |
| `show_confidence_bands` | bool | false | Show confidence bands |
| `exclude_color` | bool | false | Exclude color from trend calculation |

#### `apply_color_palette`

Set a color palette for the workbook.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `palette_name` | string | `""` | Built-in: `tableau10`, `tableau20`, `blue-red`, `green-gold` |
| `colors` | list[string] | null | Custom color list (hex values) |
| `custom_name` | string | `"twilize-palette"` | Name for custom palette |

---

### Dashboards

#### `add_dashboard`

Create a dashboard combining multiple worksheets.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `dashboard_name` | string | — | Dashboard name |
| `worksheet_names` | list[string] | — | Worksheets to include |
| `width` | int | 1200 | Dashboard width (px) |
| `height` | int | 800 | Dashboard height (px) |
| `layout` | string or dict | `"vertical"` | `vertical`, `horizontal`, `grid-2x2`, dict, or path to layout JSON |

#### `add_dashboard_action`

Add filter or highlight interaction actions.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `dashboard_name` | string | — | Target dashboard |
| `action_type` | string | — | `filter` or `highlight` |
| `source_sheet` | string | — | Source worksheet |
| `target_sheet` | string | — | Target worksheet |
| `fields` | list[string] | — | Fields to use for the action |
| `event_type` | string | `"on-select"` | Trigger: `on-select`, `on-hover`, `on-menu` |
| `caption` | string | `""` | Action display name |

#### `apply_dashboard_theme`

Apply uniform styling to all zones in a dashboard.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `dashboard_name` | string | — | Target dashboard |
| `background_color` | string | `""` | Background color (hex) |
| `font_family` | string | `""` | Font family |
| `title_font_size` | string | `""` | Title font size |

#### `generate_layout_json`

Build and save a structured dashboard flexbox layout JSON file.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `output_path` | string | yes | Path to write the layout JSON |
| `layout_tree` | dict | yes | Nested layout tree structure |
| `ascii_preview` | string | yes | ASCII art preview of the layout |

**Layout tree structure**:
```json
{
  "type": "vertical",
  "children": [
    {"type": "worksheet", "name": "Sheet1", "width": 600, "height": 400},
    {"type": "horizontal", "children": [
      {"type": "worksheet", "name": "Sheet2"},
      {"type": "worksheet", "name": "Sheet3"}
    ]}
  ]
}
```

---

### Connections

#### `set_mysql_connection`

Configure the datasource to use a local MySQL connection.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `server` | string | — | MySQL server hostname |
| `dbname` | string | — | Database name |
| `username` | string | — | MySQL username |
| `table_name` | string | — | Table name |
| `port` | string | `"3306"` | MySQL port |

#### `set_tableauserver_connection`

Configure connection to a Tableau Server.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `server` | string | — | Tableau Server hostname |
| `dbname` | string | — | Database/site name |
| `username` | string | — | Username |
| `table_name` | string | — | Published datasource name |
| `directory` | string | `"/dataserver"` | Server directory |
| `port` | string | `"82"` | Server port |

#### `set_hyper_connection`

Configure the datasource to use a local Hyper extract.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `filepath` | string | — | Path to .hyper file |
| `table_name` | string | `"Extract"` | Table name inside the Hyper file |
| `tables` | list[dict] | null | Multi-table configuration |

#### `inspect_target_schema`

Inspect the schema of a target datasource (Excel or Hyper).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `target_source` | string | yes | Path to .xls/.xlsx/.xlsm/.xlsb or .hyper file |

---

### Analysis & Validation

#### `list_capabilities`

List the complete capability catalog showing support tiers for all chart types, encodings, and features.

**Returns**: Formatted capability table with core/advanced/recipe/unsupported tiers.

#### `describe_capability`

Get details for a specific capability entry.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `kind` | string | yes | Category: `chart`, `encoding`, `feature` |
| `name` | string | yes | Capability name |

#### `analyze_twb`

Analyze a TWB file against the capability catalog. Returns both the full breakdown and gap triage.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_path` | string | yes | Path to .twb file |

#### `diff_template_gap`

Return only the capability gap section for a TWB template.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_path` | string | yes | Path to .twb file |

#### `validate_workbook`

Validate against the official Tableau TWB XSD schema (2026.1).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | string | null | Path to .twb/.twbx. If omitted, validates current open workbook |

---

### CSV Pipeline

#### `inspect_csv`

Inspect a CSV file and return inferred schema with column classification.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `csv_path` | string | — | Path to CSV file |
| `sample_rows` | int | 1000 | Rows to sample for inference |
| `encoding` | string | `"utf-8"` | File encoding |

#### `suggest_charts_for_csv`

Suggest chart types and configurations for a CSV file.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `csv_path` | string | — | Path to CSV file |
| `max_charts` | int | 6 | Maximum suggestions |
| `sample_rows` | int | 1000 | Rows to sample |

#### `csv_to_hyper`

Convert a CSV file to a Tableau Hyper extract.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `csv_path` | string | — | Source CSV path |
| `hyper_path` | string | — | Output .hyper path |
| `table_name` | string | `"Extract"` | Table name in the Hyper file |
| `sample_rows` | int | 1000 | Rows for type inference |

**Requires**: `pip install "twilize[pipeline]"` (tableauhyperapi)

#### `csv_to_dashboard`

Build a complete Tableau dashboard from a CSV file (end-to-end).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `csv_path` | string | — | Source CSV path |
| `output_path` | string | `""` | Output .twbx path (auto-derived if empty) |
| `dashboard_title` | string | `""` | Dashboard title (auto-derived if empty) |
| `max_charts` | int | 6 | Maximum charts to include |
| `template_path` | string | `""` | TWB template path |

---

### Migration

#### `profile_twb_for_migration`

Profile workbook datasources and worksheet scope before migration.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | string | — | Path to source .twb |
| `scope` | string | `"workbook"` | `"workbook"` or `"worksheet:Name"` |
| `target_source` | string | `""` | Optional target datasource path |

#### `propose_field_mapping`

Fuzzy-match source fields to target fields with confidence scores.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | string | — | Source .twb path |
| `target_source` | string | — | Target datasource path |
| `scope` | string | `"workbook"` | Migration scope |
| `mapping_overrides` | dict[string, string] | null | Force specific mappings |

#### `preview_twb_migration`

Dry-run a migration: report blockers, warnings, and rewrite summary without writing files.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | string | — | Source .twb path |
| `target_source` | string | — | Target datasource path |
| `scope` | string | `"workbook"` | Migration scope |
| `mapping_overrides` | dict[string, string] | null | Force specific mappings |

#### `apply_twb_migration`

Apply the migration and write output files.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | string | — | Source .twb path |
| `target_source` | string | — | Target datasource path |
| `output_path` | string | — | Output .twb path |
| `scope` | string | `"workbook"` | Migration scope |
| `mapping_overrides` | dict[string, string] | null | Force specific mappings |

**Output files**: `<output>.twb`, `migration_report.json`, `field_mapping.json`

#### `migrate_twb_guided`

Convenience wrapper that runs the full migration workflow with automatic pause for warning confirmation.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | string | — | Source .twb path |
| `target_source` | string | — | Target datasource path |
| `output_path` | string | `""` | Output path (auto-derived if empty) |
| `scope` | string | `"workbook"` | Migration scope |
| `mapping_overrides` | dict[string, string] | null | Force specific mappings |
| `apply_if_no_blockers` | bool | true | Auto-apply if no blocking issues |

---

## Resources

| URI | Description |
|---|---|
| `file://docs/tableau_all_functions.json` | Complete Tableau function reference (syntax, examples) |
| `twilize://skills/index` | Agent skill index with descriptions |
| `twilize://skills/calculation_builder` | Expert guide for writing Tableau formulas |
| `twilize://skills/chart_builder` | Chart type selection and encoding best practices |
| `twilize://skills/dashboard_designer` | Layout patterns, zone sizing, action wiring |
| `twilize://skills/formatting` | Color palettes, fonts, style consistency |

---

## Capability Model

### Core (Stable)

Bar, Line, Area, Pie, Map, Text/KPI, Parameters, Calculated Fields, Basic Dashboards

### Advanced

Scatterplot, Heatmap, TreeMap, Bubble, Dual Axis, Table Calculations, KPI Badges, Donut, Rich-text Labels, Advanced Styling, Filter Zones, Dashboard Actions, Declarative Layouts

### Recipes (Showcase)

Donut, Lollipop, Bullet, Bump, Butterfly, Calendar

### Unsupported

Gantt (partial), Polygon, Custom SQL, Live connections (non-Hyper), Sets, Groups, Bins, LOD expressions (FIXED/INCLUDE/EXCLUDE), Story points, Animations, Web page zones, Extension zones

---

## Comparison: .trex vs MCP Server Manifest

| .trex Field | MCP Equivalent | Location |
|---|---|---|
| `id` | `id` | `mcp-server.json` → `id` |
| `extension-version` | `version` | `mcp-server.json` → `version` |
| `name` | `name` | `mcp-server.json` → `name` |
| `description` | `description` | `mcp-server.json` → `description` |
| `author` | `author` | `mcp-server.json` → `author` |
| `min-api-version` | `minPythonVersion` | `mcp-server.json` → `minPythonVersion` |
| `source-location/url` | `command` | `mcp-server.json` → `command` |
| `icon` | `icon` | `mcp-server.json` → `icon` |
| `permissions` | `permissions` | `mcp-server.json` → `permissions` |
| `default-locale` | N/A | MCP is locale-independent |
| N/A | `tools` | 40 declared tools with categories |
| N/A | `resources` | 6 read-only reference resources |
| N/A | `clientConfigurations` | Pre-built configs for 4 MCP clients |

---

## Publishing Checklist

- [x] `pyproject.toml` — PyPI metadata, classifiers, dependencies
- [x] `mcp-server.json` — MCP server manifest (`.trex` equivalent)
- [x] `smithery.yaml` — Smithery registry configuration
- [x] `README.md` — User-facing documentation
- [x] `docs/MCP_SERVER.md` — Complete tool reference
- [x] `CHANGELOG.md` — Version history
- [x] `LICENSE` — AGPL-3.0-or-later
- [x] `CONTRIBUTING.md` — Contributor guidelines
- [x] `__main__.py` — `python -m twilize` entry point
- [x] `server.py` — `twilize` CLI entry point via `[project.scripts]`

### Publish to PyPI

```bash
# Build
pip install build
python -m build

# Upload to PyPI
pip install twine
twine upload dist/*

# Or use uv
uv build
uv publish
```

### Register on Smithery

Submit `smithery.yaml` to the [Smithery MCP Registry](https://smithery.ai).

### Register on MCP Registry

Submit `mcp-server.json` to the [MCP Server Registry](https://github.com/modelcontextprotocol/servers).
