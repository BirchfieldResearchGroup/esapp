"""Fault analysis: ``pw.fault``."""

from __future__ import annotations

import pandas as pd

from esapp.apps._base import Namespace
from esapp.commands._catalog import FaultClear, FaultType
from esapp.grid import Branch, Bus
from esapp.simauto import Bare

__all__ = ["Faults"]

_RESULTS = [Bus.FaultCurMag, Bus.FaultCurAng, Bus.FltSeqV, Bus.FltSeqAngle]


class Faults(Namespace):
    """Fault analysis: ``pw.fault``.

    ::

        pw.fault.calculate(Bus(5), "SLG")
        pw.fault.calculate(Branch(1, 5, "1"), "3PB", at=50.0)    # halfway along
    """

    __slots__ = ()

    def calculate(
        self,
        element: Bus | Branch,
        kind: FaultType = "3PB",
        *,
        at: float | None = None,
        r: float = 0.0,
        x: float = 0.0,
    ) -> pd.DataFrame:
        """Fault ``element`` through impedance ``r + jx`` (per unit), and return each bus's
        fault current and sequence voltages.

        A branch fault needs ``at``: how far along the branch, in percent, from its first bus.
        Simulator 24 crashes on a fault partway along a transformer, so fault one of its buses
        instead.
        """
        if isinstance(element, Branch) != (at is not None):
            raise ValueError("a branch fault needs its location, at=; a bus fault takes none")
        place = [element] if at is None else [element, float(at)]
        self._pw.run("Fault", *place, Bare(kind), float(r), float(x))
        return self._pw[Bus, _RESULTS].set_index("BusNum")

    def clear(self) -> None:
        """Remove the fault and its results."""
        self._pw.run(FaultClear())
