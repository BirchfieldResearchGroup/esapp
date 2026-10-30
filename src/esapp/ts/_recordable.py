"""How the catalog works: ``TS.<Type>.<Name>`` is the owner's schema ``Field``.

``TS.Gen.Delta is Gen.TSGenDelta``. The catalog adds the one thing the schema doesn't have:
which TSSave flags make Simulator record each field.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, ClassVar

from esapp.schema import Field, GObject

_FLAGS: dict[tuple[str, str], tuple[str, ...]] = {}  # (grid class, base name) -> TSSave flags
_GROUPS: dict[str, type[Group]] = {}  # grid class -> its catalog group


class Group:
    """The recordable fields of one object type: ``TS.Gen``."""

    of: ClassVar[str]  # the esapp.grid class
    _attrs: ClassVar[tuple[str, ...]]

    def __init_subclass__(cls, *, of: str, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        entries = {attr: item for attr, item in vars(cls).items() if isinstance(item, entry)}
        cls.of, cls._attrs = of, tuple(entries)
        _GROUPS[of] = cls
        for item in entries.values():
            _FLAGS[of, item.name] = item.flags

    @classmethod
    def gtype(cls) -> type[GObject]:
        return getattr(import_module("esapp.grid"), cls.of)

    @classmethod
    def fields(cls) -> list[Field[Any]]:
        return [getattr(cls, attr) for attr in cls._attrs]


class entry:
    """A catalog entry. On first access it looks up the owner's ``Field`` and replaces itself
    with it."""

    __slots__ = ("attr", "flags", "name")

    def __init__(self, name: str, *flags: str) -> None:
        self.name = name
        self.flags = flags

    def __set_name__(self, owner: type, attr: str) -> None:
        self.attr = attr

    def __get__(self, obj: object, owner: type[Group]) -> Field[Any]:
        field = owner.gtype().schema.field(self.name)
        setattr(owner, self.attr, field)
        return field


def flags(field: Field[Any]) -> tuple[Field[Any], ...]:
    """The TSSave flags that make Simulator record ``field``; any one of them is enough.

    Empty if ``field`` can't be recorded. An indexed field such as ``Field[2]`` has the same
    flags as its base field.
    """
    _catalog()
    owner = field.owner
    names = _FLAGS.get((owner.__name__, field.name.partition(":")[0]), ())
    return tuple(owner.schema.field(name) for name in names)


def fields_of(gtype: type[GObject]) -> list[Field[Any]]:
    """Every recordable field of ``gtype``; indexed ones as their base field."""
    _catalog()
    group = _GROUPS.get(gtype.__name__)
    return [] if group is None else group.fields()


def recordable() -> list[type[GObject]]:
    """The object types whose TSSave flags decide what Simulator records."""
    grid = import_module("esapp.grid")
    return [getattr(grid, name) for name in _catalog().RECORDABLE]


def _catalog() -> Any:
    return import_module("esapp.ts._catalog")
