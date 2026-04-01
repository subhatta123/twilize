"""Dashboard creation mixin for TWBEditor.

DashboardsMixin is mixed into TWBEditor and provides:
  - add_dashboard(name, worksheet_names, layout, width, height)
  - add_dashboard_action(dashboard_name, action_type, source_sheet, target_sheet, fields)

LAYOUT MODEL
------------
The `layout` parameter accepts three forms:

  "vertical"   (default) — stack all worksheets top-to-bottom, equal height
  "horizontal"           — place all worksheets left-to-right, equal width
  dict or JSON file path — structured layout tree (see dashboard_layouts.py)

Structured layout tree example:
  {
    "type": "horizontal",
    "children": [
      {"type": "worksheet", "name": "Sidebar KPIs", "width": 300},
      {"type": "vertical", "children": [
        {"type": "worksheet", "name": "CY Sales"},
        {"type": "worksheet", "name": "Sales by Sub-Category"}
      ]}
    ]
  }

XML OUTPUT
----------
add_dashboard() writes a <dashboard> element under <dashboards> in the workbook:
  <dashboard name="..." type="automatic">
    <size maxheight="..." maxwidth="..." minheight="..." minwidth="..."/>
    <zones>
      <zone h="..." id="..." type="layout-flow" w="..." x="..." y="...">
        <zone name="Sheet1" param="Sheet1" type="worksheet" .../>
        <zone name="Sheet2" param="Sheet2" type="worksheet" .../>
      </zone>
    </zones>
    <devicelayouts/>
    <snapshots/>
  </dashboard>

Zone IDs are generated as UUIDs to avoid collisions across multiple dashboards.

ACTIONS
-------
add_dashboard_action() wires filter or highlight interactions between worksheets.
It writes an <action> element inside the <dashboard> using dashboard_actions.py.
"""

from __future__ import annotations

from typing import Optional

from lxml import etree

from .config import _generate_uuid
from .dashboard_actions import add_dashboard_action as _add_dashboard_action
from .dashboard_dependencies import add_dashboard_dependencies
from .c3_layout import build_c3_zones
from .dashboard_layouts import (
    render_dashboard_layout,
    resolve_dashboard_layout,
    validate_layout_worksheets,
    extract_layout_options,
)


class DashboardsMixin:
    """Mixin providing dashboard creation and action methods for TWBEditor."""

    def add_dashboard(
        self,
        dashboard_name: str,
        width: int = 1200,
        height: int = 800,
        layout: str | dict = "vertical",
        worksheet_names: Optional[list[str]] = None,
    ) -> str:
        """Create a dashboard and arrange worksheets."""
        worksheet_names = worksheet_names or []

        for ws_name in worksheet_names:
            self._find_worksheet(ws_name)

        dashboards = self.root.find("dashboards")
        if dashboards is None:
            insert_before = None
            for tag in ("windows", "external"):
                el = self.root.find(tag)
                if el is not None:
                    insert_before = el
                    break
            if insert_before is not None:
                dashboards = etree.Element("dashboards")
                insert_before.addprevious(dashboards)
            else:
                ws_el = self.root.find("worksheets")
                if ws_el is not None:
                    idx = list(self.root).index(ws_el) + 1
                    dashboards = etree.Element("dashboards")
                    self.root.insert(idx, dashboards)
                else:
                    dashboards = etree.SubElement(self.root, "dashboards")

        db = etree.SubElement(dashboards, "dashboard")
        db.set("name", dashboard_name)

        # Dashboard style — set background color from rules or default
        bg_color = "#dce8f0"
        if isinstance(layout, dict) and layout.get("_background_color"):
            bg_color = layout["_background_color"]
        db_style = etree.SubElement(db, "style")
        style_rule = etree.SubElement(db_style, "style-rule")
        style_rule.set("element", "table")
        fmt = etree.SubElement(style_rule, "format")
        fmt.set("attr", "background-color")
        fmt.set("value", bg_color)

        size_el = etree.SubElement(db, "size")
        size_el.set("maxheight", str(height))
        size_el.set("maxwidth", str(width))
        size_el.set("minheight", str(height))
        size_el.set("minwidth", str(width))
        size_el.set("sizing-mode", "fixed")

        zones = etree.SubElement(db, "zones")

        worksheet_options = {}

        # Check for C3 direct template (bypasses FlexNode for exact Tableau layout)
        print(f"[DASHBOARD] layout type={type(layout).__name__}, is_c3={isinstance(layout, dict) and layout.get('_c3_template', False)}")
        if isinstance(layout, dict) and layout.get("_c3_template"):
            build_c3_zones(
                zones,
                self._next_zone_id,
                title=layout.get("_title", dashboard_name),
                kpi_names=layout.get("_kpi_names", []),
                chart_names=layout.get("_chart_names", []),
                filters=layout.get("_filters"),
                filter_worksheet=layout.get("_filter_worksheet", ""),
                field_registry=self.field_registry,
                editor=self,
                rules=layout.get("_rules"),
            )
            # Build deps from all worksheet names (including filter metadata)
            all_ws = layout.get("_kpi_names", []) + layout.get("_chart_names", [])
            self._add_c3_dashboard_deps(
                db,
                all_ws,
                filters=layout.get("_filters"),
                filter_worksheet=layout.get("_filter_worksheet", ""),
            )
            # Set "Entire View" fit mode for ALL worksheets (KPIs + charts)
            # so they scale to fill their zone (not clipped or scrolled).
            for ws_name in layout.get("_kpi_names", []) + layout.get("_chart_names", []):
                worksheet_options[ws_name] = {"fit": "entire"}
        elif worksheet_names or isinstance(layout, dict) or isinstance(layout, str):
            layout_dict = resolve_dashboard_layout(layout, worksheet_names)
            validate_layout_worksheets(layout_dict)
            worksheet_options = extract_layout_options(layout_dict)
            render_dashboard_layout(
                zones,
                layout_dict,
                width,
                height,
                self._next_zone_id,
                field_registry=self.field_registry,
                parameters=self._parameters,
                editor=self,
            )
            self._add_dashboard_deps(db, layout_dict)

        db_simple_id = etree.SubElement(db, "simple-id")
        db_simple_id.set("uuid", _generate_uuid())

        self._add_window(
            dashboard_name,
            window_class="dashboard",
            worksheet_names=(worksheet_names or []),
            worksheet_options=worksheet_options,
        )
        return f"Created dashboard '{dashboard_name}'"

    def _next_zone_id(self) -> int:
        """Return the next monotonic dashboard zone id for layout generation."""
        self._zone_id_counter += 1
        return self._zone_id_counter

    def _add_dashboard_deps(self, db: etree._Element, layout_dict: dict) -> None:
        """Compatibility wrapper for dashboard dependency generation."""
        add_dashboard_dependencies(self, db, layout_dict)

    def _add_c3_dashboard_deps(
        self,
        db: etree._Element,
        worksheet_names: list[str],
        filters: list[dict] | None = None,
        filter_worksheet: str = "",
    ) -> None:
        """Add dashboard dependencies for C3 direct template.

        The dependency generator looks for ``type: "filter"`` children in the
        layout dict to decide whether ``<datasources>`` and
        ``<datasource-dependencies>`` blocks are needed.  The previous
        implementation only emitted ``type: "worksheet"`` entries, which meant
        dashboard filter zones had no backing datasource metadata and were
        invisible in Tableau.
        """
        children: list[dict] = [
            {"type": "worksheet", "name": n} for n in worksheet_names
        ]
        # Include filter nodes so the dependency generator adds the required
        # <datasources> and <datasource-dependencies> to the dashboard XML.
        for f in (filters or []):
            field_name = f.get("field") or f.get("column", "")
            children.append({
                "type": "filter",
                "field": field_name,
                "worksheet": filter_worksheet,
            })
        layout_dict = {
            "type": "container",
            "direction": "vertical",
            "children": children,
        }
        add_dashboard_dependencies(self, db, layout_dict)

    def add_dashboard_action(
        self,
        dashboard_name: str,
        action_type: str,
        source_sheet: str,
        target_sheet: str,
        fields: list[str],
        event_type: str = "on-select",
        caption: str = "",
    ) -> str:
        """Compatibility wrapper for dashboard action creation."""
        return _add_dashboard_action(
            self,
            dashboard_name,
            action_type,
            source_sheet,
            target_sheet,
            fields,
            event_type,
            caption,
        )
