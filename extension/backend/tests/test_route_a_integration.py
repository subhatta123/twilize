"""Route-A integration test: every recent mainline fix lands on the trex path.

This test builds a small dashboard via the extension's
``generate_workbook`` entry point (the one behind ``/api/generate``) and
asserts that the three fixes we patched onto the trex route actually show
up in the output:

1. **Global filter scope** — worksheet ``<filter>`` elements that share a
   column carry a matching ``filter-group`` integer so Tableau renders the
   dashboard filter as "Apply to Worksheets > All Using This Data Source"
   instead of "Only This Worksheet".
2. **Reference-image styling** — when a reference image path is supplied
   the extracted palette is applied to the workbook and surfaced on the
   response manifest under ``style_reference``.
3. **Structured manifest** — ``generate_workbook`` now returns a dict with
   ``output_path`` + ``manifest``; the manifest exposes
   ``required_charts_fulfilled`` and the filter summary.

The test skips when ``tableauhyperapi`` isn't installed (it's an optional
dep and not available on every CI box).
"""

from __future__ import annotations

import random
import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest

pytest.importorskip(
    "tableauhyperapi",
    reason="Extension pipeline needs tableauhyperapi to materialise the .hyper extract.",
)

from lxml import etree

from extension.backend.chart_suggestion import suggest_dashboard
from extension.backend.pipeline import generate_workbook
from extension.backend.schema_inference import TableauField


def _write_tiny_png(tmp_path: Path) -> Path:
    """Write a 32x32 three-color PNG to disk for apply_style_reference.

    We use Pillow (a transitive dep of ``style_reference``) to build a
    real image so the downstream palette extractor has something
    non-trivial to chew on.
    """
    PIL = pytest.importorskip(
        "PIL",
        reason="Pillow is required to generate the reference image fixture.",
    )
    from PIL import Image  # noqa: PLC0415

    img = Image.new("RGB", (32, 32), color=(230, 70, 70))  # red base
    for y in range(32):
        for x in range(32):
            if x < 16 and y < 16:
                img.putpixel((x, y), (60, 90, 200))  # blue quadrant
            elif x >= 16 and y >= 16:
                img.putpixel((x, y), (240, 200, 60))  # yellow quadrant
    path = tmp_path / "ref.png"
    img.save(path, "PNG")
    assert path.exists() and path.stat().st_size > 0
    del PIL  # silence unused-import warnings under strict linters
    return path


def _synthetic_data(n_rows: int = 80) -> tuple[list[TableauField], list[list[object]]]:
    """Synthesize a Superstore-shaped slice: 3 dims + 2 measures + 1 date.

    We pick fields that reliably trigger ≥2 auto-filters (low-cardinality
    dimensions like Region / Segment / Category) so the
    ``_link_global_filters`` pass has multiple worksheets to link.
    """
    rng = random.Random(42)
    regions = ["East", "West", "Central", "South"]
    segments = ["Consumer", "Corporate", "Home Office"]
    categories = ["Furniture", "Office Supplies", "Technology"]

    fields = [
        TableauField(name="Order Date", datatype="date", cardinality=n_rows, role="dimension"),
        TableauField(name="Region", datatype="string", cardinality=len(regions), role="dimension"),
        TableauField(name="Segment", datatype="string", cardinality=len(segments), role="dimension"),
        TableauField(name="Category", datatype="string", cardinality=len(categories), role="dimension"),
        TableauField(name="Sales", datatype="float", cardinality=n_rows, role="measure"),
        TableauField(name="Profit", datatype="float", cardinality=n_rows, role="measure"),
    ]

    rows: list[list[object]] = []
    for i in range(n_rows):
        rows.append([
            f"2024-{(i % 12) + 1:02d}-01",
            rng.choice(regions),
            rng.choice(segments),
            rng.choice(categories),
            round(rng.uniform(100, 5000), 2),
            round(rng.uniform(-500, 2000), 2),
        ])
    return fields, rows


def _filter_groups_in_twbx(twbx_path: Path) -> dict[str, list[str | None]]:
    """Read the .twbx back and return {column: [filter_group, ...]} per worksheet.

    The mapping lets the assertion check both "were columns stamped" and
    "did every worksheet referencing that column get the same group id".
    """
    out: dict[str, list[str | None]] = {}
    with zipfile.ZipFile(twbx_path) as z:
        twb_name = next(n for n in z.namelist() if n.endswith(".twb"))
        xml_bytes = z.read(twb_name)
    root = etree.fromstring(xml_bytes)
    worksheets_el = root.find("worksheets")
    if worksheets_el is None:
        return out
    for ws in worksheets_el.findall("worksheet"):
        for f in ws.iter("filter"):
            col = f.get("column")
            if not col:
                continue
            out.setdefault(col, []).append(f.get("filter-group"))
    return out


from collections.abc import Iterator


@pytest.fixture
def isolated_tmp() -> Iterator[Path]:
    """``tmp_path`` replacement that sidesteps Windows ACL issues on the
    default ``pytest-of-*`` basetemp. Creates a fresh directory under the
    OS temp root and cleans it up after the test."""
    d = Path(tempfile.mkdtemp(prefix="twilize_routea_"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_route_a_all_fixes_present(isolated_tmp: Path):
    """End-to-end: generate a dashboard via the extension and verify every
    mainline fix is reflected in both the manifest and the .twb XML."""
    tmp_path = isolated_tmp
    fields, rows = _synthetic_data(n_rows=80)
    plan = suggest_dashboard(
        fields=fields,
        row_count=len(rows),
        prompt="Build a small superstore dashboard with KPIs and a sales trend",
        max_charts=5,
        sample_rows=rows[:30],
    )

    # Only force the required chart if the suggester didn't already emit it.
    # Keeps the test robust against prompt tuning in the LLM-free fallback.
    existing_titles = {(c.get("title") or "").strip().lower() for c in plan.get("charts", [])}
    required_specs = []
    if "top 10 customers by profit" not in existing_titles:
        required_specs = [{
            "title": "Top 10 Customers by Profit",
            "chart_type": "Bar",
            "shelves": [
                {"field_name": "Segment", "shelf": "rows"},
                {"field_name": "Profit", "shelf": "columns", "aggregation": "SUM"},
            ],
        }]
        plan["charts"] = list(required_specs) + list(plan.get("charts", []))

    ref_png = _write_tiny_png(tmp_path)

    result = generate_workbook(
        fields=fields,
        data_rows=rows,
        plan=plan,
        output_dir=str(tmp_path),
        reference_image_path=str(ref_png),
        required_charts=required_specs,
    )

    # Return type sanity: we upgraded from str to dict.
    assert isinstance(result, dict), f"expected dict return, got {type(result)}"
    assert "output_path" in result and "manifest" in result

    output_path = Path(result["output_path"])
    assert output_path.exists(), f"output .twbx not written to {output_path}"
    assert output_path.suffix == ".twbx"

    manifest = result["manifest"]

    # --- Fix 1: structured manifest shape ------------------------------
    for key in (
        "dashboards",
        "filters",
        "global_filter_groups",
        "required_charts_requested",
        "required_charts_fulfilled",
        "style_reference",
        "warnings",
    ):
        assert key in manifest, f"manifest missing key {key!r}"

    assert manifest["required_charts_requested"] == len(required_specs)

    # --- Fix 2: reference-image styling --------------------------------
    # When a real image path is handed to generate_workbook the mainline
    # apply_style_reference routine runs; the manifest surfaces the
    # extracted palette under style_reference.extracted.
    assert manifest["style_reference"] is not None, (
        "reference image was not applied; manifest['style_reference'] is None"
    )
    extracted = (manifest["style_reference"] or {}).get("extracted", {})
    assert isinstance(extracted, dict) and extracted, (
        "style_reference.extracted should be a non-empty dict with palette/colors"
    )

    # --- Fix 3: global filter scope (filter-group stamping) ------------
    # The dashboard uses auto_filters which land on every chart worksheet.
    # For every column that appears on ≥2 worksheets, every <filter column=X>
    # MUST carry the same non-empty filter-group integer.
    filter_map = _filter_groups_in_twbx(output_path)
    assert filter_map, "no worksheet filters found in the generated .twb"

    linked_columns = {col: gids for col, gids in filter_map.items() if len(gids) >= 2}
    assert linked_columns, (
        "no column appears on two or more worksheets — can't exercise "
        "_link_global_filters. Test data / suggester must produce a shared "
        "filter column."
    )

    for col, gids in linked_columns.items():
        assert all(g is not None for g in gids), (
            f"column {col!r} has unstamped filter-group entries: {gids}"
        )
        assert len(set(gids)) == 1, (
            f"column {col!r} stamped with inconsistent filter-group ids: {gids}"
        )

    # Mirror of the above in the manifest, easier to read.
    fm = manifest["filters"]
    assert fm.get("global_scope_applied") is True, (
        f"filters.global_scope_applied should be True when "
        f"filter_groups={manifest.get('global_filter_groups')}"
    )
    assert fm.get("filter_groups"), "filters.filter_groups should be non-empty"
