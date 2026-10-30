"""Schema source: PowerWorld's "Case Object Fields" export, tab-separated, optionally gzipped.

Columns: object type, SUBDATA allowed, key/required, variable name, concise name, type,
description, available-field-list path, enterable, data-maintainer support/inheritance.
An object's header row starts in column 0; its field rows start with a tab. Quoted
descriptions may wrap onto continuation lines.
"""

from __future__ import annotations

import gzip
import io
from collections.abc import Iterable, Iterator
from pathlib import Path

from tools.codegen.model import DTYPES, FieldSpec, ObjectSpec, parse_key_symbol

_TITLES = (
    "Object Type",
    "SUBDATA Allowed",
    "Key/Required Fields",
    "Variable Name",
    "Concise Variable Name",
    "Type of Variable",
    "Description",
    "Available Field List",
    "Enterable",
    "Data Maintainer Support",
    "Data Maintainer Inheritance",
)
_TYPES = {dtype: name for name, dtype in DTYPES.items()}


class FieldExport:
    def __init__(self, path: Path) -> None:
        self.path = path

    @property
    def label(self) -> str:
        return self.path.name

    def objects(self) -> Iterator[ObjectSpec]:
        name: str | None = None
        fields: list[FieldSpec] = []
        for row in _logical_rows(self._lines()):
            columns = row.split("\t")
            if _is_header(columns):
                if name is not None:
                    yield ObjectSpec(name, tuple(fields))
                name, fields = columns[0].strip(), []
            elif name is not None and row.startswith("\t") and (spec := _field(columns)):
                fields.append(spec)
        if name is not None:
            yield ObjectSpec(name, tuple(fields))

    def _lines(self) -> Iterator[str]:
        opener = gzip.open if self.path.suffix == ".gz" else open
        with opener(self.path, "rt", encoding="utf-8") as stream:
            next(stream, None)  # column titles
            yield from stream


def write_export(objects: Iterable[ObjectSpec], path: Path) -> None:
    """Write ``objects`` in the export's layout (gzipped for ``.gz``), for ``FieldExport``."""
    out = io.StringIO()
    out.write("\t".join(_TITLES) + "\n")
    for obj in objects:
        out.write(f"{obj.name}\tNo\n")
        for f in obj.fields:
            enterable = "Edit Mode Only" if f.edit_only else ("Yes" if f.enterable else "No")
            description = " ".join(f.description.split())
            columns = ["", "", _key_symbol(f), f.name, "", _TYPES[f.dtype], f"'{description}'"]
            out.write("\t".join([*columns, "''", f"'{enterable}'"]) + "\n")
    data = out.getvalue().encode("utf-8")
    path.write_bytes(gzip.compress(data, mtime=0) if path.suffix == ".gz" else data)


def _key_symbol(f: FieldSpec) -> str:
    """The inverse of ``parse_key_symbol``."""
    if f.key is None and f.alt_key is None:
        return "**" if f.required else ""
    return f"*{f.key or ''}{chr(ord('A') + f.alt_key - 1) if f.alt_key else ''}*"


def _logical_rows(lines: Iterable[str]) -> Iterator[str]:
    """Join wrapped continuation lines onto the row they belong to."""
    current: str | None = None
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        if current is not None and (line.startswith("\t") or _is_header(line.split("\t"))):
            yield current
            current = line
        elif current is None:
            current = line
        else:
            current = f"{current}\n{line}"
    if current is not None:
        yield current


def _column(columns: list[str], index: int) -> str:
    value = columns[index].strip() if index < len(columns) else ""
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    return value


def _is_header(columns: list[str]) -> bool:
    name = _column(columns, 0)
    if len(name) <= 1:
        return False
    return _column(columns, 1).lower() in {"yes", "no"} or _column(columns, 9).lower() in {
        "yes",
        "no",
        "inheritalways",
    }


def _field(columns: list[str]) -> FieldSpec | None:
    name = _column(columns, 3)
    if not name:
        return None
    key, alt, required = parse_key_symbol(_column(columns, 2))
    enterable = _column(columns, 8).lower()
    return FieldSpec(
        name=name,
        dtype=DTYPES.get(_column(columns, 5), "str"),
        description=" ".join(_column(columns, 6).split()),
        key=key,
        alt_key=alt,
        required=required,
        enterable=enterable in {"yes", "edit mode only"},
        edit_only=enterable == "edit mode only",
    )
