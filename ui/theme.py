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

# ── Diverging pair: gain vs loss ──────────────────────────────────────────────
#
# Two hues around a neutral midpoint, drawn from the categorical slots above so
# the whole app stays one system. Green-up / red-down is the convention every
# reader of a brokerage statement already has, and overriding it would cost more
# comprehension than it bought.
#
# The cost is measured, not assumed: the validator puts this pair at ΔE 7.2 for
# protanopia, inside the 6–8 band that is permitted **only** alongside a second,
# non-colour encoding. Sweeping the green ramp did not beat it — red against green
# tops out there — so the second encoding is not optional here. Every chart using
# these colours must also carry:
#
#   * position — bars diverge above and below a zero line, and
#   * direct labels — the signed value sits on each mark, and
#   * a table of the same numbers on the page.
#
# Remove those and the chart stops being readable for roughly 1 in 12 men.
POSITIVE_LIGHT = '#008300'
POSITIVE_DARK = '#008300'
NEGATIVE_LIGHT = '#e34948'
NEGATIVE_DARK = '#e66767'

#: Midpoint of the diverging scale, and the fill for values too small to mean
#: anything. Deliberately desaturated: a neutral must not read as a third series.
NEUTRAL_LIGHT = '#b6b5ae'
NEUTRAL_DARK = '#6b6a63'


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
        'positive': POSITIVE_DARK if dark else POSITIVE_LIGHT,
        'negative': NEGATIVE_DARK if dark else NEGATIVE_LIGHT,
        'neutral': NEUTRAL_DARK if dark else NEUTRAL_LIGHT,
        'dark': dark,
    }


def apply_layout(fig, colours: dict, *, y_prefix: str = '', y_suffix: str = ''):
    """
    Apply the shared chart chrome: transparent surface, recessive grid, no
    vertical rules, legend above the plot.

    Every chart in the app goes through here so they read as one system rather
    than as eight separately-styled figures.
    """
    fig.update_layout(
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        margin=dict(t=10, b=10, l=0, r=10),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, x=0, title_text=''),
        hoverlabel=dict(bgcolor=colours['surface'], font_size=12),
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(
        gridcolor=colours['grid'], zeroline=False,
        tickprefix=y_prefix, ticksuffix=y_suffix,
    )
    return fig


def allocation_colours(labels, colours: dict) -> dict:
    """
    Map allocation slice labels to hues by identity, never by position.

    Plotly's ``color_discrete_sequence`` recycles once the labels outrun the
    sequence, so a 9th slice silently takes slot 0's hue — which is how a Cash
    slice ended up the same blue as the largest holding. Returning an explicit
    map means a label can only ever get the colour meant for it.

    ``Other`` and ``Cash`` are not holdings, so they take neutral ink and never
    compete with a real position; they use different neutrals so the two remain
    distinguishable from each other.
    """
    hues = colours['categorical']
    mapping, slot = {}, 0
    for label in labels:
        if label == 'Other':
            mapping[label] = colours['other']
        elif label == 'Cash':
            mapping[label] = colours['reference']
        elif slot < len(hues):
            mapping[label] = hues[slot]
            slot += 1
        else:
            # Past the validated slots there is no safe hue left; fold to neutral
            # rather than inventing one or reusing a hue that carries meaning.
            mapping[label] = colours['other']
    return mapping


def signed_colours(values, colours: dict, neutral_band: float = 0.0) -> list:
    """
    One colour per value on the diverging scale.

    Values inside ``±neutral_band`` take the neutral hue: a 0.4% difference is
    noise, and painting it green implies a signal that is not there.
    """
    out = []
    for value in values:
        if value is None or abs(value) <= neutral_band:
            out.append(colours['neutral'])
        else:
            out.append(colours['positive'] if value > 0 else colours['negative'])
    return out


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
