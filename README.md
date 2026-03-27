# twilize

> **Tableau Workbook (.twb/.twbx) generation toolkit for reproducible dashboards and workbook engineering**
> Programmatically create Tableau workbooks with stable analytical primitives, dashboard composition, and built-in structural validation.

## Overview

**twilize** is a Model Context Protocol (MCP) server and Python toolkit for generating Tableau Desktop workbook files (`.twb` / `.twbx`) from code or AI-driven tool calls.

It is designed as a **workbook engineering layer**, not as a conversational data exploration agent. The goal is to make workbook generation reproducible, inspectable, and safe to automate in local workflows, scripts, and CI.

The default workflow is:

1. Start from a known template (`.twb` or `.twbx`) or the built-in zero-config template
2. Add calculated fields and parameters
3. Build worksheets from stable chart primitives
4. Assemble dashboards and interactions
5. Save and validate a `.twb` or `.twbx` that opens in Tableau Desktop

```
                            Interfaces
  ┌───────────────────────────────────────────────────────────────┐
  │  ┌──────────────────────────┐  ┌───────────────────────────┐  │
  │  │        MCP Server        │  │      Python Library       │  │
  │  │  tools_workbook          │  │  from twilize.twb_editor    │  │
  │  │  tools_layout            │  │  import TWBEditor         │  │
  │  │  tools_migration         │  │                           │  │
  │  │  tools_support           │  │  editor.add_...()         │  │
  │  │                          │  │  editor.configure_...()   │  │
  │  │  (Claude / Cursor /      │  │  editor.save(...)         │  │
  │  │   VSCode / Claude Code)  │  │                           │  │
  │  └─────────────┬────────────┘  └──────────────┬────────────┘  │
  │                └──────────────┬────────────────┘               │
  └─────────────────────────────  ┼  ─────────────────────────────┘
                                  ▼
  ┌───────────────────────────────────────────────────────────────┐
  │                          TWBEditor                            │
  │       ParametersMixin  ·  ConnectionsMixin                    │
  │       ChartsMixin      ·  DashboardsMixin                     │
  └──────────┬──────────────────┬──────────────────┬─────────────┘
             ▼                  ▼                  ▼
  ┌──────────────────┐  ┌──────────────┐  ┌──────────────────────┐
  │  Chart Builders  │  │  Dashboard   │  │  Analysis &          │
  │                  │  │  System      │  │  Migration           │
  │  Basic  DualAxis │  │              │  │                      │
  │  Pie    Text     │  │  layouts     │  │  migration.py        │
  │  Map    Recipes  │  │  actions     │  │  twb_analyzer.py     │
  │                  │  │  dependencies│  │  capability_registry │
  └────────┬─────────┘  └──────┬───────┘  └──────────┬───────────┘
           └───────────────────┼──────────────────────┘
                               ▼
  ┌───────────────────────────────────────────────────────────────┐
  │                     XML Engine  (lxml)                        │
  │    template.twb/.twbx  →  patch  →  validate  →  save        │
  └───────────────────────────────┬───────────────────────────────┘
                                  ▼
                      output.twb  /  output.twbx
```

## Installation

```bash
pip install twilize
```

To run the bundled Hyper-backed example that inspects `.hyper` files and
resolves the physical `Orders_*` table automatically, install the optional
example dependency as well:

```bash
pip install "twilize[examples]"
```

### Requirements

- Python >= 3.10
- [lxml](https://lxml.de/) >= 5.0
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [mcp](https://pypi.org/project/mcp/) >= 1.0

## Quick Start

### As MCP Server

To allow an MCP client to build Tableau workbooks automatically, add `twilize`
to that client's MCP configuration.

The launch command is the same across clients:

```bash
uvx twilize
```

Each client stores this command in a different configuration format. Use the
matching example below.

#### Claude Desktop

Open `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS or `%APPDATA%\Claude\claude_desktop_config.json` on Windows and add:

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

#### Cursor IDE

1. Open **Cursor Settings** -> **Features** -> **MCP**
2. Click **Add New MCP Server**
3. Set **Type** to `command`
4. Set **Name** to `twilize`
5. Set **Command** to `uvx twilize`

#### Claude Code

```bash
claude mcp add twilize -- uvx twilize
```

#### VSCode

Open the workspace `.vscode/mcp.json` file or your user-profile `mcp.json`
file and add:

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

In VSCode, you can open these files from the Command Palette with
**MCP: Open Workspace Folder Configuration** or
**MCP: Open User Configuration**. You can also use **MCP: Add Server** and
enter the same `uvx twilize` command through the guided flow.

### As Python Library

Use `TWBEditor(...)` to start from a template and rebuild workbook content.
Use `TWBEditor.open_existing(...)` when you want to keep existing worksheets
and dashboards and reconfigure a sheet in place.

```python
from twilize.twb_editor import TWBEditor

editor = TWBEditor("")  # "" uses the built-in Superstore template
editor.clear_worksheets()
editor.add_calculated_field("Profit Ratio", "SUM([Profit])/SUM([Sales])")

editor.add_worksheet("Sales by Category")
editor.configure_chart(
    worksheet_name="Sales by Category",
    mark_type="Bar",
    rows=["Category"],
    columns=["SUM(Sales)"],
)

editor.add_worksheet("Segment Pie")
editor.configure_chart(
    worksheet_name="Segment Pie",
    mark_type="Pie",
    color="Segment",
    wedge_size="SUM(Sales)",
)

editor.add_dashboard(
    dashboard_name="Overview",
    worksheet_names=["Sales by Category", "Segment Pie"],
    layout="horizontal",
)

editor.save("output/my_workbook.twb")
```

### Working with Packaged Workbooks (.twbx)

`.twbx` files are ZIP archives that bundle the workbook XML together with data extracts (`.hyper`) and image assets. twilize reads and writes them transparently:

```python
from twilize.twb_editor import TWBEditor

# Open a packaged workbook — extracts and images are preserved automatically
editor = TWBEditor.open_existing("templates/dashboard/MyDashboard.twbx")

# Make changes as usual
editor.add_calculated_field("Profit Ratio", "SUM([Profit])/SUM([Sales])")

# Save as .twbx — re-bundles the updated .twb with the original extracts/images
editor.save("output/MyDashboard_v2.twbx")

# Or extract just the XML when the packaged format isn't needed
editor.save("output/MyDashboard_v2.twb")
```

A plain `.twb` can also be packaged:

```python
editor = TWBEditor("templates/twb/superstore.twb")
# ...
editor.save("output/superstore.twbx")  # produces a single-entry ZIP with the .twb inside
```

## MCP Tools

| Tool | Description |
|---|---|
| `create_workbook` | Load a `.twb` or `.twbx` template and initialize a rebuild-from-template workspace |
| `open_workbook` | Open an existing `.twb` or `.twbx` and keep its worksheets and dashboards for editing |
| `list_fields` | List all available dimensions and measures |
| `list_worksheets` | List worksheet names in the active workbook |
| `list_dashboards` | List dashboards and the worksheet zones they reference |
| `add_parameter` | Add an interactive parameter for what-if analysis |
| `add_calculated_field` | Add a calculated field with Tableau formula |
| `remove_calculated_field` | Remove a previously added calculated field |
| `add_worksheet` | Add a new blank worksheet |
| `configure_chart` | Configure chart type and field mappings |
| `configure_worksheet_style` | Apply worksheet-level styling: background color, axis/grid/border visibility |
| `configure_dual_axis` | Configure a dual-axis chart composition |
| `configure_chart_recipe` | Configure a showcase recipe chart such as `lollipop`, `donut`, `butterfly`, or `calendar` |
| `add_dashboard` | Create a dashboard combining worksheets |
| `add_dashboard_action` | Add filter or highlight actions to a dashboard |
| `generate_layout_json` | Build an interactive structured dashboard flexbox layout |
| `list_capabilities` | Show twilize's declared support boundary |
| `describe_capability` | Explain whether a chart or feature is core, advanced, recipe, or unsupported |
| `analyze_twb` | Analyze a `.twb` file against the capability catalog; output includes both the full capability breakdown and the capability gap triage summary |
| `diff_template_gap` | Summarize the non-core gap of a template |
| `validate_workbook` | Validate a workbook against the official Tableau TWB XSD schema (2026.1) |
| `migrate_twb_guided` | Run the built-in TWB migration workflow and pause for warning confirmation when needed |
| `set_mysql_connection` | Configure the datasource to use a local MySQL connection |
| `set_tableauserver_connection` | Configure connection to an online Tableau Server |
| `set_hyper_connection` | Configure the datasource to use a local Hyper extract connection |
| `save_workbook` | Save the workbook as `.twb` (plain XML) or `.twbx` (ZIP with bundled extracts and images) |

## Capability Model

### Core primitives

These are the stable building blocks the project should continue to promise:

- **Bar**
- **Line**
- **Area**
- **Pie**
- **Map**
- **Text** / KPI cards
- Parameters and calculated fields
- Basic dashboard composition

### Advanced patterns

These are supported, but they are higher-level compositions or interaction features rather than the default surface area:

- **Scatterplot**
- **Heatmap**
- **Tree Map**
- **Bubble Chart**
- **Dual Axis** — `mark_color_1/2`, `color_map_1`, `reverse_axis_1`, `hide_zeroline`, `synchronized`
- **Table Calculations** — `RANK_DENSE`, `RUNNING_SUM`, `WINDOW_SUM` via `add_calculated_field(table_calc="Rows")`
- **KPI Difference badges** — `MIN(1)` dummy axis + `axis_fixed_range` + `color_map` + `customized_label`
- **Donut (via extra_axes)** — multi-pane Pie + white circle using `configure_dual_axis(extra_axes=[...])`; supports `color_map` for `:Measure Names` palette
- **Rich-text labels** — `configure_chart(label_runs=[...])` for multi-style KPI cards and dynamic titles with inline field values
- **Advanced worksheet styling** — `configure_worksheet_style` supports pane-level cell/datalabel/mark styles, per-field label/cell/header formats, axis tick control, tooltip disabling, and all Tableau visual noise suppressions
- **Row dimension header suppression** — `configure_worksheet_style(hide_row_label="FieldName")`
- Filter zones, parameter controls, color legends
- Dashboard filter and highlight actions
- Declarative JSON layout workflows
- Dashboard zone title control via `show_title: false` in layout dicts

### Recipes and showcase patterns

These can be generated today, but they should be treated as recipes or examples rather than first-class promises:

- **Donut**
- **Lollipop**
- **Bullet**
- **Bump**
- **Butterfly**
- **Calendar**

Recipe charts are intentionally exposed through a single `configure_chart_recipe`
tool so the public MCP surface does not grow one tool at a time for every
showcase pattern.

This distinction matters because `twilize` is not trying to become a chart zoo or compete with Tableau's own conversational analysis tooling. The project is strongest when it provides a reliable, automatable workbook generation layer.

### Capability-first workflow

When you are not sure whether something belongs in the stable SDK surface:

1. Use `list_capabilities` to inspect the declared boundary
2. Use `describe_capability` to check a specific chart, encoding, or feature
3. Use `analyze_twb` or `diff_template_gap` before chasing a showcase template

This keeps new feature work aligned with the project's real product boundary instead of with whatever happens to appear in a sample workbook.

## Built-in Validation

### Structural validation

`save()` automatically validates the TWB XML structure before writing:

- **Fatal errors** such as missing `<workbook>` or `<datasources>` raise `TWBValidationError`
- **Warnings** such as missing `<view>` or `<panes>` are logged but do not block saving
- Validation can be disabled with `editor.save("output.twb", validate=False)` or `editor.save("output.twbx", validate=False)`

### XSD schema validation

`TWBEditor.validate_schema()` checks the workbook against the official Tableau TWB XSD schema (2026.1), vendored at `vendor/tableau-document-schemas/`:

```python
result = editor.validate_schema()
print(result.to_text())
# PASS  Workbook is valid against Tableau TWB XSD schema (2026.1)
# — or —
# FAIL  Schema validation failed (2 error(s)):
#   * Element 'workbook': Missing child element(s)...

result.valid          # bool
result.errors         # list[str] — lxml error messages
result.schema_available  # False if the vendor submodule is not checked out
```

The same check is available as an MCP tool:

```
validate_workbook()                       # validate current open workbook in memory
validate_workbook(file_path="out.twb")    # validate a file on disk (.twb or .twbx)
```

XSD errors are **informational** — Tableau itself generates workbooks that occasionally deviate from the schema — but recurring errors signal structural problems worth fixing.

## Dashboard Layouts

| Layout | Description |
|---|---|
| `vertical` | Stack worksheets top to bottom |
| `horizontal` | Place worksheets side by side |
| `grid-2x2` | 2x2 grid layout for up to four worksheets |
| `dict` or `.json` path | Declarative custom layouts for more complex dashboards |

Custom layouts can be built programmatically using a nested `layout` dictionary or via `generate_layout_json` for MCP workflows.

## Hyper-backed Example

The `examples/hyper_and_new_charts.py` example uses the `Sample - EU Superstore.hyper`
extract bundled directly in the package (`src/twilize/references/`) and resolves the
physical `Orders_*` table via Tableau Hyper API before switching the workbook connection.
No repository clone is needed — install with `pip install "twilize[examples]"` and run directly.

## Workbook Migration

twilize includes a migration subsystem for switching an existing `.twb` to a new
datasource — for example, repointing a workbook built on one Excel file to a
different Excel with a different schema, or migrating between language variants
of the same dataset.

### How it works

Migration is a multi-step workflow. Each step is available as both an MCP tool
and a Python function:

```
1. inspect_target_schema   →  Scan the target Excel and list its columns
2. profile_twb_for_migration  →  Inventory which fields the workbook uses
3. propose_field_mapping   →  Match source fields to target columns (fuzzy)
4. preview_twb_migration   →  Dry-run: show what would change, blockers/warnings
5. apply_twb_migration     →  Write the migrated .twb + JSON reports
```

`migrate_twb_guided` is a convenience wrapper that runs steps 2–5 in sequence
and pauses automatically when only low-confidence field matches remain, returning
a `warning_review_bundle` for human review before proceeding.

### Python example

```python
from twilize.migration import migrate_twb_guided_json
import json

# One-call guided migration
result = migrate_twb_guided_json(
    file_path="templates/SalesDashboard.twb",
    target_source="data/new_data_source.xlsx",
    output_path="output/SalesDashboard_migrated.twb",
)
bundle = json.loads(result)

if bundle["status"] == "warning_review_required":
    # Inspect low-confidence matches and confirm or override them
    print(bundle["warning_review_bundle"])
    # Re-run with confirmed mappings
    result = migrate_twb_guided_json(
        file_path="templates/SalesDashboard.twb",
        target_source="data/new_data_source.xlsx",
        output_path="output/SalesDashboard_migrated.twb",
        mapping_overrides={"Old Field Name": "New Column Name"},
    )
```

### MCP tool example

When using twilize as an MCP server, an AI agent can run the full workflow:

```
inspect_target_schema(target_source="data/new_data_source.xlsx")
→ returns column list and data types

migrate_twb_guided(
    file_path="templates/SalesDashboard.twb",
    target_source="data/new_data_source.xlsx",
    output_path="output/SalesDashboard_migrated.twb"
)
→ returns status: "applied" or "warning_review_required"
```

### Output files

A completed migration writes three files:

| File | Contents |
|---|---|
| `<output>.twb` | Migrated workbook with rewritten field references |
| `migration_report.json` | Per-field status: mapped / warning / blocked |
| `field_mapping.json` | Final source→target field mapping for audit |

### Scope parameter

`scope="workbook"` migrates all worksheets. Pass a worksheet name to limit
migration to a single sheet.

### Self-contained example

`examples/migrate_workflow/` contains a template `.twb`, the original
Superstore Excel, a target Chinese-locale Superstore Excel, and a runnable
script:

```bash
python examples/migrate_workflow/test_migration_workflow.py
```

## Project Structure

```text
twilize/
|-- src/twilize/
|   |-- __init__.py
|   |-- capability_registry.py
|   |-- config.py
|   |-- charts/
|   |-- connections.py
|   |-- dashboard_actions.py
|   |-- dashboard_dependencies.py
|   |-- dashboard_layouts.py
|   |-- dashboards.py
|   |-- field_registry.py
|   |-- layout.py
|   |-- layout_model.py
|   |-- layout_rendering.py
|   |-- mcp/
|   |-- parameters.py
|   |-- twb_analyzer.py
|   |-- twb_editor.py
|   |-- validator.py
|   `-- server.py
|-- tests/
|-- examples/
|-- docs/
|-- pyproject.toml
`-- README.md
```

## Development

```bash
# Install in editable mode
pip install -e .

# Run test suite
pytest --basetemp=output/pytest_tmp

# Run the mixed showcase example
python examples/scripts/demo_all_supported_charts.py

# Run the advanced Hyper-backed example
python examples/scripts/demo_hyper_and_new_charts.py

# Run the guided migration example
python examples/migrate_workflow/test_migration_workflow.py

# Start MCP server
twilize
```

## MCP Server Manifest

twilize ships with a full MCP server manifest (`mcp-server.json`) — the MCP equivalent of a Tableau Extension `.trex` file. It declares:

| .trex Equivalent | MCP Field | Description |
|---|---|---|
| Extension ID | `id` | `com.twilize.mcp-server` |
| Version | `version` | Current package version |
| Name / Description | `name`, `description` | Server identity |
| Author | `author` | Name, email, org, URL |
| Permissions | `permissions` | `file-read`, `file-write` |
| Source URL | `command` | `uvx twilize` |
| Min API version | `minPythonVersion` | `3.10` |

Additionally, the manifest declares all 40 tools with categories, 6 resources, and pre-built client configurations for Claude Desktop, Cursor, VS Code, and Claude Code.

For the complete tool reference with parameter schemas, see [docs/MCP_SERVER.md](docs/MCP_SERVER.md).

## Publishing

### PyPI

```bash
pip install build twine
python -m build
twine upload dist/*
```

### Smithery MCP Registry

The `smithery.yaml` file is ready for submission to [Smithery](https://smithery.ai).

### MCP Server Registry

The `mcp-server.json` file follows the MCP server schema for registry submission.

## License

AGPL-3.0-or-later
