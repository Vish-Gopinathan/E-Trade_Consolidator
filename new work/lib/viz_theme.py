"""
Chart colour and styling constants.

The categorical order below is fixed and validated: the eight hues clear the
colourblind-separation, normal-vision and lightness gates in both light and dark
mode when adjacent slots are compared, which is the case that matters for stacked
areas and lines. The *ordering* is the safety mechanism, so slots are assigned in
sequence and never shuffled or cycled — a ninth series folds into "Other" rather
than borrowing a hue that is already carrying meaning.

Three light-mode slots sit below 3:1 contrast against the surface. That is
allowed only with relief, which the history page provides as a value table beside
every chart.
"""

# Slot order: blue, orange, aqua, yellow, magenta, green, violet, red
CATEGORICAL_LIGHT = [
    '#2a78d6', '#eb6834', '#1baf7a', '#eda100',
    '#e87ba4', '#008300', '#4a3aa7', '#e34948',
]
CATEGORICAL_DARK = [
    '#3987e5', '#d95926', '#199e70', '#c98500',
    '#d55181', '#008300', '#9085e9', '#e66767',
]

SURFACE_LIGHT = '#fcfcfb'
SURFACE_DARK = '#1a1a19'

# "Other" is a pooled remainder, not an entity — it takes neutral ink so it never
# competes with a real series for attention.
OTHER_LIGHT = '#b6b5ae'
OTHER_DARK = '#6b6a63'

GRID_LIGHT = 'rgba(11,11,11,0.08)'
GRID_DARK = 'rgba(255,255,255,0.10)'

REFERENCE_LIGHT = '#8a8981'   # cost-basis / annotation line
REFERENCE_DARK = '#9d9c93'


def is_dark() -> bool:
    """Whether Streamlit is currently rendering in dark mode."""
    import streamlit as st
    try:
        theme_type = st.context.theme.type
        if theme_type:
            return str(theme_type).lower() == 'dark'
    except Exception:
        pass
    try:
        return str(st.get_option('theme.base') or '').lower() == 'dark'
    except Exception:
        return False


def palette(dark: bool | None = None) -> dict:
    """All colour roles for the active theme."""
    dark = is_dark() if dark is None else dark
    return {
        'categorical': CATEGORICAL_DARK if dark else CATEGORICAL_LIGHT,
        'surface': SURFACE_DARK if dark else SURFACE_LIGHT,
        'other': OTHER_DARK if dark else OTHER_LIGHT,
        'grid': GRID_DARK if dark else GRID_LIGHT,
        'reference': REFERENCE_DARK if dark else REFERENCE_LIGHT,
        'primary': (CATEGORICAL_DARK if dark else CATEGORICAL_LIGHT)[0],
        'dark': dark,
    }


def style_stacked_area(fig, colours: dict, surface: str):
    """
    Give each band its own fill and a surface-coloured edge.

    Plotly derives an area's fill from its line colour, so recolouring the line
    to create the separating gap also blanks the fill (and the legend swatch with
    it). Setting ``fillcolor`` explicitly first keeps the band and the legend
    while the 2px edge does its job of holding neighbouring hues apart.
    """
    for trace in fig.data:
        colour = colours.get(trace.name)
        if colour:
            trace.fillcolor = colour
        trace.line.width = 2
        trace.line.color = surface
    return fig


def colour_map(display_categories, universe_ranking, colours, other_colour) -> dict:
    """
    Assign a hue to each category shown, keyed by identity rather than position.

    A category's slot comes from its rank across the *whole* dimension, so hiding
    one series never repaints the others — the survivors keep the colours they
    had. Categories outside the top slots have no reserved hue (they normally
    live inside "Other") and only get one when a filter surfaces them, taking
    whichever slots are still free.
    """
    n = len(colours)
    top = [c for c in universe_ranking[:n]]
    assigned, used = {}, set()

    for cat in display_categories:
        if cat in top:
            slot = top.index(cat)
            assigned[cat] = colours[slot]
            used.add(slot)

    free = [i for i in range(n) if i not in used]
    for cat in display_categories:
        if cat in assigned:
            continue
        if cat == 'Other':
            assigned[cat] = other_colour
        elif free:
            assigned[cat] = colours[free.pop(0)]
        else:
            assigned[cat] = other_colour
    return assigned
