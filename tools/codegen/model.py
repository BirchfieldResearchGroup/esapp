"""The source-agnostic schema model: what every source yields and the emitter consumes.

A ``SchemaSource`` describes one PowerWorld version's object types: PowerWorld's Case
Object Fields export (``sources.export``), or a running Simulator's own field lists
(``sources.simauto``, built on GetFieldList). Both yield ``ObjectSpec`` values, decoding the
same key notation with ``parse_key_symbol``, so overrides and emission are shared.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

type DType = Literal["int", "float", "str"]

DTYPES: dict[str, DType] = {"Integer": "int", "Real": "float", "String": "str"}


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str  # PowerWorld variable name, e.g. "BusNum:1"
    dtype: DType
    description: str = ""
    key: int | None = None  # 1-based position in the primary key
    alt_key: int | None = None  # 1-based position in the alternate key
    required: bool = False  # must be given when creating an object
    enterable: bool = False
    edit_only: bool = False  # enterable only in EDIT mode
    identifier: bool = False  # also names the object (Label, ObjectID)
    nonscalar: bool = False  # Simulator sends a list or range, not one value


@dataclass(frozen=True, slots=True)
class ObjectSpec:
    name: str  # PowerWorld object type, e.g. "Branch"
    fields: tuple[FieldSpec, ...]


class SchemaSource(Protocol):
    """The object types of one PowerWorld version."""

    @property
    def label(self) -> str:
        """Provenance, written into every generated file's header."""
        ...

    def objects(self) -> Iterable[ObjectSpec]: ...


_KEY_SYMBOL = re.compile(r"\*(\d*)([A-Z]?)\*")


def parse_key_symbol(symbol: str) -> tuple[int | None, int | None, bool]:
    """Decode PowerWorld's key column into ``(key position, alt-key position, required)``.

    ``*2B*`` is the 2nd primary-key and 2nd (B) alternate-key component; ``*A*`` is the
    first alternate-key component only; ``**`` marks a field required for creation.
    """
    found = _KEY_SYMBOL.search(symbol)
    if found is None:
        return None, None, False
    digits, letter = found.groups()
    if not digits and not letter:
        return None, None, True
    key = int(digits) if digits else None
    alt = ord(letter) - ord("A") + 1 if letter else None
    return key, alt, False
