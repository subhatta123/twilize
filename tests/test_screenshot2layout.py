"""Integration test: Screenshot to Layout — generate TWB dashboards from JSON layouts.

Reads layout JSON files extracted from dashboard screenshots, creates worksheets
with simple placeholder visuals, and generates TWB files.

Output:
  - output/screenshot_dashboard1.twb  (Superstore Shipping Metrics)
  - output/screenshot_dashboard2.twb  (Complaints Insights)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from cwtwb.server import (
    create_workbook,
    add_worksheet,
    configure_chart,
    add_dashboard,
    save_workbook,
)


def build_dashboard1():
    """Dashboard 1: Superstore Shipping Metrics replica."""
    project_root = Path(__file__).parent.parent
    layout_path = str(
        project_root / "examples" / "screenshot2layout" / "layout_dashboard1.json"
    )
    output_path = str(project_root / "output" / "screenshot_dashboard1.twb")

    print("=" * 60)
    print("Dashboard 1: Superstore Shipping Metrics")
    print("=" * 60)

    # 1. Create workbook
    result = create_workbook()
    print(result[:120], "...\n")

    # 2. Create worksheets — use Text mark to simulate KPI cards,
    #    and Bar charts to simulate the data areas.

    # Left sidebar KPIs
    sidebar_kpis = [
        ("Avg Delivery Days", "SUM(Quantity)"),
        ("Avg Fulfillment Days", "SUM(Discount)"),
        ("Orders KPI", "SUM(Quantity)"),
        ("Customers KPI", "SUM(Quantity)"),
        ("Returns KPI", "SUM(Quantity)"),
    ]
    for name, measure in sidebar_kpis:
        add_worksheet(name)
        configure_chart(
            worksheet_name=name,
            mark_type="Text",
            label=measure,
        )
        print(f"  [OK] {name}")

    # Breakdown row — 3 bar charts
    breakdown_sheets = [
        "Breakdown Customers",
        "Breakdown Orders",
        "Breakdown Returns",
    ]
    for name in breakdown_sheets:
        add_worksheet(name)
        configure_chart(
            worksheet_name=name,
            mark_type="Bar",
            rows=["Ship Mode"],
            columns=["SUM(Sales)"],
        )
        print(f"  [OK] {name}")

    # Delivery Days chart
    add_worksheet("Delivery Days Chart")
    configure_chart(
        worksheet_name="Delivery Days Chart",
        mark_type="Bar",
        columns=["SUM(Quantity)"],
        rows=["Ship Mode"],
        color="Segment",
    )
    print("  [OK] Delivery Days Chart")

    # Bottom row — 2 charts
    add_worksheet("Customer Distribution Map")
    configure_chart(
        worksheet_name="Customer Distribution Map",
        mark_type="Bar",
        rows=["Region"],
        columns=["SUM(Sales)"],
        color="Category",
    )
    print("  [OK] Customer Distribution Map")

    add_worksheet("Order Distribution Chart")
    configure_chart(
        worksheet_name="Order Distribution Chart",
        mark_type="Circle",
        rows=["Ship Mode"],
        columns=["SUM(Sales)"],
        size="SUM(Profit)",
    )
    print("  [OK] Order Distribution Chart")

    # 3. Add dashboard using the JSON layout
    worksheet_names = (
        [name for name, _ in sidebar_kpis]
        + breakdown_sheets
        + ["Delivery Days Chart", "Customer Distribution Map", "Order Distribution Chart"]
    )

    result = add_dashboard(
        dashboard_name="Shipping Metrics",
        worksheet_names=worksheet_names,
        width=1400,
        height=850,
        layout=layout_path,
    )
    print(f"\n  Dashboard: {result}")

    # 4. Save
    result = save_workbook(output_path)
    print(f"  Saved: {result}\n")


def build_dashboard2():
    """Dashboard 2: Complaints Insights replica."""
    project_root = Path(__file__).parent.parent
    layout_path = str(
        project_root / "examples" / "screenshot2layout" / "layout_dashboard2.json"
    )
    output_path = str(project_root / "output" / "screenshot_dashboard2.twb")

    print("=" * 60)
    print("Dashboard 2: Complaints Insights")
    print("=" * 60)

    # 1. Create workbook
    result = create_workbook()
    print(result[:120], "...\n")

    # 2. Left sidebar worksheets
    sidebar_sheets = [
        ("YTD Total Complaints", "Text", "SUM(Quantity)"),
        ("Timely Response Pct", "Text", "SUM(Discount)"),
        ("Complaints by Channel", "Bar", None),
        ("Closed with Relief Pct", "Text", "SUM(Profit)"),
        ("Complaints Vol vs Relief", "Circle", None),
    ]
    for name, mark, label in sidebar_sheets:
        add_worksheet(name)
        if mark == "Text":
            configure_chart(worksheet_name=name, mark_type="Text", label=label)
        elif mark == "Bar":
            configure_chart(
                worksheet_name=name,
                mark_type="Bar",
                rows=["Segment"],
                columns=["SUM(Sales)"],
            )
        elif mark == "Circle":
            configure_chart(
                worksheet_name=name,
                mark_type="Circle",
                rows=["Region"],
                columns=["SUM(Sales)"],
                size="SUM(Profit)",
            )
        print(f"  [OK] {name}")

    # Right-side KPI row
    kpi_sheets = [
        ("Total Complaints KPI", "SUM(Quantity)"),
        ("Timely Response KPI", "SUM(Discount)"),
        ("Closed with Monetary Relief KPI", "SUM(Profit)"),
    ]
    for name, label in kpi_sheets:
        add_worksheet(name)
        configure_chart(worksheet_name=name, mark_type="Text", label=label)
        print(f"  [OK] {name}")

    # YES section
    yes_sheets = [
        ("YES Count and Relief", "Text", "SUM(Sales)"),
        ("YES Top 10 Issues", "Bar", None),
        ("YES Complaint Details", "Text", "SUM(Quantity)"),
    ]
    for name, mark, label in yes_sheets:
        add_worksheet(name)
        if mark == "Text":
            configure_chart(worksheet_name=name, mark_type="Text", label=label)
        else:
            configure_chart(
                worksheet_name=name,
                mark_type="Bar",
                rows=["Sub-Category"],
                columns=["SUM(Sales)"],
                sort_descending="SUM(Sales)",
            )
        print(f"  [OK] {name}")

    # NO section
    no_sheets = [
        ("NO Count and Relief", "Text", "SUM(Sales)"),
        ("NO Top 10 Issues", "Bar", None),
        ("NO Complaint Details", "Text", "SUM(Quantity)"),
    ]
    for name, mark, label in no_sheets:
        add_worksheet(name)
        if mark == "Text":
            configure_chart(worksheet_name=name, mark_type="Text", label=label)
        else:
            configure_chart(
                worksheet_name=name,
                mark_type="Bar",
                rows=["Sub-Category"],
                columns=["SUM(Profit)"],
                sort_descending="SUM(Profit)",
            )
        print(f"  [OK] {name}")

    # 3. Collect all worksheet names
    all_ws = (
        [n for n, _, _ in sidebar_sheets]
        + [n for n, _ in kpi_sheets]
        + [n for n, _, _ in yes_sheets]
        + [n for n, _, _ in no_sheets]
    )

    result = add_dashboard(
        dashboard_name="Complaints Insights",
        worksheet_names=all_ws,
        width=1500,
        height=800,
        layout=layout_path,
    )
    print(f"\n  Dashboard: {result}")

    # 4. Save
    result = save_workbook(output_path)
    print(f"  Saved: {result}\n")


def main():
    build_dashboard1()
    build_dashboard2()
    print("=" * 60)
    print("ALL SCREENSHOT-TO-LAYOUT TESTS PASSED!")
    print("Open output/screenshot_dashboard1.twb and")
    print("     output/screenshot_dashboard2.twb in Tableau Desktop.")
    print("=" * 60)


if __name__ == "__main__":
    main()
