"""Transient stability: the fields you can record, contingencies, and results.

::

    from esapp.ts import TS, Contingency, clear_fault, fault

    bus6 = Contingency(
        "bus6", fault(Bus(6), at=1.0), clear_fault(Bus(6), at=1.05), stop=10.0
    )
    sim = pw.ts.solve(bus6, record=[TS.Gen.W, TS.Bus.VPU])
    sim[TS.Bus.VPU]                  # DataFrame: rows are times, columns are buses

``TS.Gen.W`` is the same field as ``Gen.TSGenW``.
"""

from esapp.ts._catalog import TS
from esapp.ts._events import (
    Contingency,
    Cycles,
    End,
    Event,
    FaultKind,
    Unit,
    bypass,
    change_load,
    clear_fault,
    close,
    exciter_setpoint,
    fault,
    governor_setpoint,
    power_setpoint,
    trip,
)
from esapp.ts._plan import Plan, Watch
from esapp.ts._playin import PlayIn
from esapp.ts._recordable import flags
from esapp.ts._result import Result
from esapp.ts._store import Store

__all__ = [
    "TS",
    "Contingency",
    "Cycles",
    "End",
    "Event",
    "FaultKind",
    "Plan",
    "PlayIn",
    "Result",
    "Store",
    "Unit",
    "Watch",
    "bypass",
    "change_load",
    "clear_fault",
    "close",
    "exciter_setpoint",
    "fault",
    "flags",
    "governor_setpoint",
    "power_setpoint",
    "trip",
]
