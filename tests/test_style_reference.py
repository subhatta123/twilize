"""Regression tests for apply_style_reference (feature added after 0.33.1).

Covers the two supported sources — CSS/HTML and reference image — and the
merge policy between them, plus a smoke test for the end-to-end workbook
rewrite.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from lxml import etree

from twilize.style_reference import (
    extract_from_css,
    extract_from_html,
    extract_from_image,
    extract_style_reference,
    merge_style_sources,
)
from twilize.twb_editor import TWBEditor


def test_css_extracts_colors_and_typography():
    """CSS declarations map to the right slots in the StyleReference dict."""
    css = """
        body { background: #111e29; color: #fff; font-size: 20px; font-family: "Inter", sans-serif; }
        .card { background: #f5f5f5; color: #5a6c7d; font-size: 11px; }
        .accent { color: #e15759; }
    """
    result = extract_from_css(css)

    assert result["colors"]["background"] == "#111e29"
    assert result["colors"]["card_background"] == "#f5f5f5"
    assert result["colors"]["text_primary"] == "#ffffff"
    assert result["colors"]["text_secondary"] == "#5a6c7d"
    # #e15759 is the most saturated non-background color declared.
    assert result["colors"]["accent"] == "#e15759"
    assert result["typography"]["title_size"] == 20
    assert result["typography"]["font_family"] == "Inter"


def test_html_style_blocks_are_parsed():
    """Embedded <style> blocks in an HTML fragment are extracted the same way."""
    html = """
        <html><head>
          <style>
            body { background: #202020; color: #efefef; font-size: 18px; }
          </style>
        </head><body></body></html>
    """
    result = extract_from_html(html)
    assert result["colors"]["background"] == "#202020"
    assert result["colors"]["text_primary"] == "#efefef"
    assert result["typography"]["title_size"] == 18


def test_image_palette_contains_source_band_colors():
    """A PNG with three hard colour bands round-trips through extract_from_image."""
    from PIL import Image

    # Three 100x33 colored bands stacked vertically.
    img = Image.new("RGB", (100, 100), (255, 255, 255))
    for y in range(100):
        if y < 34:
            color = (10, 10, 200)    # blue
        elif y < 67:
            color = (220, 20, 20)    # red
        else:
            color = (240, 240, 240)  # near-white
        for x in range(100):
            img.putpixel((x, y), color)

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bands.png"
        img.save(p)
        result = extract_from_image(str(p))

    palette = result["palette"]

    def _rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))

    def _close(target, tol=30):
        return any(
            all(abs(a - b) <= tol for a, b in zip(_rgb(c), target))
            for c in palette
        )

    assert _close((10, 10, 200)), f"blue band missing from palette {palette}"
    assert _close((220, 20, 20)), f"red band missing from palette {palette}"
    assert _close((240, 240, 240)), f"light band missing from palette {palette}"
    # Light band becomes the background slot.
    bg = result["colors"]["background"]
    bg_rgb = _rgb(bg)
    assert min(bg_rgb) >= 200, f"background {bg} should be light"


def test_merge_policy_css_typography_image_palette():
    """CSS wins typography; image wins palette; colours don't cross over."""
    css_side = extract_from_css(
        "body { font-family: 'Roboto'; font-size: 16px; }"
    )
    image_side = {
        "palette": ["#ff0000", "#00ff00", "#0000ff"],
        "colors": {"background": "#111111", "accent": "#ff00ff"},
    }
    merged = merge_style_sources(css_side, image_side)

    assert merged["typography"]["font_family"] == "Roboto"
    assert merged["palette"] == ["#ff0000", "#00ff00", "#0000ff"]
    # CSS declared no background, so image's background survives.
    assert merged["colors"]["background"] == "#111111"


def test_apply_style_reference_rewrites_dashboard_background():
    """End-to-end: applying CSS rewrites the dashboard <style> background."""
    editor = TWBEditor("")
    editor.add_worksheet("Sheet1")
    editor.add_dashboard("Exec", worksheet_names=["Sheet1"])

    style = editor.apply_style_reference(
        css="body { background: #0b0f14; color: #e8edf2; font-size: 14px; }"
    )
    assert style["colors"]["background"] == "#0b0f14"

    db = editor.root.find("dashboards/dashboard[@name='Exec']")
    db_style = db.find("style")
    assert db_style is not None
    table_rule = next(
        sr for sr in db_style.findall("style-rule") if sr.get("element") == "table"
    )
    bg_fmt = next(
        f for f in table_rule.findall("format") if f.get("attr") == "background-color"
    )
    assert bg_fmt.get("value") == "#0b0f14"


def test_apply_style_reference_requires_a_source():
    """Calling with no source is a user error, not a silent no-op."""
    editor = TWBEditor("")
    with pytest.raises(ValueError, match="requires at least one of"):
        editor.apply_style_reference()
