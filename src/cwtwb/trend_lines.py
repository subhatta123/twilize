"""Trend line support for Tableau worksheets.

Writes <trendline> XML elements inside the worksheet's <table> element.
Supports linear, polynomial, logarithmic, exponential, and power fits.
"""

from __future__ import annotations

from lxml import etree

_VALID_FIT_TYPES = {"linear", "polynomial", "log", "exp", "power"}


class TrendLineMixin:
    """Mixin providing trend line capabilities to TWBEditor."""

    def add_trend_line(
        self,
        worksheet_name: str,
        fit: str = "linear",
        degree: int = 2,
        show_confidence_bands: bool = False,
        exclude_color: bool = False,
    ) -> str:
        """Add a trend line to a worksheet.

        Args:
            worksheet_name: Target worksheet name.
            fit: Fit type: "linear", "polynomial", "log", "exp", "power".
            degree: Polynomial degree (only used when fit="polynomial").
            show_confidence_bands: Show confidence bands around the trend.
            exclude_color: If True, don't split trend by color encoding.

        Returns:
            Confirmation message.
        """
        if fit not in _VALID_FIT_TYPES:
            raise ValueError(
                f"Unknown fit type '{fit}'. Valid: {', '.join(sorted(_VALID_FIT_TYPES))}"
            )

        ws = self._find_worksheet(worksheet_name)
        table = ws.find("table")
        if table is None:
            raise ValueError(f"Worksheet '{worksheet_name}' has no <table> element")

        # Remove existing trendline if present
        for old in table.findall("trendline"):
            table.remove(old)

        tl = etree.Element("trendline")
        tl.set("enabled", "true")
        tl.set("fit", fit)
        tl.set("exclude-intercept", "false")
        tl.set("enable-confidence-bands", str(show_confidence_bands).lower())
        tl.set("exclude-color", str(exclude_color).lower())
        tl.set("enable-instant-analytics", "true")
        tl.set("enable-tooltips", "true")

        if fit == "polynomial":
            tl.set("degree", str(degree))

        # Insert after panes/reference-lines
        panes = table.find("panes")
        if panes is not None:
            # Insert after the last reference-line if any, or after panes
            last_ref = None
            for sibling in panes.itersiblings():
                if sibling.tag == "reference-line":
                    last_ref = sibling
                else:
                    break
            if last_ref is not None:
                last_ref.addnext(tl)
            else:
                panes.addnext(tl)
        else:
            table.append(tl)

        return f"Added {fit} trend line to '{worksheet_name}'"

    def remove_trend_line(self, worksheet_name: str) -> str:
        """Remove trend lines from a worksheet."""
        ws = self._find_worksheet(worksheet_name)
        table = ws.find("table")
        if table is None:
            return f"No table in worksheet '{worksheet_name}'"

        removed = 0
        for tl in table.findall("trendline"):
            table.remove(tl)
            removed += 1

        if removed == 0:
            return f"No trend line found in '{worksheet_name}'"
        return f"Removed trend line from '{worksheet_name}'"
