"""Script commands as Python objects: ``str(cmd)`` is the script text; ``pw.run(cmd)`` runs it."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, fields
from typing import Any, ClassVar

from esapp.schema._core import filter_text
from esapp.simauto._script import Bare, script

type Select = Callable[[Any], object]
"""Turns a selection (a Where) into Simulator's filter argument."""

type Word = str | float | bool
"""An unquoted argument: a keyword (``"POLARNEWTON"``), a number, or YES/NO as a bool."""


@dataclass(frozen=True, slots=True)
class Command:
    """One script command. :mod:`esapp.commands` has a generated subclass for every command
    PowerWorld documents, with its parameters typed and in order.

    ``None`` means "use Simulator's default". ``pw.run(*commands)`` sends several in one call.
    """

    name: ClassVar[str]
    section: ClassVar[str] = ""
    mutates: ClassVar[bool] = True  # False: leaves the case as it was (runs read-only)
    filters: ClassVar[frozenset[str]] = frozenset()  # parameters taking a selection
    bare: ClassVar[frozenset[str]] = frozenset()  # parameters whose text goes unquoted
    since: ClassVar[str | None] = None  # build date (YYYY-MM) of the first Simulator with it

    def __init_subclass__(
        cls,
        *,
        name: str | None = None,
        section: str | None = None,
        mutates: bool | None = None,
        filters: Iterable[str] | None = None,
        bare: Iterable[str] | None = None,
        since: str | None = None,
        **kwargs: Any,
    ) -> None:
        # Named, not super(): @dataclass(slots=True) replaces the class this body belongs to,
        # and calls this again without the keywords: what the first call set is kept.
        super(Command, cls).__init_subclass__(**kwargs)
        own = vars(cls)
        cls.name = name or own.get("name") or cls.__name__
        if section is not None:
            cls.section = section
        if mutates is not None:
            cls.mutates = mutates
        if filters is not None:
            cls.filters = frozenset(filters)
        if bare is not None:
            cls.bare = frozenset(bare)
        if since is not None:
            cls.since = since

    def arguments(self) -> list[object]:
        return [getattr(self, f.name) for f in fields(self)]

    def statement(self, select: Select | None = None) -> str:
        """The script text, the same as ``str(cmd)``. A selection that needs a filter object
        in the case can't be written here: run the command with ``pw.run``, which creates
        the filter."""
        pick = select or _plain
        args: list[object] = []
        for f, value in zip(fields(self), self.arguments(), strict=True):
            if value is not None and f.name in self.filters:
                value = pick(value)
            elif f.name in self.bare:
                value = _bare(value)
            args.append(value)
        return script(self.name, *args)

    def __str__(self) -> str:
        return self.statement()


def _bare(value: object) -> object:
    match value:
        case str():
            return Bare(value)
        case list() | tuple():
            return [_bare(item) for item in value]  # pyright: ignore[reportUnknownVariableType]
        case _:
            return value


def _plain(where: object) -> str:
    """A selection that needs no filter objects: a name, a keyword, an object, one condition."""
    if (text := filter_text(where)) is None:
        raise TypeError("these conditions need a filter object: run the command with pw.run")
    return text
