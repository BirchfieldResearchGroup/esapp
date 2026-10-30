"""Transient contingencies and their events.

The helper functions write PowerWorld's event text for you. For any other event, pass the
text yourself: ``Event(target, at, action)``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from esapp.schema import GObject

type FaultKind = Literal["3PB", "SLG", "LL", "DLG", "1PhaseOpen"]
type End = Literal["both", "near", "far"]
type Unit = Literal["MW", "%"]

_BOTH_ENDS = frozenset({"Branch", "Transformer"})  # Simulator rejects a bare OPEN on a branch


@dataclass(frozen=True, slots=True)
class Cycles:
    """A duration in cycles of nominal frequency: ``step=Cycles(0.25)``."""

    n: float


@dataclass(frozen=True, slots=True)
class Event:
    """One event: PowerWorld's event text ``action``, applied to ``target`` at ``at`` seconds."""

    target: GObject
    at: float
    action: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.at) or self.at < 0:
            raise ValueError(f"event times are finite seconds >= 0, not {self.at!r}")

    @property
    def who(self) -> str:
        return self.target.who


# ---------------------------------------------------------------------- faults
def fault(
    target: GObject,
    *,
    at: float,
    kind: FaultKind = "3PB",
    z: complex | None = None,
    y: complex | None = None,
    location: float | None = None,
    self_clear: bool = False,
) -> Event:
    """A fault of ``kind``: solid, or through impedance ``z`` or admittance ``y`` (per unit).

    A branch fault needs ``location``: how far along the branch, in percent, from the near
    end. On a transformer, Simulator moves it to the nearer end.
    """
    match z, y:
        case None, None:
            across = "SOLID"
        case (complex() | float() | int()) as zz, None:
            across = f"IMP {_number(complex(zz).real)} {_number(complex(zz).imag)}"
        case None, (complex() | float() | int()) as yy:
            across = f"ADM {_number(complex(yy).real)} {_number(complex(yy).imag)}"
        case _:
            raise ValueError("a fault has an impedance or an admittance, not both")
    two_ended = target.schema.type in _BOTH_ENDS
    if two_ended != (location is not None):
        raise ValueError("a branch fault needs a location (percent); a bus fault takes none")
    where = "" if location is None else f"{_number(location)} "
    return Event(target, at, f"FAULT {where}{kind} {across}{' SELFCLEAR' if self_clear else ''}")


def clear_fault(target: GObject, *, at: float) -> Event:
    """Clear the fault on ``target``."""
    return Event(target, at, "CLEARFAULT")


# ---------------------------------------------------------------------- switching
def trip(target: GObject, *, at: float, end: End = "both") -> Event:
    """Open ``target``: a branch at both ends, or just the near or far one."""
    return Event(target, at, _switch("OPEN", target, end))


def close(target: GObject, *, at: float, end: End = "both") -> Event:
    """Close ``target``: a branch at both ends, or just the near or far one."""
    return Event(target, at, _switch("CLOSE", target, end))


def bypass(target: GObject, *, at: float, bypassed: bool = True) -> Event:
    """Bypass a series capacitor or reactor (or put it back in, ``bypassed=False``)."""
    return Event(target, at, "BYPASS" if bypassed else "NOTBYPASS")


# ---------------------------------------------------------------------- setpoints
def exciter_setpoint(gen: GObject, pu: float, *, at: float, ramp: float | None = None) -> Event:
    """Change a generator's exciter setpoint (per unit), at once or ramped over ``ramp``
    seconds."""
    return _setpoint(gen, f"Exciter_Setpoint {_number(pu)} pu", at, ramp)


def governor_setpoint(
    gen: GObject, percent: float, *, at: float, ramp: float | None = None
) -> Event:
    """Change a generator's governor setpoint (percent), at once or ramped over ``ramp``
    seconds."""
    return _setpoint(gen, f"Governor_Setpoint {_number(percent)} %", at, ramp)


def power_setpoint(
    gen: GObject, value: float, unit: Unit = "MW", *, at: float, ramp: float | None = None
) -> Event:
    """Change a generator's power setpoint, in MW or percent. Simulator ignores it if the
    generator has a dynamic model."""
    return _setpoint(gen, f"Power {_number(value)} {unit}", at, ramp)


def change_load(load: GObject, by: float, unit: Unit = "%", *, at: float) -> Event:
    """Change a load by ``by`` MW or percent (``change_load(l, -30, "%", at=1.0)``)."""
    return Event(load, at, f"CHANGEBY {_number(by)} {unit}")


def _switch(verb: str, target: GObject, end: End) -> str:
    return f"{verb} {end.upper()}" if target.schema.type in _BOTH_ENDS else verb


def _setpoint(gen: GObject, what: str, at: float, ramp: float | None) -> Event:
    return Event(gen, at, f"SET {what}" if ramp is None else f"RAMP {what} {_number(ramp)}")


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


@dataclass(frozen=True, slots=True, init=False)
class Contingency:
    """A transient contingency. ``pw.ts.solve`` simulates it without changing the case;
    ``pw.ts.define`` saves it in the case."""

    name: str
    events: tuple[Event, ...]  # in time order
    stop: float
    start: float
    step: float | Cycles | None  # seconds or Cycles(n); None: Simulator's default

    def __init__(
        self,
        name: str,
        *events: Event,
        stop: float,
        start: float = 0.0,
        step: float | Cycles | None = None,
    ) -> None:
        if '"' in name or "'" in name:
            raise ValueError(f"contingency names cannot contain quotes: {name!r}")
        if not 0 <= start < stop:
            raise ValueError(f"need 0 <= start < stop, got start={start!r}, stop={stop!r}")
        ordered = tuple(sorted(events, key=lambda e: e.at))
        for attr, value in (
            ("name", name),
            ("events", ordered),
            ("stop", stop),
            ("start", start),
            ("step", step),
        ):
            object.__setattr__(self, attr, value)
