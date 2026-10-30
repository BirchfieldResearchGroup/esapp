"""Sphinx configuration: Furo in esa++'s colors, MyST pages, and examples captured on PowerWorld."""

import os
import sys
from importlib import metadata
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE / "_ext")]

from _style import palette  # noqa: E402

project = "esa++"
author = "Luke Lowery"
copyright = "2026, Luke Lowery"
release = metadata.version("esapp")

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx_copybutton",
    "esapp_docs",
]
exclude_patterns = ["_build", "examples/_output"]
myst_enable_extensions = ["colon_fence", "deflist"]
myst_heading_anchors = 3

autodoc_member_order = "bysource"
autodoc_typehints = "signature"
autodoc_typehints_format = "short"
autodoc_preserve_defaults = True
autodoc_inherit_docstrings = False
add_module_names = False
python_maximum_signature_line_length = 88  # longer signatures take a line per parameter
python_display_short_literal_types = True
toc_object_entries_show_parents = "hide"
nitpicky = True
nitpick_ignore_regex = [
    (r"py:.*", r"(?!esapp).*"),  # only our own references must resolve
    (r"py:class", r"esapp\.grid\..*"),  # generated object types: {objects} lists them
    (
        r"py:class",
        r"esapp\..*\.(StrPath|FieldLike|FieldSpec|Select|Allocate|Block|Pair|Values|Rows|Cols)",
    ),
]
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable", None),
    "pandas": ("https://pandas.pydata.org/docs", None),
}
# What an example printed is not code to copy.
copybutton_selector = "div:not(.output) > div.highlight > pre"

GITHUB = (
    '<svg viewBox="0 0 16 16" fill="currentColor"><path fill-rule="evenodd" d="M8 0C3.58 0 0 '
    "3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53"
    "-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21"
    " 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08"
    "-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04"
    " 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29"
    ".25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42"
    '-3.58-8-8-8z"/></svg>'
)

esapp_source = "https://github.com/BirchfieldResearchGroup/esapp/blob/main/src"

html_theme = "furo"
html_title = "esa++"
pygments_style = "github-light"
pygments_dark_style = "github-dark"
html_static_path = ["_static"]
html_extra_path = ["_extra"]  # llms.txt, at the root of the site
html_css_files = ["custom.css"]
html_favicon = "_static/favicon.svg"
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "")  # each page's canonical link
html_copy_source = False
html_show_sourcelink = False
html_use_index = False
html_domain_indices = False
html_theme_options = {
    "light_css_variables": palette("light"),
    "dark_css_variables": palette("dark"),
    "light_logo": "logo.svg",
    "dark_logo": "logo-dark.svg",
    "top_of_page_buttons": [],
    "navigation_with_keys": True,
    "footer_icons": [
        {
            "name": "GitHub",
            "url": "https://github.com/BirchfieldResearchGroup/esapp",
            "html": GITHUB,
        }
    ],
}


def page_title(app, pagename, templatename, context, doctree):
    """What search results show: the sidebar's short names are the same in Examples and API."""
    if pagename == "index":
        context["title"], context["docstitle"] = "esa++: Python for PowerWorld", ""
    elif pagename.startswith("examples/"):
        context["title"] += " examples" if pagename.endswith("/index") else " example"
    elif pagename.startswith("api/"):
        context["title"] += " API"


def setup(app):
    app.connect("html-page-context", page_title)
