"""What to record: fields written as in ``pw[...]``, turned into a column layout."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from esapp._indexing import FieldSpec, Where, as_where, select_fields
from esapp.schema import Field, GObject, Role
from esapp.ts._recordable import fields_of, flags

if TYPE_CHECKING:
    from esapp._session import PowerWorld

type Pair = tuple[GObject, Field[Any]]
type Watch = (
    Field[Any]  # every object of the field's type
    | tuple[type[GObject], FieldSpec]  # every object, several fields
    | tuple[type[GObject], FieldSpec, Where]  # the objects a selection picks
    | tuple[GObject | Sequence[GObject], FieldSpec]  # particular objects
)
"""What to record, written as you'd index ``pw[...]``. ``(Gen, slice(None))`` is every recordable
Gen field."""


@dataclass(frozen=True, slots=True)
class Block:
    """One field of several objects: one contiguous block of columns."""

    field: Field[Any]
    refs: tuple[GObject, ...]
    every: bool  # every object of the type: its flag is set for all of them at once


@dataclass(frozen=True, slots=True)
class Plan:
    """The objects and fields a result holds, in column order. Fields come in the order you
    asked for them, and each field's columns are next to each other."""

    blocks: tuple[Block, ...] = ()

    @property
    def width(self) -> int:
        return sum(len(b.refs) for b in self.blocks)

    def pairs(self) -> Iterator[Pair]:
        for block in self.blocks:
            for ref in block.refs:
                yield ref, block.field

    def span(self, field: Field[Any]) -> tuple[slice, Block]:
        """The columns of ``field``, and its block."""
        start = 0
        for block in self.blocks:
            if block.field is field:
                return slice(start, start + len(block.refs)), block
            start += len(block.refs)
        raise KeyError(f"{field!r} was not recorded")

    def column(self, ref: GObject, field: Field[Any]) -> int:
        span, block = self.span(field)
        try:
            return span.start + block.refs.index(ref)
        except ValueError:
            raise KeyError(f"{field!r} was not recorded for {ref!r}") from None

    def of(self, ref: GObject) -> list[tuple[int, Field[Any]]]:
        """``ref``'s columns and their fields."""
        return [(j, f) for j, (r, f) in enumerate(self.pairs()) if r == ref]

    @property
    def columns(self) -> pd.MultiIndex:
        """One entry per column: (object type, field, key)."""
        return pd.MultiIndex.from_tuples(
            [(type(r).__name__, f.name, r.key) for r, f in self.pairs()],
            names=["type", "field", "key"],
        )

    def flags(self) -> dict[tuple[type[GObject], Field[Any]], tuple[GObject, ...] | None]:
        """The TSSave flags that record this plan, and their objects (None: every object)."""
        out: dict[tuple[type[GObject], Field[Any]], tuple[GObject, ...] | None] = {}
        for block in self.blocks:
            key = (block.field.owner, flags(block.field)[0])
            if block.every or (key in out and out[key] is None):
                out[key] = None
            else:
                previous = out.get(key) or ()
                out[key] = tuple(dict.fromkeys([*previous, *block.refs]))
        return out

    def subset(self, watch: Iterable[Watch]) -> tuple[Plan, npt.NDArray[np.intp]]:
        """The part of this plan ``watch`` asks for, and where its columns are here."""
        wanted: dict[Field[Any], dict[GObject, None]] = {}
        for spec in watch:
            _, fields, refs, where = _parse(spec)
            if where is not None:
                raise TypeError("selecting a recorded part by a selection needs the session")
            for f in fields:
                recorded = self.span(f)[1].refs
                chosen = recorded if refs is None else refs
                if missing := [r for r in chosen if r not in recorded]:
                    raise KeyError(f"{f!r} was not recorded for {missing}")
                wanted.setdefault(f, {}).update(dict.fromkeys(chosen))
        blocks = tuple(Block(f, tuple(refs), every=False) for f, refs in wanted.items())
        columns = [self.column(r, b.field) for b in blocks for r in b.refs]
        return Plan(blocks), np.asarray(columns, dtype=np.intp)

    def to_json(self) -> list[dict[str, Any]]:
        return [
            {
                "type": b.field.owner.__name__,
                "field": b.field.name,
                "keys": [list(r.key) for r in b.refs],
                "every": b.every,
            }
            for b in self.blocks
        ]

    @classmethod
    def from_json(cls, data: Iterable[dict[str, Any]]) -> Plan:
        grid = import_module("esapp.grid")
        blocks: list[Block] = []
        for entry in data:
            gtype: type[GObject] = getattr(grid, entry["type"])
            refs = tuple(gtype(*key) for key in entry["keys"])
            blocks.append(Block(gtype.schema.field(entry["field"]), refs, entry["every"]))
        return cls(tuple(blocks))


def resolve(pw: PowerWorld, watch: Iterable[Watch]) -> Plan:
    """``record=`` specs to a plan. Object lists are read here, once per type."""
    everything: dict[type[GObject], list[GObject]] = {}
    wanted: dict[Field[Any], dict[GObject, None]] = {}
    whole: set[Field[Any]] = set()
    for spec in watch:
        gtype, fields, refs, where = _parse(spec)
        if where is not None:
            refs = pw.refs(gtype, where)
        elif refs is None:
            if gtype not in everything:
                everything[gtype] = pw.refs(gtype)
            refs = everything[gtype]
            whole.update(fields)
        for f in fields:
            wanted.setdefault(f, {}).update(dict.fromkeys(refs))
    return Plan(tuple(Block(f, tuple(refs), f in whole) for f, refs in wanted.items() if refs))


def _parse(
    spec: object,
) -> tuple[type[GObject], list[Field[Any]], list[GObject] | None, Where | None]:
    """``(type, fields, objects, selection)``: objects None for every one of them."""
    match spec:
        case Field(owner=gtype):
            return gtype, _recordable_fields(gtype, spec), None, None
        case (type() as gtype, fields, where) if issubclass(gtype, GObject):
            return gtype, _recordable_fields(gtype, fields), None, as_where(where)
        case (type() as gtype, fields) if issubclass(gtype, GObject):
            return gtype, _recordable_fields(gtype, fields), None, None
        case (GObject() as ref, fields):
            return type(ref), _recordable_fields(type(ref), fields), [ref], None
        case ([GObject() as first, *_] as refs, fields):
            if any(type(r) is not type(first) for r in refs):
                raise TypeError(f"objects recorded together must be of one type: {refs!r}")
            return type(first), _recordable_fields(type(first), fields), list(refs), None
        case _:
            raise TypeError(f"cannot record {spec!r}; see help(esapp.ts.Watch)")


def _recordable_fields(gtype: type[GObject], spec: object) -> list[Field[Any]]:
    if spec == slice(
        None
    ):  # every recordable field; indexed ones through the variants the schema knows
        fields: list[Field[Any]] = []
        for f in fields_of(gtype):
            fields.extend([f[n] for n in f.variants or ()] if f.role & Role.INDEXED else [f])
        return fields
    fields = select_fields(gtype, spec)
    if strays := [f.name for f in fields if not flags(f)]:
        raise ValueError(
            f"{gtype.__name__} {strays} cannot be recorded: esapp.ts.TS lists the fields "
            "Simulator can record"
        )
    return fields
