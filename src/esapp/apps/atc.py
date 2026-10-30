"""Available transfer capability: ``pw.atc``."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from esapp.apps._base import Namespace
from esapp.commands._catalog import ATCDeleteAllResults, ATCDetermine
from esapp.grid import TransferLimiter
from esapp.schema import GObject
from esapp.simauto._script import is_yes

__all__ = ["ATC", "Transfer"]

_LIMITERS = [
    TransferLimiter.CTGViolMaxLine,  # the transfer limit, MW beyond the present transfer
    TransferLimiter.ReportLimit,
    TransferLimiter.CTGLabel,
    TransferLimiter.LimViolID,
    TransferLimiter.LimViolValue,
    TransferLimiter.LimViolLimit,
    TransferLimiter.LinePTDF,
    TransferLimiter.LineMW,
]


@dataclass(frozen=True, slots=True)
class Transfer:
    """An ATC result."""

    limit: float
    """How many more MW can move beyond the present transfer (NaN if nothing limits it)."""
    limiters: pd.DataFrame
    """Each limiting element, most limiting first: the transfer limit in MW
    (CTGViolMaxLine), the contingency (CTGLabel), the element (LimViolID), its flow
    (LimViolValue) and limit (LimViolLimit), and its PTDF or OTDF in percent (LinePTDF)."""


class ATC(Namespace):
    """Available transfer capability: ``pw.atc``.

    ::

        transfer = pw.atc.determine(Area(1), Area(2))
        transfer.limit, transfer.limiters.head()
    """

    __slots__ = ()

    def determine(self, seller: GObject, buyer: GObject, *, distributed: bool = False) -> Transfer:
        """Find how many more MW can move from ``seller`` to ``buyer``. The case's ATC
        options set the contingencies, limits and linear method."""
        self._pw.run(ATCDetermine(seller, buyer, distributed))
        limiters = self._pw[TransferLimiter, _LIMITERS]
        reported = limiters["ReportLimit"].map(is_yes).astype(bool)
        limiters = limiters.drop(columns="ReportLimit")
        limit = limiters.loc[reported, "CTGViolMaxLine"]
        return Transfer(
            float(limit.iloc[0]) if len(limit) else math.nan,
            limiters.sort_values("CTGViolMaxLine", kind="stable").reset_index(drop=True),
        )

    def clear(self) -> None:
        """Remove the ATC results."""
        self._pw.run(ATCDeleteAllResults())
