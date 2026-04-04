"""Text chart builder.

Supports both standard Text marks and Measure Names/Values KPI-style text
layouts so the Text path is explicit in routing instead of being an implicit
helper branch.
"""

from __future__ import annotations

import logging
from typing import Optional, Union

from lxml import etree

from .builder_base import BaseChartBuilder

logger = logging.getLogger(__name__)


class TextChartBuilder(BaseChartBuilder):
    """Builder for Text marks, including measure-values KPI mode."""

    def __init__(
        self,
        editor,
        worksheet_name: str,
        columns: Optional[list[str]] = None,
        rows: Optional[list[str]] = None,
        color: Optional[str] = None,
        size: Optional[str] = None,
        label: Optional[str] = None,
        detail: Optional[str] = None,
        sort_descending: Optional[str] = None,
        tooltip: Optional[Union[str, list[str]]] = None,
        filters: Optional[list[dict]] = None,
        measure_values: Optional[list[str]] = None,
        label_runs: Optional[list[dict]] = None,
        label_param: Optional[str] = None,
        text_format: Optional[dict[str, str]] = None,
    ) -> None:
        """Capture text-table/KPI options, including measure-values configuration."""
        super().__init__(editor)
        self.worksheet_name = worksheet_name
        self.mark_type = "Text"
        self.columns = columns or []
        self.rows = rows or []
        self.color = color
        self.size = size
        self.label = label
        self.detail = detail
        self.sort_descending = sort_descending
        self.tooltip = tooltip
        self.filters = filters
        self.measure_values = measure_values or []
        self.label_runs = label_runs or []
        self.label_param = label_param
        self.text_format = text_format or {}

    def build(self) -> str:
        """Build text mark worksheet XML and optional measure-values overlays."""
        ws = self.editor._find_worksheet(self.worksheet_name)
        table = ws.find("table")
        if table is None:
            raise ValueError(f"Worksheet '{self.worksheet_name}' is malformed: missing <table>")
        view = table.find("view")
        if view is None:
            raise ValueError("Malformed structure: missing <view>")

        ds_name = self._datasource.get("name", "")
        # When label_param is set, the label field is not used as a datasource encoding
        label_for_exprs = None if self.label_param else self.label
        all_exprs = self._gather_expressions(
            self.columns,
            self.rows,
            self.color,
            self.size,
            label_for_exprs,
            self.detail,
            None,
            self.sort_descending,
            self.tooltip,
            self.filters,
            None,
            self.measure_values,
        )
        # Include label_runs field references so they get datasource-dependencies
        if self.label_runs:
            for run in self.label_runs:
                if "field" in run:
                    all_exprs.append(run["field"])
        instances = self._parse_and_prepare_instances(all_exprs, self.filters)
        self._setup_datasource_dependencies(view, ds_name, instances, all_exprs)

        pane = self._get_or_create_pane(table)
        self._setup_pane(
            pane,
            "Text",
            "Text",
            instances,
            self.color,
            self.size,
            label_for_exprs,
            self.detail,
            None,
            self.tooltip,
            False,
            None,
            None,
            ds_name,
        )

        # If label_param is set, add the parameter as the text encoding directly
        if self.label_param:
            param_info = self._parameters.get(self.label_param)
            if param_info:
                internal = param_info["internal_name"]  # e.g. "[Parameter 1]"
                # Add Parameters datasource to view's <datasources>
                datasources_el = view.find("datasources")
                if datasources_el is not None:
                    if not any(d.get("name") == "Parameters" for d in datasources_el.findall("datasource")):
                        # Get caption from the actual Parameters datasource
                        params_ds = self.editor.root.find(".//datasource[@name='Parameters']")
                        caption = params_ds.get("caption", "Parameters") if params_ds is not None else "Parameters"
                        param_ds_el = etree.SubElement(datasources_el, "datasource")
                        param_ds_el.set("caption", caption)
                        param_ds_el.set("name", "Parameters")
                # Add parameter datasource-dependencies to view
                self.editor._add_parameter_deps(view)
                # Add text encoding pointing to the parameter
                encodings_el = pane.find("encodings")
                if encodings_el is None:
                    encodings_el = etree.Element("encodings")
                    cl = pane.find("customized-label")
                    style_el = pane.find("style")
                    insert_before = cl or style_el
                    if insert_before is not None:
                        insert_before.addprevious(encodings_el)
                    else:
                        pane.append(encodings_el)
                text_enc = etree.SubElement(encodings_el, "text")
                text_enc.set("column", f"[Parameters].{internal}")

        # Rich-text label runs
        if self.label_runs:
            self._build_rich_label(pane, instances, self.label_runs)

        if self.measure_values:
            self.editor._apply_measure_values(
                view,
                table,
                pane,
                ds_name,
                instances,
                self.measure_values,
            )
        else:
            rows_el = table.find("rows")
            if rows_el is not None:
                rows_el.text = self.editor._build_dimension_shelf(instances, self.rows) if self.rows else None

            cols_el = table.find("cols")
            if cols_el is not None:
                cols_el.text = self.editor._build_dimension_shelf(instances, self.columns) if self.columns else None

            if self.sort_descending:
                self._add_shelf_sort(view, ds_name, instances, self.rows, self.sort_descending)

            self.editor._setup_table_style(table, "Text")

        if self.filters:
            self._add_filters(view, instances, self.filters)

        # Apply text formatting (e.g. percentage, currency) to KPI values.
        # Format is applied at FOUR levels for maximum Tableau compatibility:
        #   1. Main datasource column default-format (MOST RELIABLE — Tableau
        #      always respects this regardless of view-level style rules)
        #   2. Table-level cell style-rule (merged into existing rule)
        #   3. Pane-level cell style-rule (what Tableau Desktop writes)
        #   4. Column default-format in datasource-dependencies (view-local)
        if self.text_format:
            from .helpers import _get_or_create_table_style
            table_style = _get_or_create_table_style(table)

            # Find the existing table-level cell style-rule
            table_cell_rule = None
            for sr in table_style.findall("style-rule"):
                if sr.get("element") == "cell":
                    table_cell_rule = sr
                    break
            if table_cell_rule is None:
                table_cell_rule = etree.SubElement(table_style, "style-rule")
                table_cell_rule.set("element", "cell")

            # Find or create pane-level cell style-rule
            pane_style = pane.find("style")
            if pane_style is None:
                pane_style = etree.SubElement(pane, "style")
            pane_cell_rule = None
            for sr in pane_style.findall("style-rule"):
                if sr.get("element") == "cell":
                    pane_cell_rule = sr
                    break
            if pane_cell_rule is None:
                pane_cell_rule = etree.SubElement(pane_style, "style-rule")
                pane_cell_rule.set("element", "cell")

            # When using Measure Values, the display field is [Multiple Values]
            if self.measure_values:
                first_fmt = next(iter(self.text_format.values()))
                mv_ref = f"[{ds_name}].[Multiple Values]"
                for target_rule in (table_cell_rule, pane_cell_rule):
                    fmt = etree.SubElement(target_rule, "format")
                    fmt.set("attr", "text-format")
                    fmt.set("field", mv_ref)
                    fmt.set("value", first_fmt)
                logger.info("Applied text-format to [Multiple Values]: %s", first_fmt)

            for field_expr, fmt_str in self.text_format.items():
                ci = instances.get(field_expr)
                if not ci:
                    # Fallback: try partial match
                    for key, inst in instances.items():
                        if field_expr in key or key in field_expr:
                            ci = inst
                            break
                if ci:
                    full_ref = self.field_registry.resolve_full_reference(ci.instance_name)
                    # Apply at both table and pane level
                    for target_rule in (table_cell_rule, pane_cell_rule):
                        fmt = etree.SubElement(target_rule, "format")
                        fmt.set("attr", "text-format")
                        fmt.set("field", full_ref)
                        fmt.set("value", fmt_str)
                    # Set default-format on the column in datasource-dependencies
                    ds_deps = view.find(f"datasource-dependencies[@datasource='{ds_name}']")
                    if ds_deps is not None:
                        col_name = ci.column_local_name if ci.column_local_name.startswith("[") else f"[{ci.column_local_name}]"
                        for col_el in ds_deps.findall("column"):
                            if col_el.get("name") == col_name:
                                col_el.set("default-format", fmt_str)
                                break
                    # LEVEL 1 (most reliable): Set default-format on the MAIN
                    # datasource column definition.  Tableau reads this first and
                    # it applies everywhere the field appears in the workbook.
                    main_ds = self.editor._datasource
                    if main_ds is not None:
                        col_name_bare = ci.column_local_name.strip("[]")
                        for col_el in main_ds.findall(".//column"):
                            el_name = (col_el.get("caption") or col_el.get("name", "")).strip("[]")
                            if el_name == col_name_bare:
                                col_el.set("default-format", fmt_str)
                                logger.info("Set main datasource default-format: %s → %s", el_name, fmt_str)
                                break
                    logger.info("Applied text-format (4 levels): %s → %s", field_expr, fmt_str)
                else:
                    logger.warning(
                        "text_format: field '%s' not found in instances %s — format '%s' not applied",
                        field_expr, list(instances.keys()), fmt_str,
                    )

        return f"Configured worksheet '{self.worksheet_name}' as Text chart"
