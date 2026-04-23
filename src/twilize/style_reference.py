"""Reference-style extraction for ``apply_style_reference``.

Two input sources, one normalised dict:

  1. CSS / HTML — parsed with ``tinycss2``. Extracts background-color, color,
     font-family, font-size, border-*. Ranks colors by declared frequency.
  2. Image — Pillow + ``colorthief`` dominant palette, plus in-house pixel
     analysis for card style, layout density, and a categorical chart palette
     (no OCR; no ML).

The output is a single ``StyleReference`` dict:

    {
      "palette":        ["#...", ...],       # dominant colors, role-ordered
      "colors": {                            # 6 role-assigned colors
          "background", "card_background",
          "text_primary", "text_secondary",
          "accent", "border",
      },
      "chart_palette":  ["#...", ...],       # categorical, for chart marks
      "card_style": {                        # visual card treatment
          "border_width": 0|1|2,             # integer px (Tableau's unit)
          "border_color": "#rrggbb",
          "padding":       "tight"|"normal"|"spacious",
          "corner_style":  "rounded"|"sharp",
          "has_elevation": bool,             # apparent drop-shadow
      },
      "layout_style": {
          "density":       "dense"|"normal"|"spacious",
          "whitespace_ratio": float,         # 0.0–1.0
          "zone_margin":     int,            # px, for dashboard zones
      },
      "typography": {
          "font_family", "title_size", "body_size", "caption_size",
          "weight":        "light"|"regular"|"bold",
          "style":         "sans-serif"|"serif",
      },
      "borders":  {"width", "radius", "color"},
      "not_applied": [                       # things Tableau XML can't do
          "corner_radius_cards",             # no border-radius on zones
          "drop_shadows",                    # no shadow primitives
      ],
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


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def _analyze_card_and_layout(
    image_path: str | Path, bg_hex: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derive card-style + layout-density metadata from a reference image.

    Uses pure Pillow (no numpy / opencv).  Heuristics:

    * **whitespace_ratio** — fraction of pixels within 10 units of the page
      background color.  >55% → spacious, 35–55% → normal, <35% → dense.
    * **border_width / border_color** — scan horizontal + vertical lines of
      pixels and count transitions where the color changes abruptly from
      the card color to a darker color for exactly 1–2 pixels before
      returning to a light color.  If the pattern dominates we infer a
      border; otherwise 0.
    * **has_elevation** — same scan but counts *gradual* (3–6 pixel) dark
      fades adjacent to light card bodies; a classic drop-shadow fingerprint.
    * **corner_style** — at 20 sample points around card-like regions, check
      whether the 3×3 corner patch is a blend of card and background
      colours (rounded) or purely card (sharp).
    """
    from PIL import Image

    try:
        im = Image.open(str(image_path)).convert("RGB")
    except Exception:
        return {}, {}

    # Downscale for speed — heuristics don't need full resolution.
    max_dim = 600
    if max(im.size) > max_dim:
        ratio = max_dim / max(im.size)
        im = im.resize(
            (int(im.size[0] * ratio), int(im.size[1] * ratio)),
            Image.Resampling.LANCZOS,
        )
    px = im.load()
    w, h = im.size

    bg_rgb = _hex_to_rgb(bg_hex)

    # ── whitespace_ratio ──
    near_bg = 0
    total = w * h
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            if _rgb_distance(px[x, y], bg_rgb) < 18:
                near_bg += 1
    sampled = ((h + 1) // 2) * ((w + 1) // 2)
    whitespace_ratio = near_bg / max(sampled, 1)

    if whitespace_ratio > 0.55:
        density = "spacious"
        zone_margin = 12
    elif whitespace_ratio < 0.30:
        density = "dense"
        zone_margin = 2
    else:
        density = "normal"
        zone_margin = 6

    # ── border / elevation detection ──
    # Scan horizontal scanlines and count runs of "dark-enough" pixels
    # adjacent to "bg-like" pixels.  Sharp 1–2 px → border, 3–6 px gradient
    # → shadow.
    def _brightness(rgb: tuple[int, int, int]) -> float:
        return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255

    bg_bright = _brightness(bg_rgb)

    sharp_runs = 0
    gradient_runs = 0
    total_runs = 0
    darker_samples: list[tuple[int, int, int]] = []

    for y in range(5, h - 5, max(1, h // 40)):
        x = 0
        while x < w - 1:
            # Scan forward until we hit a darker transition
            start_bright = _brightness(px[x, y])
            run_len = 0
            while x + run_len < w and (
                abs(_brightness(px[x + run_len, y]) - start_bright) < 0.08
            ):
                run_len += 1
            x += run_len
            if x >= w - 1:
                break
            # Measure the dark-transition length
            transition_start = x
            trans_len = 0
            while (
                x + trans_len < w
                and _brightness(px[x + trans_len, y]) + 0.15 < bg_bright
                and trans_len < 8
            ):
                darker_samples.append(px[x + trans_len, y])
                trans_len += 1
            if 1 <= trans_len <= 2:
                sharp_runs += 1
                total_runs += 1
            elif 3 <= trans_len <= 6:
                gradient_runs += 1
                total_runs += 1
            x += max(trans_len, 1)

    has_elevation = gradient_runs > sharp_runs and gradient_runs > 8
    border_width = 0
    if sharp_runs > gradient_runs and sharp_runs > 8:
        # average transition length across sharp runs ≈ 1 or 2
        border_width = 1 if sharp_runs > 40 else 2

    # Border color: average of the darker pixels collected (RGB mean)
    if darker_samples:
        mean_r = sum(s[0] for s in darker_samples) // len(darker_samples)
        mean_g = sum(s[1] for s in darker_samples) // len(darker_samples)
        mean_b = sum(s[2] for s in darker_samples) // len(darker_samples)
        border_color = f"#{mean_r:02x}{mean_g:02x}{mean_b:02x}"
    else:
        border_color = ""

    # ── Corner-style heuristic ──
    # Sample 4 corners of the central region; in a rounded-card layout the
    # corner pixels blend with the background.
    corner_rounded_votes = 0
    for (cx, cy) in [
        (int(w * 0.28), int(h * 0.28)),
        (int(w * 0.72), int(h * 0.28)),
        (int(w * 0.28), int(h * 0.72)),
        (int(w * 0.72), int(h * 0.72)),
    ]:
        if 3 <= cx < w - 3 and 3 <= cy < h - 3:
            # Top-left corner patch: if the pixel-level variance between
            # card and background is high here, we infer rounding.
            patch_pixels = [
                px[cx + dx, cy + dy] for dx in (-2, -1, 0, 1, 2)
                for dy in (-2, -1, 0, 1, 2)
            ]
            close_bg = sum(
                1 for p in patch_pixels if _rgb_distance(p, bg_rgb) < 22
            )
            close_card = sum(
                1 for p in patch_pixels if _rgb_distance(p, bg_rgb) > 28
            )
            if close_bg >= 5 and close_card >= 5:
                corner_rounded_votes += 1
    corner_style = "rounded" if corner_rounded_votes >= 2 else "sharp"

    card_style = {
        "border_width": int(border_width),
        "border_color": border_color,
        "padding": density,
        "corner_style": corner_style,
        "has_elevation": bool(has_elevation),
    }
    layout_style = {
        "density": density,
        "whitespace_ratio": round(whitespace_ratio, 3),
        "zone_margin": zone_margin,
    }
    return card_style, layout_style


def _build_chart_palette(
    palette: list[str], bg_hex: str, card_bg_hex: str,
) -> list[str]:
    """Pick a categorical chart-mark palette from the dominant colors.

    The first six colours from ``colorthief`` usually include the page bg
    and the card bg — both useless as mark colours.  This picks the most
    saturated, distinct, medium-luminance colors, preferring variety.
    """
    bg_rgb = _hex_to_rgb(bg_hex)
    card_rgb = _hex_to_rgb(card_bg_hex) if card_bg_hex else bg_rgb
    out: list[str] = []
    for c in sorted(palette, key=_saturation, reverse=True):
        c_rgb = _hex_to_rgb(c)
        if _rgb_distance(c_rgb, bg_rgb) < 30:
            continue  # too similar to background — would invisibilise marks
        if _rgb_distance(c_rgb, card_rgb) < 30:
            continue  # would disappear on card
        lum = _luminance(c)
        if lum > 0.93 or lum < 0.05:
            continue  # too light / too dark for bar colours
        # Avoid near-duplicates of what's already in the palette.
        if any(_rgb_distance(c_rgb, _hex_to_rgb(p)) < 35 for p in out):
            continue
        out.append(c)
        if len(out) >= 6:
            break
    # Always end with at least one colour — fall back to the accent hue.
    if not out and palette:
        out.append(palette[0])
    return out


def extract_from_image(image_path: str | Path, palette_size: int = 8) -> dict[str, Any]:
    """Extract palette + heuristic color roles + card/layout/typography hints.

    Uses ``colorthief`` for the dominant palette (no scikit-learn), Pillow
    for the card-style / layout-density heuristics, and a handful of pure-
    Python rules for a categorical chart palette.

    Role assignment (unchanged):
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
    raw = ct.get_palette(color_count=max(palette_size, 4), quality=5)
    hex_palette = [f"#{r:02x}{g:02x}{b:02x}" for (r, g, b) in raw]

    # Dedup while preserving order; ColorThief can emit near-duplicates.
    seen: set[str] = set()
    palette: list[str] = [c for c in hex_palette if not (c in seen or seen.add(c))]
    if not palette:
        return {}

    by_lum = sorted(palette, key=_luminance)
    lightest = by_lum[-1]
    darkest = by_lum[0]
    second_dark = by_lum[1] if len(by_lum) > 1 else darkest

    # card_background must be a NEUTRAL light color — ColorThief's second-
    # lightest sample is often a saturated hue (beige/rose) that happens to
    # be bright, because the algorithm averages bar fills.  Require low
    # saturation so "white cards on light-grey" dashboards don't end up
    # painting the cards beige.
    neutral_lights = [
        c for c in by_lum[::-1]
        if _luminance(c) > 0.85 and _saturation(c) < 0.12
    ]
    if len(neutral_lights) >= 2:
        second_light = neutral_lights[1]
    elif neutral_lights:
        # Only one neutral light → cards share the page background.
        second_light = neutral_lights[0]
    else:
        second_light = by_lum[-2] if len(by_lum) > 1 else lightest

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

    # Categorical chart palette: saturated, distinct, mid-luminance.
    chart_palette = _build_chart_palette(palette, lightest, second_light)

    # Card-style + layout-density from pixel analysis.
    card_style, layout_style = _analyze_card_and_layout(image_path, lightest)

    # Prefer the detected border color when it's more saturated / darker
    # than the palette-derived border — it was measured directly from the
    # card edges, so it's a more faithful match to the reference.
    if card_style and card_style.get("border_color"):
        colors["border"] = card_style["border_color"]

    # Minimal typography hint — we cannot OCR a font face from an image,
    # but reference dashboards are overwhelmingly sans-serif with modest
    # title sizes.  Expose stable defaults the downstream restyler can use.
    typography = {
        "style": "sans-serif",
        "weight": "regular",
        "font_family": "Tableau Book",
        "title_size": 14,
        "body_size": 11,
        "caption_size": 9,
    }

    return {
        "palette": role_ordered[:palette_size],
        "colors": colors,
        "chart_palette": chart_palette,
        "card_style": card_style,
        "layout_style": layout_style,
        "typography": typography,
        # Things Tableau's workbook XML explicitly cannot represent; the
        # pipeline surfaces this list in the manifest so agents don't claim
        # they were applied.
        "not_applied": [
            "corner_radius_cards",   # Tableau zones don't support border-radius
            "drop_shadows",          # no shadow primitive in zone-style
        ],
    }


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

    # Image-only rich-styling fields — no CSS counterparts today, but pass
    # them through so downstream code gets them when the image source is
    # present.
    for key in ("chart_palette", "card_style", "layout_style", "not_applied"):
        if image_result.get(key):
            out[key] = image_result[key]
    # Typography: CSS still wins overall, but if CSS didn't declare a
    # typography dict at all, carry the image-level hint through so the
    # restyler has a font_family / size triplet to work with.
    if "typography" not in out and image_result.get("typography"):
        out["typography"] = image_result["typography"]

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
