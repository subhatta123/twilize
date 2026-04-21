"""Reference-style extraction for ``apply_style_reference``.

Two input sources, one normalised dict:

  1. CSS / HTML — parsed with ``tinycss2``. Extracts background-color, color,
     font-family, font-size, border-*. Ranks colors by declared frequency.
  2. Image — Pillow + ``colorthief`` dominant palette (6 colors). Role-assigned
     by luminance and saturation (no OCR).

The output is a single ``StyleReference`` dict:

    {
      "palette": ["#...", ...],            # 6 colors, role-ordered
      "colors": {
          "background", "card_background",
          "text_primary", "text_secondary",
          "accent", "border",
      },
      "typography": {
          "font_family", "title_size", "body_size", "caption_size",
      },
      "borders": {"width", "radius", "color"},
    }

Merge policy (``merge_style_sources``): CSS wins on typography, image wins on
palette. Neither source overrides keys the other did not set.
"""

from __future__ import annotations

import colorsys
import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hex / RGB helpers
# ---------------------------------------------------------------------------


_HEX_RE = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")
_RGB_RE = re.compile(
    r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*[\d.]+)?\s*\)",
    re.IGNORECASE,
)
# Minimal CSS named-color table — enough for common mockup CSS.
_NAMED_COLORS: dict[str, str] = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000",
    "green": "#008000", "blue": "#0000ff", "yellow": "#ffff00",
    "gray": "#808080", "grey": "#808080", "silver": "#c0c0c0",
    "navy": "#000080", "teal": "#008080", "orange": "#ffa500",
    "purple": "#800080", "pink": "#ffc0cb", "brown": "#a52a2a",
    "cyan": "#00ffff", "magenta": "#ff00ff", "lime": "#00ff00",
}


def _normalize_hex(val: str) -> Optional[str]:
    """Normalise any CSS color expression to lowercase 6-digit #rrggbb. None if unparseable."""
    val = val.strip().lower()
    if val in _NAMED_COLORS:
        return _NAMED_COLORS[val]
    m = _HEX_RE.match(val)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        elif len(h) == 4:
            h = "".join(c * 2 for c in h[:3])
        elif len(h) == 8:
            h = h[:6]
        return "#" + h.lower()
    m = _RGB_RE.match(val)
    if m:
        r, g, b = (int(m.group(i)) for i in (1, 2, 3))
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


def _luminance(hex_color: str) -> float:
    """Perceived luminance (Rec.709) of a hex color, 0.0–1.0."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _saturation(hex_color: str) -> float:
    """HSL saturation of a hex color, 0.0–1.0."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    _, _, s = colorsys.rgb_to_hls(r, g, b)
    return s


# ---------------------------------------------------------------------------
# CSS / HTML extraction
# ---------------------------------------------------------------------------


def _extract_css_from_html(html: str) -> str:
    """Pull every ``<style>…</style>`` block out of an HTML fragment."""
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.DOTALL | re.IGNORECASE)
    return "\n".join(blocks)


def _iter_declarations(css: str):
    """Yield (name, value_str) for every declaration in a CSS source."""
    import tinycss2

    rules = tinycss2.parse_stylesheet(css, skip_whitespace=True, skip_comments=True)
    for rule in rules:
        if rule.type != "qualified-rule":
            continue
        declarations = tinycss2.parse_declaration_list(
            rule.content, skip_whitespace=True, skip_comments=True
        )
        for decl in declarations:
            if decl.type != "declaration":
                continue
            value_str = "".join(tok.serialize() for tok in decl.value).strip()
            yield decl.lower_name, value_str


def extract_from_css(css: str) -> dict[str, Any]:
    """Extract a partial StyleReference dict from a CSS source string.

    Returns only the keys that were actually declared in the CSS, so callers
    can merge CSS results with an image palette without overwriting.
    """
    bg_colors: list[str] = []
    text_colors: list[str] = []
    border_colors: list[str] = []
    all_colors: list[str] = []
    font_sizes: list[float] = []
    font_families: list[str] = []
    border_widths: list[float] = []
    border_radii: list[float] = []

    def _to_px(s: str) -> Optional[float]:
        m = re.match(r"([\d.]+)\s*(px|pt|em|rem)?", s.strip())
        if not m:
            return None
        val = float(m.group(1))
        unit = (m.group(2) or "px").lower()
        # rough em/rem → 16px baseline; pt → 1.333px
        return {"px": val, "pt": val * 1.333, "em": val * 16, "rem": val * 16}.get(unit, val)

    for name, value in _iter_declarations(css):
        if name in ("background-color", "background"):
            c = _normalize_hex(value.split()[0] if value else "")
            if c:
                bg_colors.append(c)
                all_colors.append(c)
        elif name == "color":
            c = _normalize_hex(value)
            if c:
                text_colors.append(c)
                all_colors.append(c)
        elif name == "border-color":
            c = _normalize_hex(value.split()[0] if value else "")
            if c:
                border_colors.append(c)
                all_colors.append(c)
        elif name == "border":
            for tok in value.split():
                c = _normalize_hex(tok)
                if c:
                    border_colors.append(c)
                    all_colors.append(c)
                w = _to_px(tok)
                if w and tok[0].isdigit():
                    border_widths.append(w)
        elif name == "border-width":
            w = _to_px(value)
            if w is not None:
                border_widths.append(w)
        elif name == "border-radius":
            w = _to_px(value.split()[0] if value else "")
            if w is not None:
                border_radii.append(w)
        elif name == "font-size":
            w = _to_px(value)
            if w is not None:
                font_sizes.append(w)
        elif name == "font-family":
            family = value.strip().strip('"').strip("'")
            family = family.split(",")[0].strip().strip('"').strip("'")
            if family:
                font_families.append(family)

    out: dict[str, Any] = {}

    colors: dict[str, str] = {}
    if bg_colors:
        # First declared = page/body background; second (if distinct) = cards.
        colors["background"] = bg_colors[0]
        for c in bg_colors[1:]:
            if c != colors["background"]:
                colors["card_background"] = c
                break
    if text_colors:
        colors["text_primary"] = text_colors[0]
        for c in text_colors[1:]:
            if c != colors["text_primary"]:
                colors["text_secondary"] = c
                break
    if border_colors:
        colors["border"] = border_colors[0]
    # Accent = most-saturated non-background color declared.
    non_bg = [c for c in all_colors if c not in bg_colors]
    if non_bg:
        accent = max(non_bg, key=_saturation)
        if _saturation(accent) > 0.15:
            colors["accent"] = accent
    if colors:
        out["colors"] = colors

    typography: dict[str, Any] = {}
    if font_families:
        typography["font_family"] = font_families[0]
    if font_sizes:
        sizes = sorted(set(font_sizes), reverse=True)
        typography["title_size"] = int(round(sizes[0]))
        typography["body_size"] = int(round(sizes[1 if len(sizes) > 1 else 0]))
        typography["caption_size"] = int(round(sizes[-1]))
    if typography:
        out["typography"] = typography

    borders: dict[str, Any] = {}
    if border_widths:
        borders["width"] = int(round(min(border_widths)))
    if border_radii:
        borders["radius"] = int(round(border_radii[0]))
    if border_colors:
        borders["color"] = border_colors[0]
    if borders:
        out["borders"] = borders

    # A small palette from CSS for completeness (image palette will override).
    if all_colors:
        # Preserve declaration order, dedup.
        seen: set[str] = set()
        ordered = [c for c in all_colors if not (c in seen or seen.add(c))]
        out["palette"] = ordered[:6]

    return out


def extract_from_html(html: str) -> dict[str, Any]:
    """Extract style from an HTML snippet (pulls every ``<style>`` block)."""
    return extract_from_css(_extract_css_from_html(html))


# ---------------------------------------------------------------------------
# Image extraction
# ---------------------------------------------------------------------------


def extract_from_image(image_path: str | Path, palette_size: int = 6) -> dict[str, Any]:
    """Extract palette + heuristic color roles from a reference image.

    Uses ``colorthief`` for dominant palette extraction (no scikit-learn).
    Role assignment:
      - lightest       → background
      - next lightest  → card_background
      - darkest        → text_primary
      - next darkest   → text_secondary
      - most-saturated → accent
      - first mid-lum  → border
    """
    from colorthief import ColorThief

    ct = ColorThief(str(image_path))
    # quality=1 is slowest/most-accurate; raise default for test determinism
    raw = ct.get_palette(color_count=max(palette_size, 3), quality=5)
    hex_palette = [f"#{r:02x}{g:02x}{b:02x}" for (r, g, b) in raw]

    # Dedup while preserving order; ColorThief can emit near-duplicates.
    seen: set[str] = set()
    palette: list[str] = [c for c in hex_palette if not (c in seen or seen.add(c))]
    if not palette:
        return {}

    by_lum = sorted(palette, key=_luminance)
    lightest = by_lum[-1]
    second_light = by_lum[-2] if len(by_lum) > 1 else lightest
    darkest = by_lum[0]
    second_dark = by_lum[1] if len(by_lum) > 1 else darkest
    by_sat = sorted(palette, key=_saturation, reverse=True)
    accent = by_sat[0] if _saturation(by_sat[0]) > 0.1 else palette[0]

    # Border: pick a mid-luminance color (not lightest, not darkest).
    mids = [c for c in by_lum if c not in (lightest, darkest)]
    border = mids[len(mids) // 2] if mids else lightest

    colors = {
        "background": lightest,
        "card_background": second_light,
        "text_primary": darkest,
        "text_secondary": second_dark,
        "accent": accent,
        "border": border,
    }

    # Role-ordered palette for Tableau categorical color encodings.
    role_ordered: list[str] = []
    for slot in (accent, second_dark, lightest, darkest, second_light, border):
        if slot and slot not in role_ordered:
            role_ordered.append(slot)
    for c in palette:
        if c not in role_ordered:
            role_ordered.append(c)

    return {"palette": role_ordered[:palette_size], "colors": colors}


# ---------------------------------------------------------------------------
# Merge policy
# ---------------------------------------------------------------------------


def merge_style_sources(
    css_result: Optional[dict[str, Any]],
    image_result: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Merge CSS-derived and image-derived style dicts into one StyleReference.

    Policy:
      - Typography: CSS wins (image has none — we don't do OCR).
      - Palette: image wins; CSS palette fills gaps if image absent.
      - Colors: image provides role-assigned colors for any keys CSS did
        not declare (CSS wins when both declared the same key).
      - Borders: CSS-only (images give no border metadata).
    """
    css_result = css_result or {}
    image_result = image_result or {}

    out: dict[str, Any] = {}

    palette = image_result.get("palette") or css_result.get("palette") or []
    if palette:
        out["palette"] = palette

    css_colors = css_result.get("colors", {}) or {}
    image_colors = image_result.get("colors", {}) or {}
    colors = dict(image_colors)
    colors.update(css_colors)  # CSS overrides image for any co-declared role
    if colors:
        out["colors"] = colors

    typography = css_result.get("typography") or {}
    if typography:
        out["typography"] = typography

    borders = css_result.get("borders") or {}
    if borders:
        out["borders"] = borders

    return out


def extract_style_reference(
    image_path: Optional[str] = None,
    css: Optional[str] = None,
    html: Optional[str] = None,
) -> dict[str, Any]:
    """Top-level entry point: combine any supplied sources into one dict.

    Raises ``ValueError`` if no source is provided.
    """
    if not any((image_path, css, html)):
        raise ValueError(
            "apply_style_reference requires at least one of: "
            "image_path, css, html."
        )

    css_src: Optional[dict[str, Any]] = None
    if html:
        css_src = extract_from_html(html)
    if css:
        merged_css = extract_from_css(css)
        css_src = merge_style_sources(css_src, {}) | merged_css if css_src else merged_css

    image_src: Optional[dict[str, Any]] = None
    if image_path:
        image_src = extract_from_image(image_path)

    return merge_style_sources(css_src, image_src)
