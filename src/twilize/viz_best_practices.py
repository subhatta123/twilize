"""Curated Tableau visualization best practices.

Distilled from:
- Tableau Official Help & Whitepapers (Visual Best Practices)
- Andy Cotgreave (Tableau Zen Master, "Big Book of Dashboards")
- Storytelling with Data (Cole Nussbaumer Knaflic)
- The Data School (Information Lab)
- VizWiz (Andy Kriebel, Tableau Zen Master)
- Playfair Data (Ryan Sleeper, Tableau Zen Master)
- Senturus (BI Best Practices)
- phData (Dashboard Design Guidelines)
- Tableau Visual Vocabulary / Chart Chooser

This module provides:

* ``BEST_PRACTICES_PROMPT`` — embeddable text for LLM system prompts
* ``DATA_PATTERN_CHART_MAP`` — maps analytical patterns to chart types
* ``KPI_GUIDELINES``, ``LAYOUT_GUIDELINES``, ``MAP_GUIDELINES``,
  ``CHART_SELECTION_GUIDELINES``, ``COLOR_GUIDELINES``,
  ``SCATTER_PLOT_GUIDELINES``, ``BAR_CHART_GUIDELINES``,
  ``LINE_CHART_GUIDELINES`` — individual guideline blocks
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Nine data-analysis patterns -> recommended chart types
# ---------------------------------------------------------------------------

DATA_PATTERN_CHART_MAP: dict[str, dict] = {
    "change_over_time": {
        "description": "How has a value changed over time?",
        "best_charts": ["Line", "Area"],
        "avoid": ["Pie", "Bar"],
        "requires": ["temporal", "measure"],
        "notes": (
            "Line is almost always best for temporal trends. "
            "Use Area for cumulative or stacked compositions. "
            "Ensure the time axis is continuous (not discrete) for smooth trends. "
            "Use a consistent date grain (day/week/month/quarter). "
            "Add color for categorical breakdown only if ≤6 series."
        ),
    },
    "ranking": {
        "description": "Which items are highest/lowest?",
        "best_charts": ["Bar"],
        "avoid": ["Pie", "Line"],
        "requires": ["categorical", "measure"],
        "notes": (
            "Horizontal bar, sorted descending by the measure. "
            "Best with 5-15 categories. For >15 categories, show only Top N. "
            "Always start the axis at zero. "
            "Pre-attentive: highlight the top/bottom bar with a contrasting color."
        ),
    },
    "part_to_whole": {
        "description": "What proportion does each part contribute?",
        "best_charts": ["Pie", "Bar"],
        "avoid": ["Line", "Scatterplot"],
        "requires": ["categorical", "measure"],
        "notes": (
            "Pie ONLY with ≤5 categories. Otherwise use stacked/100% bar. "
            "Slices must sum to 100%. Start the largest slice at 12 o'clock. "
            "Stacked bars are better when comparing proportions across groups. "
            "Never use 3D pie charts — they distort proportions."
        ),
    },
    "correlation": {
        "description": "Is there a relationship between two values?",
        "best_charts": ["Scatterplot"],
        "avoid": ["Pie", "Bar"],
        "requires": ["measure", "measure"],
        "notes": (
            "Needs ≥15 distinct visual data points for meaningful patterns. "
            "When using color for categorical breakdown, ensure the dimension "
            "has enough cardinality (≥6 unique values) to show distinct clusters. "
            "A scatter plot with only 3 colored points is a bad chart — use "
            "a grouped bar chart instead. "
            "Add a trend line to clarify the relationship. "
            "Use size encoding for a third measure sparingly."
        ),
    },
    "magnitude": {
        "description": "How large are the values compared to each other?",
        "best_charts": ["Bar", "Tree Map"],
        "avoid": ["Pie"],
        "requires": ["categorical", "measure"],
        "notes": (
            "Bar for few categories (<15), Treemap for many (>10). "
            "Always start bar axis at zero — truncated axes mislead. "
            "Treemaps are hard to read precisely — use for overview only."
        ),
    },
    "deviation": {
        "description": "How do values differ from a baseline/average?",
        "best_charts": ["Bar"],
        "avoid": ["Pie"],
        "requires": ["categorical", "measure"],
        "notes": (
            "Diverging bar centered on zero or average. "
            "Use orange-blue diverging palette (colorblind-safe). "
            "Add a reference line at the baseline value."
        ),
    },
    "distribution": {
        "description": "How are values spread across a range?",
        "best_charts": ["Heatmap", "Bar"],
        "avoid": ["Pie", "Line"],
        "requires": ["measure"],
        "notes": (
            "Histogram for single field, box plot for groups. "
            "Heatmap is effective when you have two categorical dims "
            "and want to show intensity."
        ),
    },
    "spatial": {
        "description": "Where are values concentrated geographically?",
        "best_charts": ["Map"],
        "avoid": [],
        "requires": ["geographic", "measure"],
        "notes": (
            "ONLY use when location is central to the question. "
            "Filled map for regions, symbol map for points. "
            "If >20% values are null/unknown, use Bar chart instead. "
            "Maps use a LOT of dashboard real estate for limited data density — "
            "a simple bar chart sorted by geography often communicates better. "
            "Assign correct geographic role (State, City, Zip Code, Country)."
        ),
    },
    "kpi_summary": {
        "description": "What is the key metric value?",
        "best_charts": ["Text"],
        "avoid": [],
        "requires": ["measure"],
        "notes": (
            "BAN (Big Ass Number) pattern: 28pt+ Tableau Book font, center-aligned. "
            "Include contextual comparison (vs prior period, vs target). "
            "Color coding: green (#2E7D32) for positive, red (#C62828) for negative. "
            "Custom number format: '▲ #,###.0%;▼ -#,###.0%' for change indicators. "
            "Use muted gray (#666666) 14pt label beneath the number."
        ),
    },
}

# ---------------------------------------------------------------------------
# Individual guideline blocks — distilled from Tableau community expertise
# ---------------------------------------------------------------------------

KPI_GUIDELINES = """\
## KPI / Big Ass Number (BAN) Design Rules
(Source: Playfair Data, VizWiz, Tableau Official Whitepapers)

### Typography
- Primary number: Tableau Book, 28-36pt, bold, center-aligned
- Label/subtitle: Tableau Book, 14pt, muted gray (#666666), below the number
- Never use decorative fonts — clean sans-serif only
- Number formatting: use thousand separators (#,##0) and appropriate decimal places

### Color Coding
- Positive trend: green (#2E7D32) or teal (#00897B)
- Negative trend: red (#C62828) or orange (#E65100)
- Neutral: dark gray (#333333)
- Use Orange-Blue Diverging palette for colorblind accessibility

### Context is Everything
- A number alone is meaningless — always show comparison context:
  * vs prior period: "▲ 12.5% vs last quarter"
  * vs target: "85% of $1M goal"
  * vs benchmark: "3.2x industry average"
- Custom Tableau format string: '▲ #,###.0%;▼ -#,###.0%'
- Trend arrows (▲▼) are more intuitive than +/- signs

### Layout
- KPIs occupy 15-20% of total dashboard height (top row)
- Maximum 3-4 KPIs per dashboard — more dilutes impact
- Arrange left-to-right by business importance
- Each KPI gets equal horizontal space
- Fixed height 300-400px for BAN visibility
- White or very light background to make numbers pop
"""

LAYOUT_GUIDELINES = """\
## Dashboard Layout Rules
(Source: Tableau Official "Visual Best Practices", The Data School)

### The Five-Second Rule
- A viewer should grasp the main insight within 5 seconds of looking
- This means: KPI/BAN at top, primary viz in center, filters on side

### Z-Pattern (Natural Reading Flow)
- Most important insight in upper-left quadrant
- Secondary insight in upper-right
- Supporting detail in lower-left
- Call-to-action or deep-dive in lower-right
- Western audiences scan left→right, top→bottom (Z-shape)

### Dashboard Sizing
- Fixed size: 1366×768px (most common monitor) or 1920×1080px
- Automatic sizing causes layout shifts — avoid for production dashboards
- For embedded: use the container width minus padding

### Chart Count & Spacing
- Maximum 2-3 analytical views plus KPIs (5 total max)
- More views = less impact per view (cognitive overload)
- Consistent margins: 8px inner padding between zones
- White space is GOOD — don't fill every pixel
- Dashboard title at top only if needed (KPIs can serve as implicit title)

### Interactivity
- Add 1-3 quick filters for key categorical dimensions
- Cardinality 3-20 is ideal for dropdown/radio-button filters
- Use filter actions from bar/pie charts to cross-filter other views
- Add a highlight action for hover interactivity across worksheets
"""

MAP_GUIDELINES = """\
## Map Usage Rules
(Source: Tableau Official, Andy Cotgreave, Storytelling with Data)

### When to Use Maps
- ONLY when location is CENTRAL to the analytical question
- Good: "Where are our customers concentrated?" "Which regions underperform?"
- Bad: "Sales by Region" — a sorted bar chart is ALWAYS better for this

### When NOT to Use Maps
- When a bar chart would show the same data more clearly (most cases!)
- When geographic data quality is poor (>20% null/unknown values)
- When you have only 2-3 geographic regions (bar chart is clearer)
- When precise value comparison matters (position on a bar is easier to read than shade on a map)

### Map Design Rules
- Filled (choropleth) maps: for regions (country, state, province)
- Symbol maps: for point locations (city, lat/lon, store address)
- Use sequential palette (light→dark) for continuous measures
- Always assign correct geographic role in Tableau (State, City, Zip Code)
- Include a legend — never assume viewers can interpret color intensity
- Maps consume massive dashboard real estate for limited data density
"""

BAR_CHART_GUIDELINES = """\
## Bar Chart Best Practices
(Source: Tableau Visual Best Practices, Cole Nussbaumer Knaflic)

- Horizontal bars are better for category labels (easier to read)
- ALWAYS sort descending by the measure (don't use alphabetical order)
- ALWAYS start the axis at zero — truncated axes mislead
- Use one consistent color; highlight only the top/bottom bar
- For negative values, use diverging color (orange for negative, blue for positive)
- Limit to ≤15 categories; for more, show Top N or use a treemap
- Gap width: bars should be wider than the gaps between them
- Add data labels only if precision matters; otherwise let the axis do the work
"""

LINE_CHART_GUIDELINES = """\
## Line Chart Best Practices
(Source: Tableau Official, The Data School, Storytelling with Data)

- Use for continuous temporal data ONLY — never for categorical
- Use a continuous (not discrete) date axis for smooth trends
- Limit color series to ≤6 lines — more becomes spaghetti
- Thicken the "hero" line (the one you want to highlight)
- Add a reference line for benchmarks/targets/averages
- Don't use markers unless the data points are sparse (<10)
- Area chart variant: only for cumulative/stacked compositions
"""

SCATTER_PLOT_GUIDELINES = """\
## Scatter Plot Best Practices
(Source: Tableau Visual Vocabulary, Storytelling with Data)

### When to Use
- Exploring correlation/relationship between two continuous measures
- Need ≥15 distinct visual data points for meaningful patterns
- When using color for categorical breakdown, ensure the dimension
  has enough cardinality (≥6 unique values) for distinct clusters

### When NOT to Use
- When a categorical dimension used as color has very low cardinality
  (e.g., 3 categories → only 3 dots → use grouped bar instead)
- When the relationship is obvious from other charts
- When you need to show exact values (bar chart is better)

### Design Rules
- Add a trend line to clarify the relationship direction
- Use size encoding for a third measure (sparingly — max 3 visual channels)
- Label outliers, not every point
- Include quadrant lines if there's a natural threshold (e.g., above/below average)
"""

COLOR_GUIDELINES = """\
## Color & Palette Guidelines
(Source: Tableau Official, Storytelling with Data, Andy Cotgreave)

### Palette Selection
- Categorical data: use Tableau 10 palette (designed for accessibility)
- Sequential data: use a single-hue gradient (light→dark blue)
- Diverging data: use Orange-Blue Diverging (colorblind-safe)
- Never use more than 7 distinct colors — beyond that, use shape/label

### Color Principles
- Use color intentionally — it should encode meaning, not decoration
- Gray is your best friend: default everything to gray, highlight what matters
- Pre-attentive color: one bold color against gray background draws the eye
- Avoid red/green together (8% of men are red-green colorblind)
- Use Orange-Blue Diverging instead of Red-Green

### Accessibility
- Test with colorblind simulator tools
- Don't rely on color alone — add labels, patterns, or tooltips
- Ensure sufficient contrast (WCAG AA: 4.5:1 ratio)

### Dashboard Background
- Light gray (#F5F5F5) or white (#FFFFFF) backgrounds work best
- Dark dashboards: use carefully, ensure text readability
- Avoid busy background patterns/images
"""

PIE_CHART_GUIDELINES = """\
## Pie Chart Rules (Strict)
(Source: Tableau Community, Cole Nussbaumer Knaflic)

- MAXIMUM 5 slices (categories). Period.
- Slices MUST sum to 100% (part-to-whole relationship)
- Start the largest slice at 12 o'clock position
- Never use 3D pie charts — they distort proportions
- Never use two pie charts side-by-side for comparison (use stacked bar)
- If in doubt, use a horizontal bar chart instead — it's almost always better
- Pie charts are the MOST overused and LEAST effective chart type
"""

TOOLTIP_GUIDELINES = """\
## Tooltip Design
(Source: Playfair Data, The Data School)

- Show: the dimension value, the measure value with proper formatting, context
- Don't dump every field into the tooltip — curate the information
- Use formatted numbers (commas, currency symbols, percentages)
- Add a sentence of insight: "This is 15% above the average"
- Tooltip viz: embed small sparklines or bar charts for extra context
"""

# ---------------------------------------------------------------------------
# Combined prompt for LLM injection
# ---------------------------------------------------------------------------

BEST_PRACTICES_PROMPT = f"""\
=== TABLEAU VISUALIZATION BEST PRACTICES ===
(Curated from Tableau Official Docs, Andy Cotgreave, Ryan Sleeper/Playfair Data,
Cole Nussbaumer Knaflic/Storytelling with Data, The Data School, VizWiz)

{KPI_GUIDELINES}

{LAYOUT_GUIDELINES}

{MAP_GUIDELINES}

{BAR_CHART_GUIDELINES}

{LINE_CHART_GUIDELINES}

{SCATTER_PLOT_GUIDELINES}

{PIE_CHART_GUIDELINES}

{COLOR_GUIDELINES}

## Aggregation Rules (CRITICAL — choosing wrong aggregation ruins the insight)
- Rates/percentages/scores (discount, margin, score, satisfaction, conversion,
  rating, retention, churn, yield, efficiency, average) → AVG
  Rationale: summing a rate is meaningless (sum of discounts ≠ total discount rate)
- Amounts/values (sales, profit, revenue, cost, price, amount, budget, spend,
  income, expense) → SUM
- Counts/quantities (quantity, orders, number, transactions, visits, clicks) → SUM
- Identifiers (order_id, customer_key, product_code) → COUNTD (count distinct)
  Rationale: counting unique IDs answers "how many unique X" questions

## Critical Dashboard Rules
- Maximum 5 charts per dashboard (including KPIs)
- Always include 1-2 KPI/BAN charts for the most important metrics
- KPIs go at the TOP of the layout in a horizontal row
- Every chart must answer a specific analytical question
- Chart titles should BE the question: "Which Category is most profitable?"
- Sort bar charts by the measure (descending), never alphabetically
- Start bar chart axes at zero
- Use Line for time series, NEVER bar charts for temporal data
- Scatter plots need ≥15 distinct visual data points to be meaningful
- If a scatter plot would show only 3-5 aggregated dots, use a bar chart instead
- Maps: ONLY if location is central to the analysis AND >80% of geo values are valid
- Pie charts: ONLY if ≤5 categories, NEVER more
"""
