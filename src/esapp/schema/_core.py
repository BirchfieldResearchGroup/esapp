"""Fields and object types. Each generated class describes one PowerWorld object type; its
instances refer to single objects by primary key (``Bus(5)``, ``Gen(1, "1")``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntFlag
from functools import cache
from importlib import import_module
from typing import Any, ClassVar, Literal, Self

import numpy as np

type Key = int | float | str
type Variants = dict[int, int | tuple[int, type]]
type Op = Literal[
    "=", "<>", ">", "<", ">=", "<=", "between", "notbetween", "contains", "notcontains",
    "startswith", "notstartswith", "isblank", "notisblank",
]  # fmt: skip


class Role(IntFlag):
    """Flags that describe a field, combined as in ``Role.KEY | Role.REQUIRED``. Generated
    code writes them as one letter each: K, A, R, E, M, V, I, N."""

    NONE = 0
    #: Part of the primary key.
    KEY = 1
    #: Part of the alternate key, such as BusName_NomVolt.
    ALT_KEY = 2
    #: Must be given when creating an object.
    REQUIRED = 4
    #: Can be set.
    ENTERABLE = 8
    #: Can be set only in EDIT mode.
    EDIT_ONLY = 16
    #: Exists only as numbered variants: use ``field[n]``.
    INDEXED = 32
    #: Also names the object (Label, ObjectID), so writes can find objects by it.
    IDENTIFIER = 64
    #: Simulator returns a list or range, not one value, so it's read untyped.
    NONSCALAR = 128


class Field[T]:
    """A PowerWorld field, as an attribute of an object type: ``Bus.BusPUVolt``.

    ``str(field)`` is the PowerWorld name. ``field[n]`` is the numbered variant PowerWorld
    writes ``name:n`` (``Branch.BusNum[1]`` is ``BusNum:1``).

    Comparisons build conditions for reads, writes and commands::

        pw[Bus, Bus.BusPUVolt, Bus.BusNomVolt > 100]
        pw[Gen, Gen.GenMW, Gen.AreaNum.eq(1) & (Gen.GenMW >= 50)]

    ``==`` isn't a condition (fields are used as dictionary keys): use :meth:`eq`.
    """

    __slots__ = ("attr", "dtype", "name", "owner", "role", "variants", "vocab")

    owner: type[GObject]
    attr: str

    def __init__(
        self,
        name: str,
        dtype: type[T],
        role: int = 0,
        variants: Variants | None = None,
        *,
        vocab: tuple[str, str] | None = None,
    ) -> None:
        self.name = name
        self.dtype = dtype
        self.role = Role(role)
        self.variants = variants
        self.vocab = vocab  # PowerWorld's (true, false) strings for Python bools

    def __set_name__(self, owner: type[GObject], attr: str) -> None:
        self.owner, self.attr = owner, attr

    def __getitem__(self, n: int) -> Field[T]:
        return self.owner.schema.field(f"{self.name}:{n}")

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        owner = getattr(self, "owner", None)
        where = f"{owner.__name__}." if owner is not None else ""
        return f"<Field {where}{self.name}: {getattr(self.dtype, '__name__', self.dtype)}>"

    # where-clauses ---------------------------------------------------------------
    def __lt__(self, value: T) -> Clause:
        return Clause(self, "<", (value,))

    def __le__(self, value: T) -> Clause:
        return Clause(self, "<=", (value,))

    def __gt__(self, value: T) -> Clause:
        return Clause(self, ">", (value,))

    def __ge__(self, value: T) -> Clause:
        return Clause(self, ">=", (value,))

    def eq(self, value: T) -> Clause:
        """Equal to ``value``."""
        return Clause(self, "=", (value,))

    def ne(self, value: T) -> Clause:
        """Not equal to ``value``."""
        return Clause(self, "<>", (value,))

    def between(self, low: T, high: T) -> Clause:
        """From ``low`` to ``high``, both included."""
        return Clause(self, "between", (low, high))

    def contains(self, text: str) -> Clause:
        """Contains ``text``."""
        return Clause(self, "contains", (text,))

    def startswith(self, text: str) -> Clause:
        """Starts with ``text``."""
        return Clause(self, "startswith", (text,))

    def isblank(self) -> Clause:
        """Holds nothing."""
        return Clause(self, "isblank")


class SubData:
    """A list stored on each object of a type, such as a generator's bid curve:
    ``Gen.BidCurve``.

    Read and write it like a field. You get one row per entry, in order::

        pw[Gen, Gen.BidCurve]                        # every generator's: keys, point, MW, Price
        pw[Gen(2, "1"), Gen.BidCurve] = points       # replaces one generator's

    ``columns`` are the column names from PowerWorld's documentation. Lists it doesn't name
    get numbered columns (``"1"``, ``"2"``, ...).
    """

    __slots__ = ("attr", "columns", "name", "owner")

    owner: type[GObject]
    attr: str

    def __init__(self, name: str, columns: tuple[str, ...] = ()) -> None:
        self.name = name
        self.columns = columns

    def __set_name__(self, owner: type[GObject], attr: str) -> None:
        self.owner, self.attr = owner, attr

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        owner = getattr(self, "owner", None)
        return f"<SubData {owner.__name__ if owner else '?'}.{self.name}>"


@dataclass(frozen=True, slots=True)
class Clause:
    """One condition on a field: ``Bus.BusNomVolt > 100``. ``&`` and ``|`` combine them."""

    field: Field[Any]
    op: Op
    values: tuple[object, ...] = ()

    @property
    def owner(self) -> type[GObject]:
        return self.field.owner

    def inline(self) -> str | None:
        """The condition as Simulator filter text, if every value is a number; else None."""
        if all(isinstance(v, int | float) and not isinstance(v, bool) for v in self.values):
            return " ".join([self.field.name, self.op, *(repr(v) for v in self.values)])
        return None  # text goes through a Condition object, which needs no quoting rules

    def __and__(self, other: Clause | Conditions) -> Conditions:
        return Conditions("AND", (self, other))

    def __or__(self, other: Clause | Conditions) -> Conditions:
        return Conditions("OR", (self, other))


@dataclass(frozen=True, slots=True)
class Conditions:
    """Conditions joined by AND or OR. A nested group becomes a nested Simulator filter."""

    logic: Literal["AND", "OR"]
    clauses: tuple[Clause | Conditions, ...]

    def __post_init__(self) -> None:
        if len({c.owner for c in self.clauses}) != 1:
            names = sorted({c.owner.__name__ for c in self.clauses})
            raise TypeError(f"conditions on different object types cannot combine: {names}")

    @property
    def owner(self) -> type[GObject]:
        return self.clauses[0].owner

    def __and__(self, other: Clause | Conditions) -> Conditions:
        if self.logic == "AND":
            return Conditions("AND", (*self.clauses, other))
        return Conditions("AND", (self, other))

    def __or__(self, other: Clause | Conditions) -> Conditions:
        if self.logic == "OR":
            return Conditions("OR", (*self.clauses, other))
        return Conditions("OR", (self, other))


@dataclass(frozen=True, slots=True, eq=False)
class Schema:
    """Everything known about one object type, reached as ``Type.schema``."""

    type: str  # PowerWorld object type string
    owner: type[GObject]
    attributes: tuple[Field[Any], ...]  # the class attributes, as declared
    keys: tuple[Field[Any], ...]  # primary key, in PowerWorld's order
    alt_key: tuple[Field[Any], ...]  # alternate key, in PowerWorld's order
    subdata: tuple[SubData, ...]  # the lists each object holds
    _index: dict[str, Field[Any]] = field(repr=False)

    @classmethod
    def build(
        cls, owner: type[GObject], pw_type: str, keys: tuple[str, ...], alt_key: tuple[str, ...]
    ) -> Schema:
        attributes = tuple(v for v in vars(owner).values() if isinstance(v, Field))
        index: dict[str, Field[Any]] = {f.name: f for f in attributes}

        def resolve(name: str) -> Field[Any]:
            if name not in index:
                index[name] = _derive(owner, index, name)
            return index[name]

        return cls(
            pw_type,
            owner,
            attributes,
            tuple(map(resolve, keys)),
            tuple(map(resolve, alt_key)),
            tuple(v for v in vars(owner).values() if isinstance(v, SubData)),
            index,
        )

    @property
    def key_names(self) -> list[str]:
        return [f.name for f in self.keys]

    @property
    def key_sets(self) -> tuple[frozenset[str], ...]:
        """Field sets that identify an object: the primary key, then the alternate key."""
        return tuple(frozenset(f.name for f in ks) for ks in (self.keys, self.alt_key) if ks)

    @property
    def fields(self) -> tuple[Field[Any], ...]:
        """Every PowerWorld field of this type, indexed variants included."""
        out: list[Field[Any]] = []
        for f in self.attributes:
            if not f.role & Role.INDEXED:
                out.append(f)
            out.extend(self.field(f"{f.name}:{n}") for n in f.variants or ())
        return tuple(out)

    def field(self, name: str) -> Field[Any]:
        """The field named ``name``.

        Generated fields are always typed (int, float, or str); a name the schema does
        not know becomes an untyped field (``dtype is object``).
        """
        found = self._index.get(name)
        if found is None:
            found = self._index[name] = _derive(self.owner, self._index, name)
        return found


def filter_text(where: object) -> str | None:
    """Simulator's filter argument for a selection, or None if it needs a filter object."""
    match where:
        case str():  # a filter name, or a keyword (Kw is a str)
            return where
        case GObject():
            return f"<DEVICE>{where.who}" if where.key else ""
        case Clause():
            return where.inline()
        case Conditions():
            return None
        case _:
            raise TypeError(f"not a selection: {where!r}")


def _derive(owner: type[GObject], index: dict[str, Field[Any]], name: str) -> Field[Any]:
    """Build the field ``base:n`` from its base field, or an untyped field for an unknown name."""
    base_name, sep, n = name.rpartition(":")
    base = index.get(base_name) if sep and n.isdigit() else None
    made: Field[Any]
    if base is None:
        made = Field(name, object)
        attr = name
    else:
        inherited = base.role & ~(Role.KEY | Role.ALT_KEY | Role.INDEXED)
        spec = (base.variants or {}).get(int(n), inherited)
        role, dtype = spec if isinstance(spec, tuple) else (spec, base.dtype)
        made = Field(name, dtype, role, vocab=base.vocab)
        attr = base.attr
    made.owner, made.attr = owner, attr
    return made


class GObject:
    """The base class of every generated object type.

    The class describes the type (``Bus.schema``, ``Bus.BusPUVolt``). An instance points to
    one object by its primary key: ``Gen(1, "1")``, written ``[GEN 1 "1"]`` in scripts. Keys
    are stored as plain Python values with Simulator's padding removed, so a key read from a
    DataFrame (``Gen(4131, "H ")``) equals the one you type (``Gen(4131, "H")``).
    """

    __slots__ = ("_key",)
    schema: ClassVar[Schema]

    def __init_subclass__(
        cls,
        *,
        pw: str,
        keys: tuple[str, ...] = (),
        alt_key: tuple[str, ...] = (),
        **kwargs: Any,
    ) -> None:
        super().__init_subclass__(**kwargs)
        cls.schema = Schema.build(cls, pw, keys, alt_key)

    def __init__(self, *key: Key) -> None:
        names = self.schema.key_names
        if len(key) != len(names):
            raise TypeError(
                f"{type(self).__name__}({', '.join(names)}) takes {len(names)} key value(s), "
                f"got {len(key)}"
            )
        self._key = tuple(map(plain, key))

    @classmethod
    def parse(cls, text: str) -> Self:
        """The object ``text`` refers to, in either of Simulator's formats: ``Gen '2' '1'``
        (WhoAmI, device filters) or ``GEN 2 1`` (contingency elements).

        ``GObject.parse`` accepts any object type; ``Gen.parse`` accepts only generators.
        Raises ValueError if the text doesn't identify one object by its primary key.
        """
        words = [quoted or bare for quoted, bare in _WORDS.findall(text)]
        gtype = _named(words[0]) if words else None
        if gtype is None or not issubclass(gtype, cls):
            raise ValueError(f"{text!r} does not name {_article(cls)}")
        keys = gtype.schema.keys
        if len(words) - 1 != len(keys):
            raise ValueError(
                f"{text!r} gives {len(words) - 1} key value(s); {gtype.__name__} has {len(keys)}"
            )
        values = (
            k.dtype(v) if k.dtype in (int, float) else v
            for k, v in zip(keys, words[1:], strict=True)
        )
        return gtype(*values)

    @property
    def key(self) -> tuple[Key, ...]:
        """The primary key's values, in PowerWorld's order."""
        return self._key

    @property
    def who(self) -> str:
        """``Gen '2' '1'``: the object's name in WhoAmI, device filters and result requests."""
        keys = [str(k) for k in self._key]
        if any("'" in k for k in keys):
            raise ValueError(f"{self!r}: PowerWorld cannot name an object whose key has a quote")
        return " ".join([self.schema.type, *(f"'{k}'" for k in keys)])

    def __str__(self) -> str:
        tokens = (f'"{v}"' if isinstance(v, str) else str(v) for v in self._key)
        return f"[{' '.join([self.schema.type.upper(), *tokens])}]"

    def __repr__(self) -> str:
        return f"{type(self).__name__}({', '.join(map(repr, self._key))})"

    def __eq__(self, other: object) -> bool:
        return type(other) is type(self) and other._key == self._key  # type: ignore[attr-defined]

    def __hash__(self) -> int:
        return hash((type(self), self._key))


def plain(value: Key) -> Key:
    """A key value as written: text without Simulator's padding, numbers as Python numbers."""
    if isinstance(value, str):
        return value.strip()
    return value.item() if isinstance(value, np.generic) else value  # as frames hold them


_WORDS = re.compile(r"'([^']*)'|(\S+)")  # a key in quotes, or a word


def _named(word: str) -> type[GObject] | None:
    """The generated type ``word`` names, in any case: ``Gen``, ``GEN``."""
    name = _grid_names().get(word.upper())
    return None if name is None else getattr(import_module("esapp.grid"), name)


@cache
def _grid_names() -> dict[str, str]:
    return {name.upper(): name for name in dir(import_module("esapp.grid"))}


def _article(cls: type[GObject]) -> str:
    return "an object" if cls is GObject else f"a {cls.__name__}"
