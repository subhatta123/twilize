"""Color palettes and dashboard themes for Tableau workbooks.

Provides custom color palettes (written to <preferences>) and
dashboard-level theme application (uniform background, font).
"""

from __future__ import annotations

from lxml import etree

# Built-in named palettes
NAMED_PALETTES = {
    "tableau10": [
        "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
        "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC",
    ],
    "tableau20": [
        "#4E79A7", "#A0CBE8", "#F28E2B", "#FFBE7D", "#59A14F",
        "#8CD17D", "#B6992D", "#F1CE63", "#499894", "#86BCB6",
        "#E15759", "#FF9D9A", "#79706E", "#BAB0AC", "#D37295",
        "#FABFD2", "#B07AA1", "#D4A6C8", "#9D7660", "#D7B5A6",
    ],
    "blue-red": [
        "#2166AC", "#4393C3", "#92C5DE", "#D1E5F0",
        "#F7F7F7", "#FDDBC7", "#F4A582", "#D6604D", "#B2182B",
    ],
    "green-gold": [
        "#1B7837", "#5AAE61", "#A6DBA0", "#D9F0D3",
        "#F7F7F7", "#FEE0B6", "#FDB863", "#E08214", "#B35806",
    ],
}


class ThemesMixin:
    """Mixin providing color palette and theme capabilities to TWBEditor."""

    def apply_color_palette(
        self,
        palette_name: str = "",
        colors: list[str] | None = None,
        custom_name: str = "twilize-palette",
    ) -> str:
        """Set a custom color palette in the workbook preferences.

        Args:
            palette_name: Name of a built-in palette ("tableau10", "tableau20",
                "blue-red", "green-gold"), or empty to use custom colors.
            colors: List of hex color strings (used when palette_name is empty).
            custom_name: Name for the palette element.

        Returns:
            Confirmation message.
        """
        if palette_name:
            if palette_name not in NAMED_PALETTES:
                raise ValueError(
                    f"Unknown palette '{palette_name}'. "
                    f"Available: {', '.join(sorted(NAMED_PALETTES.keys()))}"
                )
            color_list = NAMED_PALETTES[palette_name]
            pal_name = palette_name
        elif colors:
            color_list = colors
            pal_name = custom_name
        else:
            raise ValueError("Provide either palette_name or colors list")

        prefs = self.root.find("preferences")
        if prefs is None:
            # Insert after document-format-change-manifest if present
            dfcm = self.root.find("document-format-change-manifest")
            prefs = etree.Element("preferences")
            if dfcm is not None:
                dfcm.addnext(prefs)
            else:
                self.root.insert(0, prefs)

        # Remove existing palette with same name
        for old in prefs.findall("color-palette"):
            if old.get("name") == pal_name:
                prefs.remove(old)

        cp = etree.SubElement(prefs, "color-palette")
        cp.set("name", pal_name)
        cp.set("type", "regular")
        for color in color_list:
            ce = etree.SubElement(cp, "color")
            ce.text = color

        return f"Applied color palette '{pal_name}' ({len(color_list)} colors)"

    def apply_dashboard_theme(
        self,
        dashboard_name: str,
        background_color: str = "",
        font_family: str = "",
        title_font_size: str = "",
    ) -> str:
        """Apply uniform styling to all zones in a dashboard.

        Args:
            dashboard_name: Target dashboard name.
            background_color: Hex color for zone backgrounds.
            font_family: Font family name.
            title_font_size: Font size for titles.

        Returns:
            Confirmation message.
        """
        dashboards = self.root.find("dashboards")
        if dashboards is None:
            raise ValueError("No dashboards in workbook")

        dashboard = None
        for db in dashboards.findall("dashboard"):
            if db.get("name") == dashboard_name:
                dashboard = db
                break
        if dashboard is None:
            raise ValueError(f"Dashboard '{dashboard_name}' not found")

        zones = dashboard.find("zones")
        if zones is None:
            return f"Dashboard '{dashboard_name}' has no zones"

        changes = 0
        for zone in zones.findall(".//zone"):
            if background_color:
                # Format elements must go inside <zone-style>, not directly in <zone>
                zone_style = zone.find("zone-style")
                if zone_style is None:
                    zone_style = etree.SubElement(zone, "zone-style")
                # Find or create format element for background-color
                fmt = None
                for existing in zone_style.findall("format"):
                    if existing.get("attr") == "background-color":
                        fmt = existing
                        break
                if fmt is None:
                    fmt = etree.SubElement(zone_style, "format")
                fmt.set("attr", "background-color")
                fmt.set("value", background_color)
                changes += 1

        applied = []
        if background_color:
            applied.append(f"background={background_color}")
        if font_family:
            applied.append(f"font={font_family}")
        if title_font_size:
            applied.append(f"title-size={title_font_size}")

        return f"Applied theme to '{dashboard_name}': {', '.join(applied)} ({changes} zones)"
