"""Optimal power flow: ``pw.opf``."""

from __future__ import annotations

import pandas as pd

from esapp.apps._base import Namespace
from esapp.commands._catalog import SolveFullSCOPF, SolvePrimalLP
from esapp.grid import Bus

__all__ = ["OPF"]

_PRICES = [Bus.BusMCMW, Bus.BusMWMCEnergy, Bus.BusMWMCCongestion, Bus.BusMWMCLosses]


class OPF(Namespace):
    """Optimal power flow: ``pw.opf``.

    ::

        pw.opf.solve()
        pw.opf.lmp()          # $/MWh by bus: price, energy, congestion, losses
    """

    __slots__ = ()

    def solve(self, *, security: bool = False) -> None:
        """Solve the OPF (primal LP), or the security-constrained OPF with ``security``.

        If Simulator can't enforce a limit, it still solves, and prices include its penalty for
        the violation: ``pw.log.text()`` reports "unenforceable constraints"."""
        self._pw.run(SolveFullSCOPF() if security else SolvePrimalLP())

    def lmp(self) -> pd.DataFrame:
        """Each bus's marginal MW cost and its energy, congestion and loss parts."""
        return self._pw[Bus, _PRICES].set_index("BusNum")
