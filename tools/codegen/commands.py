"""The script-command catalog: PowerWorld's AUX document -> ``schema/commands.tsv`` -> code.

``extract`` reads the "Auxiliary File Format" document (as text; a PDF needs ``pypdf``) into
one row per script command: name, section, signature, how many leading parameters it requires,
and the first sentence of its description. ``schema/commands.toml`` corrects and types those
rows; ``render`` emits one frozen dataclass per command into ``esapp.commands``.
"""

from __future__ import annotations

import csv
import fnmatch
import io
import keyword
import re
import textwrap
import tomllib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

CATALOG = PurePosixPath("src/esapp/commands/_catalog.py")
STUB = PurePosixPath("src/esapp/commands/__init__.pyi")

_LEADER = re.compile(r"\s*(?:\.{2,}\s*\d+|\.{4,})\s*$")  # a contents line's dots and page
_ENTRY = re.compile(r"^([A-Za-z]\w*)\s*(?:\((.*)\))?\s*;?$", re.S)
_TABLE = re.compile(r"^\s{0,24}([^\s:][^:]{0,40}?)?\s*:\s*(.*)$")  # "Name   : text" parameter rows
_OPTIONAL = re.compile(r"^\(?optional\b", re.I)
_WORDS = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


@dataclass(frozen=True, slots=True)
class Row:
    """One documented command, as extracted."""

    name: str
    section: str
    signature: str  # the argument list between the parentheses ("" for none)
    required: int  # leading parameters the document does not call optional
    since: str  # YYYY-MM build a note says added the command, or ""
    description: str


@dataclass(frozen=True, slots=True)
class Param:
    name: str  # Python name
    kind: str  # an _ANNOTATIONS key or a [words] alias
    doc: str  # the document's spelling, e.g. '"filename"' or '[fieldlist]'
    required: bool


@dataclass(frozen=True, slots=True)
class Spec:
    name: str
    section: str
    description: str
    signature: str
    params: tuple[Param, ...]
    mutates: bool
    since: str | None = None


@dataclass(frozen=True, slots=True)
class Overrides:
    """``schema/commands.toml``."""

    words: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # alias -> keywords
    names: Mapping[str, str] = field(default_factory=dict)  # run-together spelling -> name
    types: Mapping[str, str] = field(default_factory=dict)  # parameter name -> kind
    read_only: tuple[str, ...] = ()  # command-name globs that leave the case as it was
    commands: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Overrides:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
        if strays := set(data) - {"words", "names", "types", "read_only", "command"}:
            raise ValueError(f"{path.name}: unknown entries {sorted(strays)}")
        for table in ("names", "types"):
            if bad := [k for k, v in data.get(table, {}).items() if not isinstance(v, str)]:
                raise ValueError(f"{path.name} [{table}]: not names: {bad}")
        words = {name: tuple(map(str, values)) for name, values in data.get("words", {}).items()}
        return cls(
            words,
            data.get("names", {}),
            data.get("types", {}),
            tuple(data.get("read_only", ())),
            data.get("command", {}),
        )


# ---------------------------------------------------------------------- extraction
def extract(text: str) -> list[Row]:
    """The script commands the document lists, in order, with their sections."""
    lines = text.splitlines()
    found: list[tuple[str, str, str]] = []  # name, section, signature
    section = ""
    for entry in _contents(lines):
        if _heading(entry):
            section = entry
        elif (match := _ENTRY.match(entry)) and match[1] not in {n for n, _, _ in found}:
            found.append((match[1], section, " ".join((match[2] or "").split())))
    rows: list[Row] = []
    blocks = _blocks(lines, [n for n, _, _ in found])
    for (name, section, signature), block in zip(found, blocks, strict=True):
        required = _required(split(signature), block)
        description, since = _describe(block)
        rows.append(Row(name, section, signature, required, since, description))
    return rows


def read_document(path: Path) -> str:
    if path.suffix.lower() != ".pdf":
        return path.read_text(encoding="utf-8", errors="replace")
    try:
        from pypdf import PdfReader  # pyright: ignore[reportMissingImports]
    except ImportError:
        raise SystemExit("reading the PDF needs pypdf, or pass the document as text") from None
    reader: Any = PdfReader(str(path))
    return "\n".join(page.extract_text(extraction_mode="layout") or "" for page in reader.pages)


def write_rows(rows: Iterable[Row], path: Path) -> None:
    out = io.StringIO()
    writer = csv.writer(out, delimiter="\t", lineterminator="\n")
    writer.writerow(["name", "section", "required", "since", "signature", "description"])
    writer.writerows(
        (r.name, r.section, r.required, r.since, r.signature, r.description) for r in rows
    )
    path.write_text(out.getvalue(), encoding="utf-8", newline="\n")


def read_rows(path: Path) -> list[Row]:
    with path.open(encoding="utf-8", newline="") as stream:
        return [
            Row(
                r["name"],
                r["section"],
                r["signature"],
                int(r["required"]),
                r["since"],
                r["description"],
            )
            for r in csv.DictReader(stream, delimiter="\t")
        ]


def _contents(lines: Sequence[str]) -> Iterator[str]:
    """The script part of the table of contents, wrapped entries joined."""
    pending = ""
    started = False
    for raw in lines:
        line = raw.strip()
        if not started:
            started = line.startswith("SCRIPT Section")
            continue
        if line.startswith("DATA Section"):
            return
        if not line or line.isdigit():
            continue
        leader = _LEADER.search(line)
        body = f"{pending} {line[: leader.start()] if leader else line}".strip()
        if leader:
            pending = ""
            yield body
        elif "(" in body:
            pending = body  # the signature continues on the next line


def _heading(entry: str) -> bool:
    """A contents entry naming a section: ``Power Flow``, ``TS (Transient Stability)``."""
    if ";" in entry:
        return False
    name, paren, rest = (part.strip() for part in entry.partition("("))
    if not paren:  # TimeStepResetRun is a command; Weather and AUX Actions are headings
        return re.fullmatch(r"[A-Z][a-z\d]+[A-Z]\w*", name) is None
    return " " in name or (" " in rest and re.search(r'[,"\[]', rest) is None)


def _blocks(lines: Sequence[str], names: Sequence[str]) -> Iterator[list[str]]:
    """Each command's section of the document body, found in contents order."""
    start = next(
        i
        for i, line in enumerate(lines)
        if line.startswith("Introduction") and not _LEADER.search(line)
    )
    heads: list[int] = []
    for name in names:
        heading = re.compile(rf"^\s{{0,8}}{name}\s*(\(|;|$)")
        at = next((i for i in range(start, len(lines)) if heading.match(lines[i])), None)
        heads.append(at if at is not None else -1)
        start = at + 1 if at is not None else start
    for i, head in enumerate(heads):
        if head < 0:
            yield []
            continue
        end = next((h for h in heads[i + 1 :] if h > head), len(lines))
        body = head + 1
        if not lines[head].rstrip().endswith((";", ")")):  # the heading wraps: skip the rest
            body = next(
                (
                    j + 1
                    for j in range(head + 1, min(head + 8, end))
                    if lines[j].rstrip().endswith(";")
                ),
                body,
            )
        yield list(lines[body:end])


def _required(params: Sequence[str], block: Sequence[str]) -> int:
    """Leading parameters before the first the document calls optional."""
    names = {_plain(p): i for i, p in enumerate(params)}
    seen_required = -1
    for line in block:
        if (row := _TABLE.match(line)) is None:
            continue
        index = names.get(_plain(row[1] or ""))
        if index is None:
            continue
        if _OPTIONAL.match(row[2].strip()):
            return index
        seen_required = max(seen_required, index)
    if any(_OPTIONAL.match(line.split(":", 1)[1].strip()) for line in block if ":" in line):
        return seen_required + 1  # optional ones the table names differently
    return len(params)


def _describe(block: Sequence[str]) -> tuple[str, str]:
    """The first sentence of the description, and the build (YYYY-MM) a note says added it."""
    words: list[str] = []
    for line in block:
        text = line.strip()
        if not text or (text.endswith(";") and not words):  # blank, or another spelling
            continue
        if text.isdigit() or text.endswith(";") or _TABLE.match(line):
            break
        words.append(text)
        if len(words) > 6:
            break
    text = " ".join(" ".join(words).split()).replace("�", "-")
    since = ""
    if note := _ADDED.match(text):
        since = f"{note[2]}-{_MONTHS.index(note[1][:3].lower()) + 1:02d}"
        text = text[note.end() :]
    end = text.find(". ")
    return (text if end < 0 else text[: end + 1]), since


_ADDED = re.compile(
    r"\(?(?:this command was |command )?added (?:in|on|to) (?:the )?([a-z]+) [\d?]{1,2}, (\d{4})"
    r"[^)]*?simulator(?: \d+)?\)?\s*",
    re.I,
)
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _plain(token: str) -> str:
    return re.sub(r"[^a-z0-9]", "", token.lower())


def split(signature: str) -> list[str]:
    """Top-level comma-separated parameters; brackets and quotes kept whole."""
    tokens: list[str] = []
    depth, quoted, start = 0, False, 0
    for i, ch in enumerate(signature):
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch == "[":
            depth += 1
        elif not quoted and ch == "]":
            depth -= 1
        elif not quoted and depth == 0 and ch == ",":
            tokens.append(signature[start:i].strip())
            start = i + 1
    tokens.append(signature[start:].strip())
    return [t for t in tokens if t]


# ---------------------------------------------------------------------- arity
type Arity = Mapping[str, tuple[int, int]]
"""Command -> (fewest, most) parameters, as Simulator validates them (codegen --from-simauto)."""


def write_arity(arity: Arity, path: Path) -> None:
    lines = ["name\tfewest\tmost", *(f"{n}\t{lo}\t{hi}" for n, (lo, hi) in sorted(arity.items()))]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def read_arity(path: Path) -> dict[str, tuple[int, int]]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as stream:
        return {
            r["name"]: (int(r["fewest"]), int(r["most"]))
            for r in csv.DictReader(stream, delimiter="\t")
        }


def disagreements(commands: Iterable[Spec], arity: Arity) -> list[str]:
    """Commands taking more or fewer parameters than the document lists."""
    return [
        f"{spec.name} takes up to {arity[spec.name][1]}, the document lists {len(spec.params)}"
        for spec in commands
        if spec.name in arity and arity[spec.name][1] != len(spec.params)
    ]


# ---------------------------------------------------------------------- specs
def specs(rows: Iterable[Row], overrides: Overrides, arity: Arity | None = None) -> list[Spec]:
    """Rows corrected and typed by the overrides, sorted by name.

    A command's required parameters are, first to last: its ``required`` override, the
    fewest Simulator takes (``arity``), or what the document says.
    """
    arity = arity or {}
    by_name = {r.name: r for r in rows}
    for name, fix in overrides.commands.items():
        if name not in by_name:
            if "signature" not in fix:
                raise ValueError(f"[command.{name}]: not in the document; give its signature")
            by_name[name] = Row(name, fix.get("section", ""), fix["signature"], -1, "", "")
    out: list[Spec] = []
    for name, row in sorted(by_name.items()):
        fix = overrides.commands.get(name, {})
        signature: str = fix.get("signature", row.signature)
        tokens = split(signature)
        documented = row.required if row.required >= 0 else len(tokens)
        fewest = min(arity[name][0], len(tokens)) if name in arity else documented
        required = fix.get("required", fewest)
        params = _params(tokens, required, overrides.names)
        names: Mapping[str, str] = fix.get("names", {})
        types: Mapping[str, str] = fix.get("types", {})
        if unknown := (set(names) | set(types)) - {p.name for p in params} - set(names.values()):
            raise ValueError(f"[command.{name}]: no parameters {sorted(unknown)}")
        params = [replace(p, name=names.get(p.name, p.name)) for p in params]
        params = [
            replace(p, kind=types.get(p.name, overrides.types.get(p.name, p.kind))) for p in params
        ]
        for p in params:
            if p.kind not in _ANNOTATIONS and p.kind not in overrides.words:
                raise ValueError(f"[command.{name}]: {p.name} has unknown type {p.kind!r}")
        if len({p.name for p in params}) != len(params):
            raise ValueError(f"[command.{name}]: parameter names collide")
        read_only = any(fnmatch.fnmatchcase(name, glob) for glob in overrides.read_only)
        out.append(
            Spec(
                name,
                fix.get("section", row.section),
                fix.get("description", row.description),
                signature,
                tuple(params),
                mutates=fix.get("mutates", not read_only),
                since=fix.get("since", row.since) or None,
            )
        )
    return out


def _params(tokens: Sequence[str], required: int, names: Mapping[str, str]) -> list[Param]:
    out: list[Param] = []
    taken: set[str] = set()
    for i, token in enumerate(tokens):
        kind, name = _kind(token, names)
        base, n = name, 2
        while name in taken:
            name, n = f"{base}{n}", n + 1
        taken.add(name)
        out.append(Param(name, kind, token, i < required))
    return out


def _kind(token: str, names: Mapping[str, str]) -> tuple[str, str]:
    """A parameter's kind and Python name, from how the document spells it."""
    inner = token.strip('"[] ')
    plain = _plain(inner)
    if "," in inner:  # a positional list of options: [StartTime, StopTime, ...]
        return "list", "options"
    name = names.get(plain) or snake(inner) or "value"
    if token.startswith('"'):
        return ("path" if re.search(r"file|director|folder|path", name) else "str"), name
    if token.startswith("["):
        if "objecttype" in plain:
            return "types", name
        if "field" in plain:
            return "fields", name
        if _OBJECT.search(inner) and not plain.endswith(("list", "s")):
            return "object", name
        return "list", name
    if plain == "objecttype":
        return "type", name
    if "filter" in plain:
        return "filter", name
    return "word", name


# [BUS num], [BRANCH nearbusnum farbusnum ckt], [transactor seller], [flow element], [start]
_OBJECT = re.compile(r"element|transactor|^bus\b|^branch\b|^start$|^end$|injection group", re.I)


def snake(text: str) -> str:
    """``CreateIfNotFound1`` -> ``create_if_not_found1``; ``GEFileType`` -> ``ge_file_type``."""
    name = ""
    for part in re.split(r"[^A-Za-z0-9]+", text):
        for word in (w.lower() for w in _WORDS.findall(part)):
            name += word if word.isdigit() or not name else f"_{word}"
    if name[:1].isdigit():
        name = f"n{name}"
    return f"{name}_" if keyword.iskeyword(name) or name in _RESERVED else name


_RESERVED = frozenset(  # Command attributes a parameter must not shadow
    {"name", "section", "mutates", "filters", "bare", "since", "arguments", "statement"}
)


# ---------------------------------------------------------------------- emission
_ANNOTATIONS = {
    "path": "StrPath",
    "str": "str",
    "word": "Word",
    "words": "Sequence[str]",
    "bool": "bool",
    "int": "int",
    "float": "float",
    "list": "Sequence[object]",
    "fields": "Sequence[Field[Any] | str]",
    "object": "GObject",
    "type": "type[GObject]",
    "types": "Sequence[type[GObject]]",
    "filter": "Where",
}
_BARE = frozenset({"word", "words", "fields"})  # text written unquoted: keywords, field names
_WIDTH = 100


def render(commands: Sequence[Spec], overrides: Overrides, header: str) -> dict[PurePosixPath, str]:
    lines = [
        '"""Every documented PowerWorld script command as a typed value."""',
        "",
        "from __future__ import annotations",
        "",
        "from collections.abc import Sequence",
        "from dataclasses import dataclass",
        "from typing import TYPE_CHECKING, Any, Literal",
        "",
        "from esapp.commands._command import Command",
        "",
        "if TYPE_CHECKING:",
        "    from esapp._indexing import Where",
        "    from esapp.commands._command import Word",
        "    from esapp.schema import Field, GObject",
        "    from esapp.simauto._script import StrPath",
    ]
    for alias, values in overrides.words.items():
        lines += ["", f"type {alias} = Literal[{', '.join(map(_q, values))}]"]
    for spec in commands:
        options = [f"section={_q(spec.section)}"]
        if not spec.mutates:
            options.append("mutates=False")
        if filters := [p.name for p in spec.params if p.kind == "filter"]:
            options.append(f"filters={_tuple(filters)}")
        if bare := [p.name for p in spec.params if p.kind in _BARE or p.kind in overrides.words]:
            options.append(f"bare={_tuple(bare)}")
        if spec.since:
            options.append(f"since={_q(spec.since)}")
        doc = _escape(spec.description) or f"The {spec.name} script command."
        text = f"{spec.name}({spec.signature});" if spec.params else f"{spec.name};"
        head = f"class {spec.name}(Command, {', '.join(options)}):"
        if len(head) > _WIDTH:
            head = "\n".join(
                [f"class {spec.name}(", "    Command,", *(f"    {o}," for o in options), "):"]
            )
        lines += [
            "",
            "",
            "@dataclass(frozen=True, slots=True)",
            head,
            *textwrap.wrap(f'"""{doc}', _WIDTH, initial_indent="    ", subsequent_indent="    "),
            "",
            f"    ``{_escape(text)}``",
            '    """',
        ]
        if spec.params:
            lines.append("")
        for p in spec.params:
            annotation = _ANNOTATIONS.get(p.kind, p.kind)
            optional = "" if p.required else " | None = None"
            lines.append(f"    {p.name}: {annotation}{optional}")
    names = sorted(s.name for s in commands)
    lines += ["", "", f"__all__ = [{', '.join(map(_q, [*overrides.words, *names]))}]"]
    stub = [
        "from esapp.commands._command import Command as Command",
        *(f"from esapp.commands._catalog import {n} as {n}" for n in [*overrides.words, *names]),
    ]
    return {CATALOG: header + "\n".join(lines) + "\n", STUB: header + "\n".join(stub) + "\n"}


def _q(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _tuple(items: Iterable[str]) -> str:
    quoted = [_q(i) for i in items]
    return f"({quoted[0]},)" if len(quoted) == 1 else f"({', '.join(quoted)})"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
