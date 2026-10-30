"""Linear sensitivities: ``pw.sens``."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

import numpy as np
import pandas as pd

from esapp.apps._base import Namespace
from esapp.commands._catalog import (
    CalculateLODF,
    CalculatePTDF,
    CalculateShiftFactors,
    CalculateVoltSense,
    LinearMethod,
)
from esapp.grid import Branch, Bus
from esapp.schema import Field, GObject

__all__ = ["Sensitivity"]

_VOLTAGE = [Bus.BusgenericSensP, Bus.BusgenericSensQ, Bus.BusSensdAngledP, Bus.BusSensdAngledQ]
_ISLANDS = 1e3  # Simulator marks an islanding outage's LODFs 1e6; no real one nears this


class Sensitivity(Namespace):
    """Linear sensitivities: ``pw.sens``. Values are fractions, by branch key.

    ::

        pw.sens.ptdf(Area(1), Area(2))        # flow change per MW moved from area 1 to 2
        pw.sens.lodf(Branch(1, 5, "1"))       # flow change per MW of the outaged branch's flow
        pw.sens.voltage(Bus(5))               # bus 5's voltage per MW and Mvar at each bus
    """

    __slots__ = ()

    def ptdf(self, seller: GObject, buyer: GObject, method: LinearMethod = "DC") -> pd.Series:
        """Power transfer distribution factors: the fraction of a transfer from ``seller`` to
        ``buyer`` (a bus, area, zone, super area, injection group, ...) that each branch
        carries."""
        self._pw.run(CalculatePTDF(seller, buyer, method))
        return self._branches(Branch.LinePTDF, "ptdf")

    def lodf(self, outage: Branch, method: LinearMethod = "DC") -> pd.Series:
        """Line outage distribution factors: the fraction of ``outage``'s flow that moves onto
        each branch when ``outage`` opens. The outaged branch itself is -1.

        An outage that islands buses, such as a radial line, has no LODFs. Simulator returns
        huge values for it, so esapp returns NaN for every branch.
        """
        self._pw.run(CalculateLODF(outage, method))
        factors = self._branches(Branch.LineLODF, "lodf")
        return factors * np.nan if (factors.abs() > _ISLANDS).any() else factors

    def lodf_matrix(self, outages: Iterable[Branch], method: LinearMethod = "DC") -> pd.DataFrame:
        """LODFs for several outages: a row per branch, a column per outage. An outage that
        islands buses is a column of NaN."""
        columns = {outage.key: self.lodf(outage, method) for outage in outages}
        return pd.DataFrame(columns)

    def voltage(self, bus: Bus) -> pd.DataFrame:
        """How much ``bus``'s voltage changes per MW and per Mvar injected at each bus, at the
        present solution. Magnitude is in per unit (BusgenericSensP, BusgenericSensQ); angle is
        BusSensdAngledP and BusSensdAngledQ. Indexed by bus number."""
        self._pw.run(CalculateVoltSense(bus))
        return self._pw[Bus, _VOLTAGE].set_index("BusNum")

    def shift_factors(
        self,
        element: GObject,
        transactor: GObject,
        *,
        direction: Literal["BUYER", "SELLER"] = "BUYER",
        method: LinearMethod = "DC",
    ) -> pd.Series:
        """Each bus's shift factor on ``element`` (a branch or interface): the fraction of 1 MW
        that flows on ``element`` when the bus injects it and ``transactor`` (an area, zone,
        injection group, bus, ...) absorbs it. With ``direction="SELLER"`` the transactor
        injects and the bus absorbs, which flips every sign."""
        self._pw.run(CalculateShiftFactors(element, direction, transactor, method))
        return (
            self._pw[Bus, Bus.BusgenericSensP]
            .set_index("BusNum")["BusgenericSensP"]
            .rename("shift_factor")
        )

    def _branches(self, field: Field[float], name: str) -> pd.Series:
        frame = self._pw[Branch, field].set_index(Branch.schema.key_names)
        return (frame[field.name] / 100.0).rename(name)
