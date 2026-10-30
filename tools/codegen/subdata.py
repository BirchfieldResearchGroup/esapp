"""The SUBDATA catalog: PowerWorld's AUX document -> ``schema/subdata.tsv`` -> ``esapp.grid``.

SUBDATA are the lists an object holds inside an auxiliary file's DATA section: a generator's
bid curve, a contingency's elements, an injection group's participation points. The document
lists them by owner in its contents and describes each one's columns in its body, either as
"Name : meaning" rows or as a ``// Name Name`` header over an example. ``extract`` reads both;
``schema/subdata.toml`` corrects what the layout garbles.
"""

from __future__ import annotations

import csv
import io
import re
import tomllib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

_LEADER = re.compile(r"\s*(?:\.{2,}\s*\d+|\.{4,})\s*$")
_HEADER = re.compile(r"^\s*//\s*(\S.*)$")  # an example's column header
_HEADING = re.compile(r"^[A-Z]\w*$")  # a body heading: one word at the margin
_ROW = re.compile(r"^(\s+)([A-Za-z][\w /#()-]*?)\s*:\s+\S")  # "Name : meaning"
_UNITS = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_TOKEN = re.compile(r'"[^"]*"|\S+')  # a value in a SUBDATA line: quoted text, or a word
_NOT_COLUMNS = frozenset({"Note", "Example", "Notes"})


@dataclass(frozen=True, slots=True)
class Row:
    owner: str  # the object type that holds the list
    name: str  # the SUBDATA type, as <SUBDATA name>
    columns: tuple[str, ...]  # in order; () when the document names none
    description: str


@dataclass(frozen=True, slots=True)
class Overrides:
    """``schema/subdata.toml``: ``[subdata."Owner.Name"]`` with ``columns`` or ``skip``."""

    entries: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Overrides:
        if not path.is_file():
            return cls()
        with path.open("rb") as stream:
            data = tomllib.load(stream)
        if strays := set(data) - {"subdata"}:
            raise ValueError(f"{path.name}: unknown entries {sorted(strays)}")
        return cls(data.get("subdata", {}))

    def apply(self, rows: Iterable[Row]) -> list[Row]:
        out: dict[str, Row] = {f"{r.owner}.{r.name}": r for r in rows}
        if strays := set(self.entries) - set(out):
            raise ValueError(
                f"subdata.toml names SUBDATA the document does not list: {sorted(strays)}"
            )
        for key, fix in self.entries.items():
            if fix.get("skip"):
                del out[key]
            elif "columns" in fix:
                out[key] = replace(out[key], columns=tuple(fix["columns"]))
        return list(out.values())


# ---------------------------------------------------------------------- extraction
def extract(text: str, types: Iterable[str]) -> list[Row]:
    """Every SUBDATA the document lists, under the object types ``types`` knows."""
    lines = [line.rstrip("\r") for line in text.split("\n")]  # not splitlines: it eats page breaks
    known = set(types)
    entries = list(_contents(lines, known))
    blocks = _blocks(lines, entries)
    rows: list[Row] = []
    for (owner, name), block in zip(entries, blocks, strict=True):
        if name is not None and owner in known:  # display-file objects have no class
            rows.append(Row(owner, name, _columns(block), _sentence(block)))
    return rows


def _contents(lines: Sequence[str], known: set[str]) -> Iterator[tuple[str, str | None]]:
    """(owner, None) for each owner, then (owner, name) for each of its SUBDATA, in order.

    Owners sit at a page's shallowest indentation and SUBDATA deeper; a page break resets the
    indentation, so each page is read on its own.
    """
    start = next(
        i
        for i, line in enumerate(lines)
        if line.strip().startswith("SubData Sections") and _LEADER.search(line)
    )
    pages: list[list[tuple[int, str]]] = [[]]
    for raw in lines[start + 1 :]:
        if raw.lstrip("\f").startswith("SCRIPT Section"):
            break
        if "\f" in raw:
            pages.append([])
        line = _LEADER.sub("", raw.replace("\f", "")).rstrip()
        if line.strip() and not line.strip().isdigit():
            pages[-1].append((len(line) - len(line.lstrip()), line.strip()))
    owner: str | None = None
    for page in pages:
        if not page:
            continue
        top = min(indent for indent, _ in page)
        for indent, name in page:
            if indent == top:
                owner = name
                yield owner, None
            elif owner is not None:
                yield owner, name


def _blocks(lines: Sequence[str], entries: Sequence[tuple[str, str | None]]) -> list[list[str]]:
    """Each entry's text in the document body: from its heading to the next one's."""
    start = next(i for i, line in enumerate(lines) if line.strip() == "SubData Sections") + 1
    heads: list[int] = []
    at = start
    for owner, name in entries:
        wanted = name if name is not None else owner
        found = next((i for i in range(at, len(lines)) if lines[i].strip() == wanted), None)
        heads.append(found if found is not None else -1)
        if found is not None:
            at = found + 1
    out: list[list[str]] = []
    for i, head in enumerate(heads):
        later = [h for h in heads[i + 1 :] if h > head]
        end = later[0] if later else _chapter_end(lines, head)
        out.append(list(lines[head + 1 : end]) if head >= 0 else [])
    for i in range(len(out) - 2, -1, -1):  # headings in a row share the description after them
        if not any(line.strip() for line in out[i]):
            out[i] = out[i + 1]
    return out


def _chapter_end(lines: Sequence[str], head: int) -> int:
    """Where the last entry's text ends: at the next heading (one word at the margin), or the
    SCRIPT chapter."""
    for i in range(head + 1, len(lines)):
        text = lines[i].lstrip("\f")
        if _HEADING.match(text) or text.startswith("SCRIPT Section"):
            return i
    return len(lines)


def _columns(block: Sequence[str]) -> tuple[str, ...]:
    """The example's header if it has one, else the first level of "Name : meaning" rows.

    A header is one only if it names as many columns as the example's next line has values:
    otherwise it is a comment. A page break (a page number, or a form feed) shifts the text,
    so the rows of each page are levelled on their own: the shallowest are the first level.
    """
    for i, line in enumerate(block):
        if found := _HEADER.match(line):
            words = _UNITS.sub(" ", found[1]).split()
            data = next((d for d in block[i + 1 :] if d.strip() and not _HEADER.match(d)), "")
            if len(words) >= 2 and len(words) == len(_TOKEN.findall(data.strip())):
                return tuple(_name(w) for w in words)
    pages: list[list[tuple[int, str]]] = [[]]
    for raw in block:
        if "\f" in raw or raw.strip().isdigit():
            pages.append([])
        if row := _ROW.match(raw.replace("\f", "")):
            pages[-1].append((len(row[1].expandtabs()), _name(row[2])))
    names = [name for page in pages if page for depth, name in page if depth == min(page)[0]]
    return tuple(dict.fromkeys(n for n in names if n and n not in _NOT_COLUMNS))


def _name(text: str) -> str:
    """``Scenario Number`` -> ``ScenarioNumber``; units and notes dropped."""
    words = re.findall(r"[A-Za-z0-9]+", _UNITS.sub(" ", text))
    return "".join(w[:1].upper() + w[1:] for w in words)


def _sentence(block: Sequence[str]) -> str:
    words: list[str] = []
    for line in block:
        text = line.strip()
        if not text:
            if words:
                break
            continue
        if text.isdigit() or _ROW.match(line) or text.startswith(("<", "Example")):
            break
        words.append(text)
    sentence = " ".join(" ".join(words).split()).replace("�", "-")
    end = sentence.find(". ")
    return sentence if end < 0 else sentence[: end + 1]


# ---------------------------------------------------------------------- the checked-in table
def write_rows(rows: Iterable[Row], path: Path) -> None:
    out = io.StringIO()
    writer = csv.writer(out, delimiter="\t", lineterminator="\n")
    writer.writerow(["owner", "name", "columns", "description"])
    writer.writerows((r.owner, r.name, ",".join(r.columns), r.description) for r in rows)
    path.write_text(out.getvalue(), encoding="utf-8", newline="\n")


def read_rows(path: Path) -> list[Row]:
    with path.open(encoding="utf-8", newline="") as stream:
        return [
            Row(
                r["owner"],
                r["name"],
                tuple(filter(None, r["columns"].split(","))),
                r["description"],
            )
            for r in csv.DictReader(stream, delimiter="\t")
        ]
