"""Schema source: the installed Simulator's own field lists, through SimAuto's GetFieldList.

GetFieldList rows are ``(key symbol, variable name, type, description, display name,
enterable)``. Its enterable column is finer than the export's: ``AUX/Paste`` and
``Yes/Always`` fields take writes through SimAuto (which loads data as an AUX file does), so
they count as enterable; ``Depends: ...`` fields only sometimes do, so they do not.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime

from esapp.errors import PowerWorldError
from esapp.simauto import SimAuto
from tools.codegen.model import DTYPES, FieldSpec, ObjectSpec, parse_key_symbol

_ENTERABLE = {  # enterable column -> (enterable, edit-mode only)
    "yes": (True, False),
    "yes/always": (True, False),
    "aux/paste": (True, False),
    "edit mode only": (True, True),
}
_FLAGS = ("YES", "NO")
_ARITY = re.compile(
    r"requires (?:between (\d+) and (\d+)|exactly (\d+)) param|(does not pass any arg)", re.I
)
_TOO_MANY = 64  # arguments no command takes: validation refuses the statement unrun


class SimAutoSource:
    """Field lists of ``types`` from a running Simulator."""

    def __init__(self, simauto: SimAuto, types: Iterable[str]) -> None:
        self.simauto = simauto
        self.types = list(types)
        self.unknown: list[str] = []  # types this Simulator does not have

    @property
    def label(self) -> str:
        return f"SimAuto GetFieldList ({version(self.simauto)})"

    def objects(self) -> Iterator[ObjectSpec]:
        for name in self.types:
            try:
                rows = self.simauto.GetFieldList(name)
            except PowerWorldError:
                self.unknown.append(name)
                continue
            yield ObjectSpec(name, tuple(f for row in rows or () if (f := field(row))))


def field(row: Sequence[object]) -> FieldSpec | None:
    """One GetFieldList row as a field."""
    symbol, name, kind, description = (str(v or "") for v in row[:4])
    if not name:
        return None
    key, alt, required = parse_key_symbol(symbol)
    enterable, edit_only = _ENTERABLE.get(
        str(row[5] if len(row) > 5 else "").lower(), (False, False)
    )
    return FieldSpec(
        name=name,
        dtype=DTYPES.get(kind, "str"),
        description=" ".join(description.split()),
        key=key,
        alt_key=alt,
        required=required,
        enterable=enterable,
        edit_only=edit_only,
    )


def version(simauto: SimAuto) -> str:
    """``Simulator 24 (2025-12-11)``, from ProgramInformation."""
    for entry in simauto.ProgramInformation:
        if entry and entry[0] == "version":
            built = entry[2]
            stamp = f" ({built:%Y-%m-%d})" if isinstance(built, datetime) else ""
            return f"Simulator {entry[1]}{stamp}"
    return "Simulator"


def command_arity(simauto: SimAuto, names: Iterable[str]) -> dict[str, tuple[int, int]]:
    """How many parameters each command takes, ``(fewest, most)``, as Simulator validates them.

    Each command gets far too many arguments, which Simulator's validation refuses before
    anything runs; most refusals say how many it takes. Commands whose refusal does not say
    are left out, as are ones that run anyway (those ignore arguments): pass only commands
    that take parameters, and run this on an empty case (NewCase).
    """
    out: dict[str, tuple[int, int]] = {}
    for name in names:
        try:
            simauto.RunScriptCommand(f"{name}({', '.join(['0'] * _TOO_MANY)});")
        except PowerWorldError as err:
            if found := _ARITY.search(str(err)):
                low, high, exact, none = found.groups()
                if none:
                    out[name] = (0, 0)
                elif exact:
                    out[name] = (int(exact), int(exact))
                else:
                    out[name] = (int(low), int(high))
    return out


def option_vocab(simauto: SimAuto, objects: Iterable[ObjectSpec]) -> dict[str, tuple[str, str]]:
    """YES/NO vocabularies, by ``Type.Field``, for option fields: text fields of keyless
    objects (one per case) that hold YES or NO in the open case. Needs a case open."""
    vocab: dict[str, tuple[str, str]] = {}
    for obj in objects:
        if any(f.key is not None for f in obj.fields):
            continue
        names = [f.name for f in obj.fields if f.dtype == "str" and ":" not in f.name]
        if not names:
            continue
        try:
            columns = simauto.GetParamsTypedCols(obj.name, names)
        except PowerWorldError:
            continue
        for name, column in zip(names, columns or (), strict=False):
            if column and str(column[0]).strip().upper() in _FLAGS:
                vocab[f"{obj.name}.{name}"] = _FLAGS
    return dict(sorted(vocab.items()))
