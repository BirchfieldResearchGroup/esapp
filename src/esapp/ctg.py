"""Static contingencies.

::

    from esapp.ctg import Contingency, close, trip

    n2 = Contingency("Aloha N-2", Branch(1, 5, "1"), Branch(1, 5, "2"))
    pw.ctg.solve(n2).violations      # solves it without changing the case
    pw.ctg.define(n2)                # or saves it in the case

Passing just an object means "open it". :func:`trip` and :func:`close` take a delay, and any
other action Simulator supports for that object type works through ``Action(target, verb)``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from esapp.schema import GObject

__all__ = ["Action", "Contingency", "close", "target", "trip"]


@dataclass(frozen=True, slots=True)
class Action:
    """One contingency element: ``verb`` on ``target``, ``delay`` seconds in.

    ``target`` is an object, or Simulator's text for one (``"BRANCH 1 5 1"``). Elements that
    point at something other than one object, such as a filter, read back as text.
    """

    target: GObject | str
    verb: str = "OPEN"
    delay: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.delay) or self.delay < 0:
            raise ValueError(f"delays are finite seconds >= 0, not {self.delay!r}")

    @property
    def object(self) -> str:
        """The target as Simulator's contingency text: ``BRANCH 1 5 '1'``."""
        if isinstance(self.target, str):
            return self.target
        keys = [f"'{k}'" if isinstance(k, str) else str(k) for k in self.target.key]
        return " ".join([self.target.schema.type.upper(), *keys])


def trip(target: GObject, *, delay: float = 0.0) -> Action:
    """Open ``target``, ``delay`` seconds into the contingency."""
    return Action(target, "OPEN", delay)


def close(target: GObject, *, delay: float = 0.0) -> Action:
    """Close ``target``, ``delay`` seconds into the contingency."""
    return Action(target, "CLOSE", delay)


@dataclass(frozen=True, slots=True, init=False)
class Contingency:
    """A static contingency. ``pw.ctg.solve`` solves it; ``pw.ctg.define`` saves it in the case.

    With ``skip``, Simulator leaves it out when it solves all contingencies.
    """

    name: str
    actions: tuple[Action, ...]
    skip: bool

    def __init__(self, name: str, *actions: Action | GObject, skip: bool = False) -> None:
        if not name or '"' in name or "'" in name:
            raise ValueError(f"contingency names are non-empty and without quotes: {name!r}")
        steps = tuple(a if isinstance(a, Action) else trip(a) for a in actions)
        for attr, value in (("name", name), ("actions", steps), ("skip", skip)):
            object.__setattr__(self, attr, value)


def target(text: str) -> GObject | str:
    """The object that an element's text names: ``"BRANCH 1 5 1"`` gives ``Branch(1, 5, "1")``.
    Text that doesn't name one object by its primary key, such as a filter, comes back as is."""
    try:
        return GObject.parse(text)
    except ValueError:
        return text
