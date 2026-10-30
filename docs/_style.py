"""One palette for the pages and the figures: Furo's variables and Matplotlib's, light and dark."""

BRAND = ("#0e7c6b", "#3cc8b0")  # esapp teal, beside pyvectorfit's copper and latkit's blue
SERIES = (  # figures' series, in this order: separable in color-vision deficiency, light and dark
    ("#008571", "#13a58c"),  # teal (the first three stay apart pairwise: scatter takes three)
    ("#c95f26", "#d36a33"),  # copper
    ("#3768d4", "#4f86e8"),  # blue
    ("#b38600", "#a8830c"),  # gold (lines only: beside copper it needs a neighbor between)
)
RAMP = (  # magnitude, one hue: from near the page (nothing) to the strongest
    ("#d6eee8", "#7ccfbf", "#008571", "#00453a"),
    ("#173a34", "#0f6e5f", "#13a58c", "#a3e8d9"),
)
NEUTRAL = {  # latkit's neutrals, the same on every companion site
    "ink": ("#1b1f24", "#e6e9ed"),
    "secondary": ("#4a525c", "#b3bac3"),
    "muted": ("#6b737d", "#8a929c"),
    "border": ("#c4cad1", "#3a4048"),
    "rule": ("#e9ecef", "#23272c"),
    "surface": ("#ffffff", "#131416"),
    "panel": ("#f6f8fa", "#1a1c1f"),
}


def palette(theme: str) -> dict[str, str]:
    """Furo's colors for ``theme``, "light" or "dark"."""
    i = theme == "dark"
    c = {role: pair[i] for role, pair in NEUTRAL.items()}
    return {
        "color-brand-primary": BRAND[i],
        "color-brand-content": BRAND[i],
        "color-brand-visited": BRAND[i],
        "color-foreground-primary": c["ink"],
        "color-foreground-secondary": c["secondary"],
        "color-foreground-muted": c["muted"],
        "color-foreground-border": c["border"],
        "color-background-primary": c["surface"],
        "color-background-secondary": c["panel"],
        "color-background-border": c["rule"],
        "color-api-name": c["ink"],
        "color-api-pre-name": c["secondary"],
        "color-code-background": c["panel"],
        "color-inline-code-background": c["panel"],
    }


def rc(theme: str) -> dict[str, object]:
    """Matplotlib settings that set a figure on the page in ``theme``: the capture's alone.

    Examples choose no colors: series take ``SERIES`` in order, and color scales ``RAMP``.
    """
    import matplotlib as mpl
    from cycler import cycler
    from matplotlib.colors import LinearSegmentedColormap

    i = theme == "dark"
    c = {role: pair[i] for role, pair in NEUTRAL.items()}
    ramp = f"esapp-{theme}"
    if ramp not in mpl.colormaps:
        mpl.colormaps.register(LinearSegmentedColormap.from_list(ramp, RAMP[i]))
    return {
        "figure.facecolor": "none",
        "axes.facecolor": "none",
        "savefig.transparent": True,
        "svg.fonttype": "none",
        "svg.hashsalt": "esapp",  # the same figure writes the same file
        "font.size": 10,
        "text.color": c["ink"],
        "axes.labelcolor": c["secondary"],
        "axes.edgecolor": c["border"],
        "axes.grid": True,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.prop_cycle": cycler(color=[pair[i] for pair in SERIES]),
        "axes.titlesize": 10,
        "axes.titlecolor": c["secondary"],
        "image.cmap": ramp,
        "grid.color": c["rule"],
        "grid.linewidth": 0.6,
        "xtick.color": c["muted"],
        "ytick.color": c["muted"],
        "legend.frameon": False,
        "legend.labelcolor": c["secondary"],
        "polaraxes.grid": True,
        "lines.linewidth": 0.9,
        "scatter.edgecolors": "none",
    }
