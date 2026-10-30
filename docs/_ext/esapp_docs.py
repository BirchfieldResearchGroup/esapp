"""esapp's Sphinx directives: examples as captured on PowerWorld, and the generated catalogs.

``{example} name.py``  the script (less its docstring), what it printed, then its figures
``{gallery}``          examples as cards, one name a line: a figure (or the code), and its line
``{commands}``         every script command in esapp.commands, by section
``{recordable}``       every field in esapp.ts.TS, by group
``{objects}``          every object type in esapp.grid, with its keys
``{api} module``       what a module's ``__all__`` exports, through autodoc
``typealias``          a ``type X = ...`` alias as the py:type it is, which autodoc cannot do

Read the Docs cannot run PowerWorld, so an example shows its capture (docs/capture.py), and
an example changed since its capture fails the build.
"""

import ast
import importlib
import inspect
import json
from collections.abc import Sequence
from dataclasses import fields
from itertools import pairwise
from pathlib import Path
from textwrap import indent
from typing import Any, ClassVar, TypeAliasType

from docutils import nodes
from docutils.parsers.rst import Parser, directives
from sphinx.application import Sphinx
from sphinx.pycode import ModuleAnalyzer
from sphinx.util import logging
from sphinx.util.docutils import SphinxDirective, new_document
from sphinx.util.osutil import relative_uri
from sphinx.util.typing import ExtensionMetadata, OptionSpec, stringify_annotation

from _examples import EXAMPLES, OUTPUT, digest

log = logging.getLogger(__name__)


def body(source: str) -> str:
    """The script after its docstring, which its page already says."""
    first = ast.parse(source).body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        source = "\n".join(source.splitlines()[first.end_lineno :])
    return source.strip("\n") + "\n"


class Example(SphinxDirective):
    required_arguments = 1
    option_spec: ClassVar[OptionSpec] = {"alt": directives.unchanged_required}  # figures' text

    def run(self) -> list[nodes.Node]:
        script = Path(self.env.doc2path(self.env.docname)).parent / self.arguments[0]
        record = OUTPUT / f"{script.stem}.json"
        for path in (script, record):
            self.env.note_dependency(str(path))
        captured = json.loads(record.read_text(encoding="utf-8"))
        if captured["sha256"] != digest(script):
            log.warning(
                "%s changed since its capture: run `python docs/capture.py %s` where PowerWorld is",
                script.name,
                script.stem,
                location=self.get_location(),
            )
        code = body(script.read_text(encoding="utf-8"))
        out: list[nodes.Node] = [nodes.literal_block(code, code, language="python")]
        if printed := captured["stdout"]:
            out.append(nodes.literal_block(printed, printed, language="text", classes=["output"]))
        for n in range(1, captured["figures"] + 1):
            for suffix, theme in (("", "only-light"), ("-dark", "only-dark")):
                uri = f"/examples/_output/{script.stem}-{n}{suffix}.svg"
                alt = self.options.get("alt", "")
                out.append(nodes.image(uri=uri, alt=alt, classes=[theme, "figure"]))
        return out


class Gallery(SphinxDirective):
    """Each example named in the content as a card linking to its page: its first figure, or
    the opening of its code, then its docstring's title and line."""

    has_content = True

    def run(self) -> list[nodes.Node]:
        cards: list[nodes.Node] = []
        for stem in filter(None, map(str.strip, self.content)):
            script = EXAMPLES / f"{stem}.py"
            record = OUTPUT / f"{script.stem}.json"
            for path in (script, record):
                self.env.note_dependency(str(path))
            source = script.read_text(encoding="utf-8")
            title, _, line = (ast.get_docstring(ast.parse(source)) or stem).partition("\n\n")
            page = f"examples/{stem}{self.config.html_file_suffix or '.html'}"
            link = nodes.reference("", "", internal=True, refuri=relative_uri(self.here, page))
            if json.loads(record.read_text(encoding="utf-8"))["figures"]:
                for suffix, theme in (("", "only-light"), ("-dark", "only-dark")):
                    uri = f"/examples/_output/{script.stem}-1{suffix}.svg"
                    link += nodes.image(uri=uri, alt="", classes=[theme, "preview"])
            else:
                opening = "\n".join(body(source).splitlines()[:12])
                link += nodes.inline(opening, opening, classes=["preview", "code"])
            link += nodes.inline(title, title, classes=["card-title"])
            link += nodes.inline(line, " ".join(line.split()), classes=["card-line"])
            cards.append(nodes.paragraph("", "", link, classes=["card"]))
        return [nodes.container("", *cards, classes=["gallery"])]

    @property
    def here(self) -> str:
        return f"{self.env.docname}{self.config.html_file_suffix or '.html'}"


class Commands(SphinxDirective):
    def run(self) -> list[nodes.Node]:
        from esapp.commands import Command, _catalog

        sections: dict[str, list[type[Command]]] = {}
        for name in _catalog.__all__:
            if isinstance(value := getattr(_catalog, name), type) and issubclass(value, Command):
                sections.setdefault(value.section, []).append(value)
        out: list[nodes.Node] = []
        for section, commands in sorted(sections.items()):
            out.append(nodes.rubric(text=section))
            rows = [
                (
                    c.name.lower(),
                    literal(c.name),
                    text(", ".join(f.name for f in fields(c))),
                    text(lead(c.__doc__)),
                )
                for c in commands
            ]
            out.append(table(("Command", "Parameters", ""), rows))
        return out


class Recordable(SphinxDirective):
    def run(self) -> list[nodes.Node]:
        from esapp.ts import TS
        from esapp.ts._recordable import Group

        docs = ModuleAnalyzer.for_module("esapp.ts._catalog").find_attr_docs()
        out: list[nodes.Node] = []
        for name, group in vars(TS).items():
            if not (isinstance(group, type) and issubclass(group, Group)):
                continue
            out.append(nodes.rubric(text=f"TS.{name}"))
            mine = group.fields()
            rows = [
                (
                    f"ts-{name}-{attr}".lower(),
                    literal(f"TS.{name}.{attr}"),
                    literal(field.name),
                    text(" ".join(docs.get((f"TS.{name}", attr), []))),
                )
                for attr, field in vars(group).items()
                if any(field is f for f in mine)
            ]
            out.append(table(("Name", "Field", ""), rows))
        return out


class Objects(SphinxDirective):
    def run(self) -> list[nodes.Node]:
        grid = importlib.import_module("esapp.grid")
        rows = []
        for name in grid.__all__:
            gtype = getattr(grid, name)
            path = gtype.__module__.replace(".", "/")
            link = nodes.reference("", "fields", refuri=f"{self.config.esapp_source}/{path}.py")
            rows.append(
                (
                    f"object-{name}".lower(),
                    literal(name),
                    text(", ".join(gtype.schema.key_names)),
                    nodes.paragraph("", "", link),
                )
            )
        return [table(("Type", "Key", ""), rows)]


class Api(SphinxDirective):
    """What a module's ``__all__`` exports, in its source order, through autodoc."""

    required_arguments = 1
    option_spec: ClassVar[OptionSpec] = {
        "exceptions": directives.flag,  # only its exceptions; without this, all but them
        "exclude": directives.unchanged_required,  # documented elsewhere
    }

    def run(self) -> list[nodes.Node]:
        module = importlib.import_module(self.arguments[0])
        exported = set(module.__all__) - set(self.options.get("exclude", "").split())
        lines: list[str] = []
        for name, value in vars(module).items():
            is_exception = isinstance(value, type) and issubclass(value, BaseException)
            if name in exported and is_exception == ("exceptions" in self.options):
                lines += documented(f"{module.__name__}.{name}", value)
        document = new_document(self.state.document["source"], self.state.document.settings)
        document.reporter = self.state.document.reporter
        Parser().parse("\n".join(lines), document)  # reStructuredText, in a Markdown page too
        return document.children


def documented(name: str, value: Any) -> list[str]:
    if isinstance(value, TypeAliasType):
        return [f".. typealias:: {value.__module__}.{value.__name__}", ""]
    if isinstance(value, type) and issubclass(value, BaseException):
        return [f".. autoexception:: {name}", ""]
    if isinstance(value, type):
        lines = [f".. autoclass:: {name}", "   :members:", "   :undoc-members:"]
        if any(base.__module__.startswith("esapp.") for base in value.__bases__):
            lines.append("   :inherited-members:")
        return [*lines, ""]
    if callable(value):
        return [f".. autofunction:: {name}", ""]
    return [f".. autodata:: {name}", "   :no-value:", ""]


class TypeAlias(SphinxDirective):
    """``.. typealias:: module.Name``, in RST: ``type Name = <its value>``, and the docstring
    under it. Documented where it is defined, which is where signatures refer to it."""

    required_arguments = 1

    def run(self) -> list[nodes.Node]:
        path, _, name = self.arguments[0].rpartition(".")
        module = importlib.import_module(path)
        value = stringify_annotation(getattr(module, name).__value__, "smart")
        head = f".. py:type:: {name}\n   :module: {path}\n   :canonical: {value}\n\n"
        return self.parse_text_to_nodes(head + indent(alias_doc(module, name), "   "))


def alias_doc(module: Any, name: str) -> str:
    """The string under ``type name = ...``, which Sphinx's own source reader does not see."""
    for node, after in pairwise(ast.parse(inspect.getsource(module)).body):
        if (
            isinstance(node, ast.TypeAlias)
            and node.name.id == name
            and isinstance(after, ast.Expr)
            and isinstance(after.value, ast.Constant)
        ):
            return inspect.cleandoc(str(after.value.value))
    return ""


def table(head: Sequence[str], rows: Sequence[tuple[str, *tuple[nodes.Node, ...]]]) -> nodes.table:
    """A table whose rows are linkable: ``#<anchor>``."""
    group = nodes.tgroup(cols=len(head))
    group += [nodes.colspec(colwidth=1) for _ in head]
    group += nodes.thead("", nodes.row("", *(nodes.entry("", text(h)) for h in head)))
    group += nodes.tbody(
        "",
        *(
            nodes.row("", *(nodes.entry("", cell) for cell in cells), ids=[anchor])
            for anchor, *cells in rows
        ),
    )
    return nodes.table("", group, classes=["catalog"])


def literal(value: str) -> nodes.paragraph:
    return nodes.paragraph("", "", nodes.literal(text=value))


def text(value: str) -> nodes.paragraph:
    return nodes.paragraph(text=value)


def lead(doc: str | None) -> str:
    """A docstring's first paragraph, on one line."""
    return " ".join((doc or "").strip().split("\n\n")[0].split())


def setup(app: Sphinx) -> ExtensionMetadata:
    app.add_directive("example", Example)
    app.add_directive("gallery", Gallery)
    app.add_directive("commands", Commands)
    app.add_directive("recordable", Recordable)
    app.add_directive("objects", Objects)
    app.add_directive("api", Api)
    app.add_directive("typealias", TypeAlias)
    app.add_config_value("esapp_source", "", "html")
    return {"version": "1", "parallel_read_safe": True, "parallel_write_safe": True}
