"""``schema/overrides.toml``: hand corrections applied to any source before emission."""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from tools.codegen.model import DTYPES, FieldSpec, ObjectSpec


@dataclass(frozen=True, slots=True)
class TSGroup:
    prefix: str  # field-name prefix dropped from attribute names
    owner: str  # PowerWorld object type


@dataclass(frozen=True, slots=True)
class Overrides:
    exclude_objects: frozenset[str] = frozenset()
    exclude_fields: frozenset[str] = frozenset()
    identifiers: frozenset[str] = frozenset()  # fields that also name their object
    enterable: frozenset[str] = frozenset()  # "Type.Field": settable, whatever the export says
    nonscalar: frozenset[str] = frozenset()  # fields Simulator sends as a list or range
    additions: tuple[tuple[str, FieldSpec], ...] = ()
    vocab: tuple[tuple[str, tuple[str, str]], ...] = ()
    ts_groups: tuple[tuple[str, TSGroup], ...] = ()
    ts_suffixes: tuple[str, ...] = ()
    ts_unrecordable: frozenset[str] = frozenset()
    ts_save: tuple[tuple[str, tuple[str, ...]], ...] = ()  # "Owner.Field" -> flags

    @classmethod
    def load(cls, path: Path, *vocabularies: Path) -> Overrides:
        """``path``'s overrides, with the ``[vocab]`` of any ``vocabularies`` beneath its own."""
        with path.open("rb") as stream:
            data = tomllib.load(stream)
        vocab: dict[str, list[str]] = {}
        for extra in vocabularies:
            if extra.is_file():
                with extra.open("rb") as stream:
                    vocab |= tomllib.load(stream).get("vocab", {})
        vocab |= data.get("vocab", {})
        exclude = data.get("exclude", {})
        ts = data.get("ts", {})
        return cls(
            exclude_objects=frozenset(exclude.get("objects", ())),
            exclude_fields=frozenset(exclude.get("fields", ())),
            identifiers=frozenset(data.get("identifiers", {}).get("fields", ())),
            enterable=frozenset(data.get("enterable", {}).get("fields", ())),
            nonscalar=frozenset(data.get("nonscalar", {}).get("fields", ())),
            additions=tuple(
                (entry["object"], _added_field(entry)) for entry in data.get("add", ())
            ),
            vocab=tuple((name, (pair[0], pair[1])) for name, pair in vocab.items()),
            ts_groups=tuple(
                (group, TSGroup(spec["prefix"], spec["owner"]))
                for group, spec in ts.get("groups", {}).items()
            ),
            ts_suffixes=tuple(ts.get("suffixes", ())),
            ts_unrecordable=frozenset(ts.get("unrecordable", ())),
            ts_save=tuple((field, tuple(flags)) for field, flags in ts.get("save", {}).items()),
        )

    def apply(self, objects: Iterable[ObjectSpec]) -> list[ObjectSpec]:
        """Drop exclusions, mark identifiers, add missing fields, and order objects by name."""
        out: list[ObjectSpec] = []
        for obj in objects:
            if obj.name in self.exclude_objects:
                continue
            fields: dict[str, FieldSpec] = {}
            for f in obj.fields:
                excluded = f.name in self.exclude_fields or "/" in f.name  # "/" is not addressable
                if not excluded:
                    marked = replace(
                        f,
                        identifier=f.identifier or f.name in self.identifiers,
                        enterable=f.enterable or f"{obj.name}.{f.name}" in self.enterable,
                        nonscalar=f.nonscalar or f.name in self.nonscalar,
                    )
                    fields.setdefault(f.name, marked)
            for o, f in self.additions:
                if o == obj.name:
                    fields.setdefault(f.name, f)
            out.append(ObjectSpec(obj.name, tuple(fields.values())))
        return sorted(out, key=lambda o: o.name)


def _added_field(entry: dict[str, Any]) -> FieldSpec:
    dtype = entry.get("dtype", "str")
    if dtype not in DTYPES.values():
        raise ValueError(
            f"overrides: unknown dtype {dtype!r} for {entry['object']}.{entry['name']}"
        )
    return FieldSpec(
        name=entry["name"],
        dtype=dtype,
        description=entry.get("description", ""),
        enterable=entry.get("enterable", False),
        edit_only=entry.get("edit_only", False),
    )
