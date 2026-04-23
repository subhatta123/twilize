"""TWB XML Editor — manipulate Tableau Workbook XML trees with lxml.

Core capabilities:
- Load and parse fields from a TWB template
- Add/remove calculated fields
- Add/configure worksheets (multiple chart types)
- Create dashboards with layout-flow zone structure
- Serialize and save TWB files
"""

from __future__ import annotations

import copy
import io
import logging
import re
import zipfile
from pathlib import Path
from typing import Optional

from lxml import etree

from .field_registry import ColumnInstance, FieldRegistry
from .config import _generate_uuid
from .charts import ChartsMixin
from .connections import ConnectionsMixin
from .dashboards import DashboardsMixin
from .parameters import ParametersMixin
from .reference_lines import ReferenceLinesMixin
from .trend_lines import TrendLineMixin
from .themes import ThemesMixin

logger = logging.getLogger(__name__)

_AGGREGATE_FUNCTION_RE = re.compile(
    r"\b(SUM|AVG|COUNT|COUNTD|MIN|MAX|MEDIAN|ATTR)\s*\(",
    re.IGNORECASE,
)


def _set_format(zone_style: etree._Element, attr: str, value: str) -> None:
    """Set/replace a <format attr="…" value="…"/> child on a zone-style.

    Used by ``apply_style_reference`` to rewrite background/border rules
    without duplicating existing entries.
    """
    for fmt in zone_style.findall("format"):
        if fmt.get("attr") == attr:
            fmt.set("value", value)
            return
    fmt = etree.SubElement(zone_style, "format")
    fmt.set("attr", attr)
    fmt.set("value", value)


def _upsert_style_rule_format(
    style_el: etree._Element,
    element_name: str,
    attr: str,
    value: str,
) -> None:
    """Upsert a <style-rule element=X><format attr=Y value=Z/></style-rule>.

    Creates the rule if missing; replaces a same-attr format if present.
    Shared by dashboard style and pane style rewrites in
    ``apply_style_reference``.
    """
    rule = None
    for sr in style_el.findall("style-rule"):
        if sr.get("element") == element_name:
            rule = sr
            break
    if rule is None:
        rule = etree.SubElement(style_el, "style-rule")
        rule.set("element", element_name)
    _set_format(rule, attr, value)


class TWBEditor(ParametersMixin, ConnectionsMixin, ChartsMixin, DashboardsMixin,
                ReferenceLinesMixin, TrendLineMixin, ThemesMixin):
    """lxml-based TWB XML editor."""

    def __init__(self, template_path: str | Path, clear_existing_content: bool = True):
        """Load a TWB/TWBX template and initialize editor-side registries."""
        template_path = self._resolve_template_path(template_path)

        if not template_path.exists():
            raise FileNotFoundError(f"Template file not found: {template_path}")

        # Parse with XMLParser to preserve original formatting
        parser = etree.XMLParser(remove_blank_text=False)

        # Track .twbx source so we can re-pack on save
        self._twbx_source: Path | None = None
        self._twbx_twb_name: str | None = None

        # Hyper files registered via set_hyper_connection, to auto-bundle
        # into Data/Extracts/ when save() writes a .twbx. Keyed by the
        # basename used in the connection's dbname attribute.
        self._hyper_files: dict[str, Path] = {}

        if template_path.suffix.lower() == ".twbx":
            self._twbx_source = template_path
            with zipfile.ZipFile(template_path) as zf:
                twb_names = [n for n in zf.namelist() if n.lower().endswith(".twb")]
                if not twb_names:
                    raise ValueError(f"No .twb file found inside {template_path}")
                self._twbx_twb_name = twb_names[0]
                twb_bytes = zf.read(self._twbx_twb_name)
            self.tree = etree.parse(io.BytesIO(twb_bytes), parser)
        else:
            self.tree = etree.parse(str(template_path), parser)

        self.root = self.tree.getroot()
        self.template_path = template_path
        self._sanitize_workbook_tree()

        # Parse datasource
        self._datasource = self._get_datasource()
        ds_name = self._datasource.get("name", "")
        self.field_registry = FieldRegistry(ds_name)

        # Zone ID counter (used by dashboards)
        self._zone_id_counter = 2

        # Parameter tracking (name -> {internal_name, datatype, domain_type})
        self._parameters: dict[str, dict] = {}

        # Initialize field registry corresponding to metadata
        self._init_fields()
        self._init_parameters()
        self._init_zone_id_counter()

        if clear_existing_content:
            # Clear out default worksheets/dashboards to avoid ghost fields
            self.clear_worksheets()
            self._init_zone_id_counter()

        # If using the default template, dynamically fix the excel connection filename
        if getattr(self, "_is_default_template", False):
            from .config import REFERENCES_DIR
            default_excel = REFERENCES_DIR / "Sample _ Superstore (Simple).xls"
            # Find the excel-direct connection and update its filename
            excel_conn = self._datasource.find(".//connection[@class='excel-direct']")
            if excel_conn is not None:
                # lxml paths should use forward slashes
                excel_conn.set("filename", str(default_excel.absolute()).replace("\\", "/"))

    @classmethod
    def open_existing(cls, file_path: str | Path) -> TWBEditor:
        """Open an existing workbook without clearing worksheets or dashboards."""

        return cls(file_path, clear_existing_content=False)

    # ================================================================
    # Initialization
    # ================================================================

    def _resolve_template_path(self, template_path: str | Path) -> Path:
        """Resolve user input to a template path and mark default-template usage."""
        if not template_path:
            from .config import REFERENCES_DIR

            self._is_default_template = True
            return REFERENCES_DIR / "empty_template.twb"

        self._is_default_template = False
        return Path(template_path)

    def _sanitize_workbook_tree(self) -> None:
        """Remove noisy top-level nodes that should never be persisted."""

        while True:
            thumbnails = self.root.find("thumbnails")
            if thumbnails is None:
                break
            self.root.remove(thumbnails)

        for tag in ("actions", "worksheets", "dashboards", "windows", "mapsources"):
            self._remove_empty_top_level_container(tag)

    def _remove_empty_top_level_container(self, tag: str) -> None:
        """Drop empty top-level containers that violate Tableau's schema."""

        while True:
            element = self.root.find(tag)
            if element is None:
                break
            if len(element):
                break
            if (element.text or "").strip():
                break
            self.root.remove(element)

    def _get_datasource(self) -> etree._Element:
        """Get the primary data datasource element.

        When a template contains multiple datasources (e.g. a 'Parameters'
        datasource alongside a real data connection), the 'Parameters' one has
        ``hasconnection='false'`` and should be skipped.  We iterate all
        and return the first datasource that actually holds data, so that
        FieldRegistry.datasource_name is set to the real federated/connection
        name and all column references resolve correctly.
        """
        datasources = self.root.find("datasources")
        if datasources is None:
            raise ValueError("No <datasources> found in template")

        all_ds = datasources.findall("datasource")
        if len(all_ds) == 0:
            raise ValueError("No <datasource> elements inside <datasources>")

        for ds in all_ds:
            if ds.get("hasconnection") == "false":
                continue
            return ds

        # Fallback: return the last one (single-datasource templates)
        return all_ds[-1]

    def _init_fields(self) -> None:
        """Parse field info from metadata-records and column definitions."""
        # 1. Parse metadata-records
        for mr in self._datasource.findall(".//metadata-records/metadata-record"):
            cls = mr.get("class", "")
            if cls != "column":
                continue
            remote_name_el = mr.find("remote-name")
            local_name_el = mr.find("local-name")
            local_type_el = mr.find("local-type")
            remote_type_el = mr.find("remote-type")

            if remote_name_el is None or local_name_el is None:
                continue

            remote_name = remote_name_el.text or ""
            local_name = local_name_el.text or ""
            local_type = (local_type_el.text or "string") if local_type_el is not None else "string"
            remote_type = (remote_type_el.text or "0") if remote_type_el is not None else "0"

            # Determine role/type from the remote integer type
            numeric_types = {"5", "4", "131", "20", "3", "2", "14", "6", "7"}
            if remote_type in numeric_types:
                role = "measure"
                field_type = "quantitative"
            else:
                role = "dimension"
                field_type = "nominal"

            self.field_registry.register(
                display_name=remote_name,
                local_name=local_name,
                datatype=local_type,
                role=role,
                field_type=field_type,
                is_calculated=False,
            )

        # 2. Also parse top-level <column> definitions for calculated fields
        for col in self._datasource.findall("column"):
            calc = col.find("calculation")
            if calc is not None:
                name = col.get("name", "")
                caption = col.get("caption", name.strip("[]"))
                datatype = col.get("datatype", "string")
                role = col.get("role", "dimension")
                field_type = col.get("type", "nominal")
                self.field_registry.register(
                    display_name=caption,
                    local_name=name,
                    datatype=datatype,
                    role=role,
                    field_type=field_type,
                    is_calculated=True,
                )
            else:
                # Register semantic-role columns (e.g. geographic columns)
                name = col.get("name", "")
                caption = col.get("caption", name.strip("[]"))
                if name and caption:
                    datatype = col.get("datatype", "string")
                    role = col.get("role", "dimension")
                    field_type = col.get("type", "nominal")
                    self.field_registry.register(
                        display_name=caption,
                        local_name=name,
                        datatype=datatype,
                        role=role,
                        field_type=field_type,
                        is_calculated=False,
                    )

    def _reinit_fields(self) -> None:
        """Clear the field registry and re-initialize it."""
        ds_name = self._datasource.get("name", "")
        self.field_registry = FieldRegistry(ds_name)
        self._init_fields()

    def _init_parameters(self) -> None:
        """Restore tracked parameters from the Parameters datasource."""

        self._parameters = {}

        datasources = self.root.find("datasources")
        if datasources is None:
            return

        params_ds = datasources.find("datasource[@name='Parameters']")
        if params_ds is None:
            return

        for col in params_ds.findall("column"):
            caption = col.get("caption")
            internal_name = col.get("name")
            if not caption or not internal_name:
                continue
            self._parameters[caption] = {
                "internal_name": internal_name,
                "datatype": col.get("datatype", "real"),
                "domain_type": col.get("param-domain-type", "range"),
            }

    def _init_zone_id_counter(self) -> None:
        """Resume dashboard zone ids after the highest existing zone id."""

        max_zone_id = 2
        for zone in self.root.findall(".//dashboard//zone[@id]"):
            zone_id = zone.get("id")
            if zone_id is None:
                continue
            try:
                max_zone_id = max(max_zone_id, int(zone_id))
            except ValueError:
                continue
        self._zone_id_counter = max_zone_id

    # ================================================================
    # Calculated Fields
    # ================================================================

    def add_calculated_field(
        self,
        field_name: str,
        formula: str,
        datatype: str = "real",
        role: Optional[str] = None,
        field_type: Optional[str] = None,
        table_calc: Optional[str] = None,
        default_format: str = "",
    ) -> str:
        """Add a calculated field to the datasource.

        Args:
            field_name: Display name, e.g. "Profit Ratio"
            formula: Tableau calculation formula, e.g. "SUM([Profit])/SUM([Sales])"
            datatype: Data type: real/string/integer/date/boolean
            role: Optional explicit Tableau role override (dimension/measure)
            field_type: Optional explicit Tableau field type override
            default_format: Optional Tableau number format string, e.g. 'c"$"#,##0,K'

        Returns:
            Confirmation message.
        """
        # Validate every function call in the formula against the official
        # Tableau function catalog (tableau_all_functions.json). Raises with a
        # close-match suggestion if an unknown function is used — e.g. CHR →
        # "Did you mean CHAR?". This blocks typos from producing a .twb with
        # red-"!" calc fields.
        from .formula_validator import assert_valid_formula
        assert_valid_formula(formula, field_name=field_name)

        inferred_role, inferred_field_type = self._infer_calculated_field_semantics(
            formula,
            datatype,
        )
        role = role or inferred_role
        field_type = field_type or inferred_field_type

        # Resolve field and parameter references in formula
        resolved_formula = formula

        # First, resolve [ParamName] bracketed parameter references
        for param_name, param_info in self._parameters.items():
            internal = param_info["internal_name"]  # e.g. "[Parameter 1]"
            replacement = f"[Parameters].{internal}"
            # Safely replace [ParamName] or [Parameters].[ParamName]
            pattern = rf"(?:\[Parameters\]\.)?\[{re.escape(param_name)}\]"
            resolved_formula = re.sub(pattern, replacement, resolved_formula)

        # Then resolve [FieldName] references → [local_name]
        # Re-scan after parameter resolution
        temp_formula = resolved_formula
        for match in re.finditer(r'\[([^\]]+)\]', temp_formula):
            ref_name = match.group(1)
            # Skip already-resolved parameter references
            if ref_name == "Parameters" or ref_name.startswith("Parameter "):
                continue
            # Try to find the field in registry
            try:
                fi = self.field_registry._find_field(ref_name)
                local = fi.local_name  # e.g. "[Profit (Orders)]"
                if local.startswith("[") and local.endswith("]"):
                    resolved_formula = resolved_formula.replace(f"[{ref_name}]", local)
            except (KeyError, ValueError) as e:
                logger.debug("Field '%s' not found in registry during formula resolution, keeping original reference: %s", ref_name, e)

        # Create <column> element — must be inserted before <layout>
        # Tableau XSD requires column before layout/style/semantic-values
        col = etree.Element("column")
        col.set("caption", field_name)
        col.set("datatype", datatype)
        internal_name = f"[Calculation_{_generate_uuid().strip('{}').replace('-','')}]"
        col.set("name", internal_name)
        col.set("role", role)
        col.set("type", field_type)
        if default_format:
            col.set("default-format", default_format)

        calc = etree.SubElement(col, "calculation")
        calc.set("class", "tableau")
        calc.set("formula", resolved_formula)
        if table_calc:
            tc = etree.SubElement(calc, "table-calc")
            tc.set("ordering-type", table_calc)

        # Insert before <layout> if present
        layout_el = self._datasource.find("layout")
        if layout_el is not None:
            layout_el.addprevious(col)
        else:
            # Before semantic-values
            sv = self._datasource.find("semantic-values")
            if sv is not None:
                sv.addprevious(col)
            else:
                self._datasource.append(col)

        # Register in field registry. is_aggregate flags calcs whose formula
        # embeds SUM/AVG/COUNT/... so FieldRegistry.parse_expression emits
        # derivation="User" (user-defined aggregate) when the field is placed
        # on a shelf, even if role="dimension" (e.g. string KPI label calcs).
        is_aggregate = bool(_AGGREGATE_FUNCTION_RE.search(resolved_formula))
        self.field_registry.register(
            display_name=field_name,
            local_name=internal_name,
            datatype=datatype,
            role=role,
            field_type=field_type,
            is_calculated=True,
            is_aggregate=is_aggregate,
        )

        return f"Added calculated field '{field_name}' = {formula}"

    def _infer_calculated_field_semantics(self, formula: str, datatype: str) -> tuple[str, str]:
        """Infer Tableau role/type for a calculated field."""

        if datatype in ("real", "integer"):
            return "measure", "quantitative"

        if datatype == "boolean":
            return "measure", "nominal"

        if datatype == "date":
            return "dimension", "ordinal"

        if _AGGREGATE_FUNCTION_RE.search(formula):
            return "measure", "nominal"

        return "dimension", "nominal"

    def apply_style_reference(
        self,
        image_path: Optional[str] = None,
        css: Optional[str] = None,
        html: Optional[str] = None,
        apply_to: Optional[list[str]] = None,
    ) -> dict:
        """Re-skin existing dashboards from a reference image and/or CSS.

        Extracts palette + typography + border styling via the pure
        ``style_reference`` module, then mutates the current workbook in
        place — dashboard `<style>` backgrounds, every zone's `<zone-style>`
        background / border format rules, and pane-level mark text colours
        for worksheets inside the targeted dashboards.

        Args:
            image_path: Path to a reference image (PNG/JPG). Optional.
            css: Raw CSS string. Optional.
            html: HTML fragment containing one or more ``<style>`` blocks.
                Optional.
            apply_to: Dashboard names to re-style. ``None`` applies to all
                dashboards in the workbook.

        Returns:
            The extracted StyleReference dict, for inspection.
        """
        from .style_reference import extract_style_reference

        style = extract_style_reference(image_path=image_path, css=css, html=html)
        self._apply_style_reference_to_workbook(style, apply_to)
        return style

    def _apply_style_reference_to_workbook(
        self,
        style: dict,
        apply_to: Optional[list[str]],
    ) -> None:
        """Apply an already-extracted StyleReference dict onto workbook XML.

        In addition to the palette and dashboard/zone backgrounds applied by
        the original implementation, this also:

        * registers the image's categorical palette as a named
          ``<color-palette>`` in ``<preferences>`` so the user can pick it
          anywhere in Tableau Desktop,
        * rotates ``mark-color`` across non-KPI worksheets using that
          categorical palette (first chart → palette[0], second → palette[1],
          …), giving a categorical feel without requiring a Color shelf,
        * applies zone ``margin`` / ``border-width`` / ``border-style`` from
          the image's detected card style + layout density,
        * sets a font-family fallback across KPI worksheets so the text
          treatment follows the reference's sans-serif / serif hint.
        """
        colors = style.get("colors", {}) or {}
        typography = style.get("typography", {}) or {}
        borders = style.get("borders", {}) or {}
        card_style = style.get("card_style", {}) or {}
        layout_style = style.get("layout_style", {}) or {}
        chart_palette: list[str] = list(style.get("chart_palette") or [])

        bg = colors.get("background")
        card_bg = colors.get("card_background") or bg
        text_primary = colors.get("text_primary")
        # Borders: prefer the CSS-declared values, else the image's measured
        # card-edge values, else the palette's mid-luminance role slot.
        border_color = (
            borders.get("color")
            or card_style.get("border_color")
            or colors.get("border")
        )
        border_width = borders.get("width")
        if border_width is None and card_style.get("border_width") is not None:
            border_width = card_style["border_width"]
        # Zone margin scales with detected whitespace: dense designs compress,
        # spacious designs breathe.  None → leave template default.
        zone_margin = layout_style.get("zone_margin")

        dashboards_el = self.root.find("dashboards")
        if dashboards_el is None:
            return

        target_set: Optional[set[str]] = set(apply_to) if apply_to else None
        touched_worksheets: set[str] = set()

        for db in dashboards_el.findall("dashboard"):
            if target_set is not None and db.get("name") not in target_set:
                continue

            # Dashboard <style><style-rule element="table"> background.
            if bg:
                db_style = db.find("style")
                if db_style is None:
                    db_style = etree.SubElement(db, "style")
                    # style must be early in the dashboard element order;
                    # place it before <size> if present.
                    size_el = db.find("size")
                    if size_el is not None:
                        db.remove(db_style)
                        size_el.addprevious(db_style)
                _upsert_style_rule_format(db_style, "table", "background-color", bg)

            # Walk every zone in this dashboard and rewrite zone-style formats.
            for zone in db.iter("zone"):
                zs = zone.find("zone-style")
                if zs is None:
                    continue
                ztype = zone.get("type-v2")
                # Containers get the page background; inner zones (cards,
                # worksheets, filters) get the card background.
                z_bg = bg if ztype == "layout-flow" else card_bg
                if z_bg:
                    _set_format(zs, "background-color", z_bg)
                if border_color:
                    _set_format(zs, "border-color", border_color)
                if border_width is not None:
                    _set_format(zs, "border-width", str(int(border_width)))
                    # Mirror Tableau Desktop's "solid/none" enum — a >0 width
                    # with border-style="none" renders as no border at all.
                    bstyle = "solid" if int(border_width) > 0 else "none"
                    _set_format(zs, "border-style", bstyle)

                # Card spacing — only touch *card* zones (worksheets + filters
                # + text), never layout-flow containers.  Setting margins on
                # containers squeezes the whole chart area against the
                # dashboard edges.
                if zone_margin is not None and ztype != "layout-flow":
                    # Filter zones need a minimum margin (6 px) so the
                    # dropdown is visibly separated from the strip and
                    # stays clickable even under a "dense" reference image
                    # that otherwise collapses every card margin to ~2 px.
                    if ztype == "filter":
                        eff_margin = max(int(zone_margin), 6)
                    else:
                        eff_margin = int(zone_margin)
                    _set_format(zs, "margin", str(eff_margin))

                # Collect worksheet names referenced from this dashboard so we
                # can restyle their pane text.
                if zone.get("name") and not ztype:
                    touched_worksheets.add(zone.get("name"))

        # Pane-level mark-label typography is intentionally NOT re-skinned:
        # Tableau's schema rejects <style-rule element="mark-labels"> and does
        # not accept mark-labels-color / mark-labels-font-family /
        # mark-labels-font-size as <format attr=...> on <style-rule element="mark">
        # either. Valid mark-labels-* attrs are show/cull/mode/range-min/range-max;
        # label typography lives in worksheet-level formatted-text blocks.
        # (D2E8DA72 load errors confirmed this in prior attempts.)
        _ = typography

        # ── Worksheet-interior restyle ─────────────────────────────────
        # Zone backgrounds only color the frame around each worksheet; the
        # worksheet's own canvas / cell / header / mark colors are set by
        # <style-rule element="worksheet"|"cell"|"header"|"mark"> inside
        # <worksheet><table><style>. Re-skin those too so KPI cards actually
        # look coloured (not just their zone frames) and so bar/line marks
        # take the reference-image accent color instead of default blue.
        accent = colors.get("accent") or colors.get("text_secondary")

        # Rotate the categorical palette across non-KPI worksheets so that
        # e.g. four bar charts in the same dashboard don't all look alike.
        # Callers can inspect the assignment via the manifest's
        # ``chart_mark_color_assignments`` key (set by the pipeline).
        if chart_palette:
            non_kpi_worksheets: list[str] = []
            for ws_name in touched_worksheets:
                try:
                    ws = self._find_worksheet(ws_name)
                except ValueError:
                    continue
                table = ws.find("table")
                if table is None:
                    continue
                is_kpi_ws = any(
                    (p.find("mark") is not None
                     and p.find("mark").get("class") == "Text")
                    for p in table.iter("pane")
                )
                if not is_kpi_ws:
                    non_kpi_worksheets.append(ws_name)
            non_kpi_worksheets.sort()  # stable ordering for reproducibility
            chart_color_assignments = {
                name: chart_palette[i % len(chart_palette)]
                for i, name in enumerate(non_kpi_worksheets)
            }
        else:
            chart_color_assignments = {}

        # Register the categorical chart palette as a named custom palette
        # in <preferences> so the user can also pick it from Tableau's color
        # editor anywhere in the workbook.
        if chart_palette and hasattr(self, "apply_color_palette"):
            try:
                self.apply_color_palette(
                    colors=chart_palette,
                    custom_name="reference-image",
                )
            except Exception as exc:  # noqa: BLE001 — best-effort registration
                logger.warning(
                    "Failed to register reference-image palette: %s", exc,
                )

        for ws_name in touched_worksheets:
            try:
                ws = self._find_worksheet(ws_name)
            except ValueError:
                continue
            table = ws.find("table")
            if table is None:
                continue

            # Detect whether this worksheet is a KPI (Text mark) — the card
            # background should cover the whole worksheet. For analytical
            # charts, the card background would wash out the axes, so we
            # only touch the mark color for those.
            is_kpi_ws = False
            for pane in table.iter("pane"):
                mark = pane.find("mark")
                if mark is not None and mark.get("class") == "Text":
                    is_kpi_ws = True
                    break

            # 1) Worksheet canvas background (KPI cards only).
            if is_kpi_ws and card_bg:
                table_style = table.find("style")
                if table_style is None:
                    table_style = etree.SubElement(table, "style")
                    # <style> belongs before <panes>.
                    panes_el = table.find("panes")
                    if panes_el is not None:
                        table.remove(table_style)
                        panes_el.addprevious(table_style)
                _upsert_style_rule_format(
                    table_style, "worksheet", "background-color", card_bg,
                )
                # Also set cell + header backgrounds to match — otherwise a
                # thin white band can appear inside the card.
                _upsert_style_rule_format(
                    table_style, "cell", "background-color", card_bg,
                )
                _upsert_style_rule_format(
                    table_style, "header", "background-color", card_bg,
                )
                # KPI text primary color from the palette, for readability.
                if text_primary:
                    _upsert_style_rule_format(
                        table_style, "cell", "color", text_primary,
                    )

            # 2) Mark color inside every pane (bar/line/scatter charts).
            #    Skip KPI worksheets — their "text" marks are calc-driven.
            #    Use the rotated chart-palette assignment if available so
            #    sibling charts in the same dashboard get distinct hues.
            if not is_kpi_ws:
                ws_mark_color = (
                    chart_color_assignments.get(ws_name) or accent
                )
                if ws_mark_color:
                    for pane in table.iter("pane"):
                        pane_style = pane.find("style")
                        if pane_style is None:
                            pane_style = etree.SubElement(pane, "style")
                        rule = None
                        for sr in pane_style.findall("style-rule"):
                            if sr.get("element") == "mark":
                                rule = sr
                                break
                        if rule is None:
                            rule = etree.SubElement(pane_style, "style-rule")
                            rule.set("element", "mark")
                        # Replace or add the mark-color format entry.
                        existing = None
                        for fmt in rule.findall("format"):
                            if fmt.get("attr") == "mark-color":
                                existing = fmt
                                break
                        if existing is not None:
                            existing.set("value", ws_mark_color)
                        else:
                            etree.SubElement(
                                rule, "format",
                                {"attr": "mark-color", "value": ws_mark_color},
                            )

        # Expose the per-chart color assignments on the style dict so the
        # pipeline can surface them in the manifest (callers read this via
        # the return value of apply_style_reference).
        style["chart_mark_color_assignments"] = chart_color_assignments

    def remove_calculated_field(self, field_name: str) -> str:
        """Remove a calculated field."""
        try:
            fi = self.field_registry._find_field(field_name)
        except KeyError:
            return f"Calculated field '{field_name}' does not exist"
        col = self._datasource.find(f"column[@name='{fi.local_name}']")
        if col is not None:
            self._datasource.remove(col)
        self.field_registry.remove(field_name)
        return f"Removed calculated field '{field_name}'"

    # ================================================================
    # Worksheets
    # ================================================================

    def clear_worksheets(self) -> None:
        """Clear all worksheets and dashboards from the template."""
        worksheets = self.root.find("worksheets")
        if worksheets is not None:
            for ws in list(worksheets):
                worksheets.remove(ws)

        dashboards = self.root.find("dashboards")
        if dashboards is not None:
            for db in list(dashboards):
                dashboards.remove(db)

        windows = self.root.find("windows")
        if windows is not None:
            for win in list(windows):
                windows.remove(win)

        # Clear model-level columns references
        for mc in self.root.findall(".//model-columns"):
            for c in list(mc):
                mc.remove(c)

        # Clean up mapsources that reference removed worksheets
        root_ms = self.root.find("mapsources")
        if root_ms is not None:
            self.root.remove(root_ms)

    def add_worksheet(self, worksheet_name: str) -> str:
        """Add a new blank worksheet."""
        ds_name = self._datasource.get("name", "")

        worksheets = self.root.find("worksheets")
        if worksheets is None:
            worksheets = etree.Element("worksheets")
            insert_before = None
            for tag in ("dashboards", "windows", "external"):
                insert_before = self.root.find(tag)
                if insert_before is not None:
                    break
            if insert_before is not None:
                insert_before.addprevious(worksheets)
            else:
                self.root.append(worksheets)

        ws = etree.SubElement(worksheets, "worksheet")
        ws.set("name", worksheet_name)

        table = etree.SubElement(ws, "table")

        # Add view with datasource reference
        view = etree.SubElement(table, "view")
        view_ds = etree.SubElement(view, "datasources")
        ds_ref = etree.SubElement(view_ds, "datasource")
        caption = self._datasource.get("caption", ds_name)
        ds_ref.set("caption", caption)
        ds_ref.set("name", ds_name)

        # Add aggregation default
        agg = etree.SubElement(view, "aggregation")
        agg.set("value", "true")

        # Add style
        style = etree.SubElement(table, "style")

        # Add panes with pane and mark
        panes = etree.SubElement(table, "panes")
        pane = etree.SubElement(panes, "pane")
        
        # pane MUST have a <view> before <mark> according to Tableau XSD
        pane_view = etree.SubElement(pane, "view")
        breakdown = etree.SubElement(pane_view, "breakdown")
        breakdown.set("value", "auto")
        
        mark = etree.SubElement(pane, "mark")
        mark.set("class", "Automatic")

        # Set rows/cols
        rows = etree.SubElement(table, "rows")
        cols = etree.SubElement(table, "cols")

        # Add simple-id at the end of the worksheet
        simple_id = etree.SubElement(ws, "simple-id")
        simple_id.set("uuid", _generate_uuid())

        # Add window entry
        self._add_window(worksheet_name, "worksheet")

        return f"Added worksheet '{worksheet_name}'"

    def _add_window(
        self,
        name: str,
        window_class: str = "worksheet",
        worksheet_names: Optional[list[str]] = None,
        worksheet_options: Optional[dict[str, dict]] = None,
    ) -> None:
        """Add a window entry in <windows>.

        Worksheet windows use <cards> structure.
        Dashboard windows use <viewpoints> + <active> structure (per c.2 (2) reference).

        If a window with the same name already exists it is removed first
        to prevent the XSD "duplicate identity constraint" error (D2E8DA72).
        """
        windows = self.root.find("windows")
        if windows is None:
            windows = etree.SubElement(self.root, "windows")

        # Remove any pre-existing window with the same name. Tableau's XSD
        # scopes <windows> identity by name alone (error D2E8DA72 otherwise),
        # so add_dashboard / add_worksheet guard against cross-class name
        # collisions up front — this dedup only fires for same-class re-adds.
        for existing in windows.findall("window"):
            if existing.get("name") == name:
                windows.remove(existing)

        win = etree.SubElement(windows, "window")
        win.set("class", window_class)
        win.set("name", name)

        if window_class == "worksheet":
            cards = etree.SubElement(win, "cards")
            
            # Left edge (pages, filters, marks)
            edge_left = etree.SubElement(cards, "edge")
            edge_left.set("name", "left")
            strip_left = etree.SubElement(edge_left, "strip", size="160")
            etree.SubElement(strip_left, "card", type="pages")
            etree.SubElement(strip_left, "card", type="filters")
            etree.SubElement(strip_left, "card", type="marks")
            
            # Top edge (columns, rows, title)
            edge_top = etree.SubElement(cards, "edge")
            edge_top.set("name", "top")
            for t in ["columns", "rows", "title"]:
                strip_top = etree.SubElement(edge_top, "strip", size="2147483647")
                etree.SubElement(strip_top, "card", type=t)
                
            # Right edge (will be populated by chart encodings with legends later)
            edge_right = etree.SubElement(cards, "edge")
            edge_right.set("name", "right")
            
            # Bottom edge
            edge_bottom = etree.SubElement(cards, "edge")
            edge_bottom.set("name", "bottom")
        elif window_class == "dashboard":
            # For dashboards: add viewpoints per worksheet + active marker
            if worksheet_names:
                viewpoints = etree.SubElement(win, "viewpoints")
                for vp_name in worksheet_names:
                    viewpoint = etree.SubElement(viewpoints, "viewpoint")
                    viewpoint.set("name", vp_name)
                    if worksheet_options and worksheet_options.get(vp_name, {}).get("fit") in ("entire", "entire-view"):
                        zoom = etree.SubElement(viewpoint, "zoom")
                        zoom.set("type", "entire-view")
                active = etree.SubElement(win, "active")
                active.set("id", "-1")

        # Add simple-id (must be at the end according to schema)
        simple_id = etree.SubElement(win, "simple-id")
        simple_id.set("uuid", _generate_uuid())

    def _deduplicate_windows(self) -> None:
        """Remove duplicate ``<window>`` entries from ``<windows>``.

        Keeps the LAST window for each name (the most recently added one).
        This is a safety net called before save() to guarantee the XSD
        unique-identity constraint is never violated.
        """
        windows = self.root.find("windows")
        if windows is None:
            return
        seen: dict[str, etree._Element] = {}
        for win in list(windows):
            name = win.get("name")
            if name in seen:
                # Remove the earlier duplicate, keep the newer one
                windows.remove(seen[name])
            seen[name] = win

    def _find_worksheet(self, name: str) -> etree._Element:
        """Find a worksheet element by name."""
        for ws in self.root.findall(".//worksheets/worksheet"):
            if ws.get("name") == name:
                return ws
        raise ValueError(f"Worksheet '{name}' not found")

    def list_worksheets(self) -> list[str]:
        """List worksheet names in workbook order."""

        worksheets = self.root.find("worksheets")
        if worksheets is None:
            return []
        return [
            ws.get("name", "")
            for ws in worksheets.findall("worksheet")
            if ws.get("name")
        ]

    def list_dashboards(self) -> list[dict]:
        """List dashboards with the worksheets + filter zones they reference.

        Each entry includes:
          - ``name``        : dashboard name
          - ``worksheets``  : chart worksheets placed on the dashboard
          - ``filters``     : list of dicts
                {"field": param, "scope": apply-to-worksheets,
                 "mode": dropdown/single/multi, "height_px": int, "width_units": int}
            so downstream consumers can verify filters are present,
            clickable-sized, and scoped correctly.
        """

        dashboards = self.root.find("dashboards")
        if dashboards is None:
            return []

        def _zone_px_height(zone) -> int:
            """Best-effort pixel height of a zone based on the dashboard canvas."""
            try:
                return int(round(int(zone.get("h", "0")) / 100.0))
            except (TypeError, ValueError):
                return 0

        dashboard_summaries: list[dict] = []
        for dashboard in dashboards.findall("dashboard"):
            worksheet_names: list[str] = []
            filter_info: list[dict] = []
            zones = dashboard.find("zones")
            if zones is not None:
                for zone in zones.findall(".//zone"):
                    ztype = zone.get("type-v2")
                    if ztype == "filter":
                        filter_info.append({
                            "field": zone.get("param") or "",
                            "worksheet": zone.get("name") or "",
                            "scope": zone.get("apply-to-worksheets") or "selected",
                            "mode": zone.get("mode") or "",
                            "height_units": int(zone.get("h", "0") or 0),
                            "width_units": int(zone.get("w", "0") or 0),
                            "height_px_est": _zone_px_height(zone),
                        })
                        continue
                    name = zone.get("name")
                    if name and name not in worksheet_names:
                        worksheet_names.append(name)
            dashboard_summaries.append(
                {
                    "name": dashboard.get("name", ""),
                    "worksheets": worksheet_names,
                    "filters": filter_info,
                }
            )
        return dashboard_summaries

    # ================================================================
    # Output
    # ================================================================

    def list_fields(self) -> str:
        """List all fields in the datasource."""
        lines = []
        lines.append("=== Dimensions ===")
        for fi in sorted(self.field_registry._fields.values(),
                        key=lambda f: f.display_name):
            if fi.role == "dimension":
                calc_tag = " [calculated]" if fi.is_calculated else ""
                lines.append(f"  {fi.display_name} ({fi.datatype}){calc_tag}")

        lines.append("\n=== Measures ===")
        for fi in sorted(self.field_registry._fields.values(),
                        key=lambda f: f.display_name):
            if fi.role == "measure":
                calc_tag = " [calculated]" if fi.is_calculated else ""
                lines.append(f"  {fi.display_name} ({fi.datatype}){calc_tag}")

        return "\n".join(lines)

    def validate_schema(self) -> "SchemaValidationResult":
        """Validate the current workbook against the official Tableau TWB XSD schema.

        This check is non-destructive and does not require saving first.
        XSD errors are reported as informational — Tableau itself occasionally
        generates workbooks that deviate from the schema.

        Returns:
            SchemaValidationResult with validity flag, error list, and a
            human-readable .to_text() summary.
        """
        from .validator import SchemaValidationResult, validate_against_schema
        return validate_against_schema(self.root)

    def save(
        self,
        output_path: str | Path,
        validate: bool = True,
        extra_files: list[str | Path] | None = None,
    ) -> str:
        """Save the workbook as a .twb or .twbx file.

        Args:
            output_path: Destination path. Use .twbx extension to produce a
                packaged workbook (ZIP containing the .twb XML plus any data
                extracts / images bundled from the source .twbx, if one was
                opened). Use .twb for a plain XML workbook.
            validate: If True (default), run structural validation before saving.
                      Raises TWBValidationError if the structure is broken.
            extra_files: Additional files (e.g. .hyper extracts) to bundle
                into the .twbx archive under ``Data/Extracts/``.

        Returns:
            Confirmation message.

        Raises:
            TWBValidationError: If validate=True and the TWB structure is broken.
        """
        self._deduplicate_windows()
        self._sanitize_workbook_tree()

        if validate:
            from .validator import validate_twb
            validate_twb(self.root)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if output_path.suffix.lower() == ".twbx":
            # Collect all hyper files that need to be bundled:
            # explicit extra_files (from pipelines) + files registered via
            # set_hyper_connection (from MCP/SDK calls). Deduplicate by
            # basename so the same file isn't written twice.
            bundle_files: dict[str, Path] = {}
            if getattr(self, "_hyper_files", None):
                for name, fpath in self._hyper_files.items():
                    if fpath.exists():
                        bundle_files[name] = fpath
            if extra_files:
                for fpath in extra_files:
                    fp = Path(fpath)
                    if fp.exists():
                        bundle_files[fp.name] = fp

            # Rewrite each bundled hyper's dbname in the XML to the relative
            # archive path so Tableau finds the file after unzipping the .twbx.
            if bundle_files:
                bundled_basenames = set(bundle_files)
                for conn_el in self._datasource.findall(".//connection[@class='hyper']"):
                    current = conn_el.get("dbname", "")
                    basename = Path(current).name
                    if basename in bundled_basenames:
                        conn_el.set("dbname", f"Data/Extracts/{basename}")

            # Serialize the XML into memory (after dbname rewrite)
            buf = io.BytesIO()
            self.tree.write(buf, xml_declaration=True, encoding="utf-8", pretty_print=False)
            twb_bytes = buf.getvalue()

            # Name for the .twb entry inside the ZIP
            inner_twb_name = self._twbx_twb_name or output_path.with_suffix(".twb").name

            with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zout:
                # Write the updated workbook XML
                zout.writestr(inner_twb_name, twb_bytes)
                # Copy bundled extracts / images from the source .twbx if available.
                # Skip any Data/Extracts/<name> entries that we're about to overwrite
                # with a newer bundled file — otherwise the stale extract wins.
                if self._twbx_source and self._twbx_source.exists():
                    with zipfile.ZipFile(self._twbx_source) as zsrc:
                        for info in zsrc.infolist():
                            if info.filename == self._twbx_twb_name:
                                continue
                            if info.filename in {f"Data/Extracts/{n}" for n in bundle_files}:
                                continue
                            zout.writestr(info, zsrc.read(info.filename))
                # Bundle collected hyper files
                for name, fpath in bundle_files.items():
                    zout.write(fpath, f"Data/Extracts/{name}")
        else:
            self.tree.write(
                str(output_path),
                xml_declaration=True,
                encoding="utf-8",
                pretty_print=False,
            )

        return f"Saved workbook to {output_path}"

