"""Reference lines and bands for Tableau worksheets.

Writes <reference-line> XML elements inside the worksheet's <table>
element. Supports constant value lines, aggregate formula lines
(average, median, etc.), and bands (fill-above/fill-below pairs).
"""

from __future__ import annotations

from lxml import etree


class ReferenceLinesMixin:
    """Mixin providing reference line and band capabilities to TWBEditor."""

    def add_reference_line(
        self,
        worksheet_name: str,
        axis_field: str,
        value: str | float | None = None,
        formula: str = "constant",
        scope: str = "per-pane",
        label_type: str = "automatic",
        label: str = "",
        line_color: str = "",
        line_style: str = "",
    ) -> str:
        """Add a reference line to a worksheet.

        Args:
            worksheet_name: Target worksheet name.
            axis_field: Expression for the axis column (e.g. "SUM(Sales)").
            value: Constant value (required when formula="constant").
            formula: "constant", "average", "median", "min", "max", "sum", "total".
            scope: "per-cell", "per-pane", or "per-table".
            label_type: "none", "automatic", "value", "computation", "custom".
            label: Custom label text (when label_type="custom").
            line_color: Hex color for the line (e.g. "#FF0000").
            line_style: CSS-style line pattern.

        Returns:
            Confirmation message.
        """
        ws = self._find_worksheet(worksheet_name)
        table = ws.find("table")
        if table is None:
            raise ValueError(f"Worksheet '{worksheet_name}' has no <table> element")

        ci = self.field_registry.parse_expression(axis_field)
        axis_ref = self.field_registry.resolve_full_reference(ci.instance_name)

        ref_line = etree.Element("reference-line")
        ref_line.set("axis-column", axis_ref)
        ref_line.set("enable-instant-analytics", "true")
        ref_line.set("formula", formula)
        ref_line.set("scope", scope)
        ref_line.set("label-type", label_type)

        if formula == "constant" and value is not None:
            ref_line.set("value", str(value))
        if label and label_type == "custom":
            ref_line.set("label", label)

        # Style attributes
        style = etree.SubElement(ref_line, "style")
        style_rule = etree.SubElement(style, "style-rule")
        style_rule.set("element", "reference-line")
        if line_color:
            fmt = etree.SubElement(style_rule, "format")
            fmt.set("attr", "line-color")
            fmt.set("value", line_color)
        if line_style:
            fmt = etree.SubElement(style_rule, "format")
            fmt.set("attr", "line-pattern")
            fmt.set("value", line_style)

        # Insert reference-line inside <table>, after <panes>
        panes = table.find("panes")
        if panes is not None:
            panes.addnext(ref_line)
        else:
            table.append(ref_line)

        return f"Added {formula} reference line on '{axis_field}' in '{worksheet_name}'"

    def add_reference_band(
        self,
        worksheet_name: str,
        axis_field: str,
        from_value: str | float | None = None,
        to_value: str | float | None = None,
        from_formula: str = "constant",
        to_formula: str = "constant",
        scope: str = "per-pane",
        fill_color: str = "#E0E0E0",
    ) -> str:
        """Add a reference band (shaded region) to a worksheet.

        Args:
            worksheet_name: Target worksheet name.
            axis_field: Expression for the axis column.
            from_value: Lower bound value (when from_formula="constant").
            to_value: Upper bound value (when to_formula="constant").
            from_formula: Formula for lower bound.
            to_formula: Formula for upper bound.
            scope: "per-cell", "per-pane", or "per-table".
            fill_color: Hex color for the band fill.

        Returns:
            Confirmation message.
        """
        ws = self._find_worksheet(worksheet_name)
        table = ws.find("table")
        if table is None:
            raise ValueError(f"Worksheet '{worksheet_name}' has no <table> element")

        ci = self.field_registry.parse_expression(axis_field)
        axis_ref = self.field_registry.resolve_full_reference(ci.instance_name)

        # Create the "from" reference line
        ref_from = etree.Element("reference-line")
        ref_from.set("axis-column", axis_ref)
        ref_from.set("enable-instant-analytics", "true")
        ref_from.set("formula", from_formula)
        ref_from.set("scope", scope)
        ref_from.set("fill-above", "true")
        ref_from.set("label-type", "none")
        if from_formula == "constant" and from_value is not None:
            ref_from.set("value", str(from_value))

        # Create the "to" reference line
        ref_to = etree.Element("reference-line")
        ref_to.set("axis-column", axis_ref)
        ref_to.set("enable-instant-analytics", "true")
        ref_to.set("formula", to_formula)
        ref_to.set("scope", scope)
        ref_to.set("fill-below", "true")
        ref_to.set("label-type", "none")
        if to_formula == "constant" and to_value is not None:
            ref_to.set("value", str(to_value))

        # Style with fill color
        for ref_line in (ref_from, ref_to):
            style = etree.SubElement(ref_line, "style")
            style_rule = etree.SubElement(style, "style-rule")
            style_rule.set("element", "reference-line")
            fmt = etree.SubElement(style_rule, "format")
            fmt.set("attr", "fill-color")
            fmt.set("value", fill_color)

        panes = table.find("panes")
        if panes is not None:
            panes.addnext(ref_from)
            ref_from.addnext(ref_to)
        else:
            table.append(ref_from)
            table.append(ref_to)

        return f"Added reference band on '{axis_field}' in '{worksheet_name}'"
