"""QV curves: ``pw.qv``."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from esapp._aux import read_text
from esapp._indexing import Where
from esapp.apps._base import Namespace
from esapp.commands._catalog import QVDeleteAllResults, QVRun
from esapp.grid import Bus, QVCurve

__all__ = ["QV", "QvResults"]

_CURVES = [QVCurve.qv_Q0, QVCurve.qv_Qmin, QVCurve.qv_VQmin, QVCurve.qv_Vmin, QVCurve.qv_Vmax]
_HEADER = re.compile(r"\*\*BUS\*\*\s*(\d+).*\*\*CASE\*\*\s*(.*?)\s*$")


@dataclass(frozen=True, slots=True)
class QvResults:
    """A QV study's results: one curve per bus and contingency."""

    summary: pd.DataFrame
    """One row per curve, indexed by BusNum and CaseName: the Mvar at the operating point
    (qv_Q0) and at the nose (qv_Qmin, at voltage qv_VQmin), and the voltage range covered."""
    curves: pd.DataFrame
    """Every point of every curve, one row each: BusNum, CaseName, V (per unit) and Q (Mvar)."""

    def curve(self, bus: int, case: str = "BASECASE") -> pd.Series:
        """One curve: Q (Mvar) by V (per unit)."""
        mine = self.curves[(self.curves["BusNum"] == bus) & (self.curves["CaseName"] == case)]
        return mine.set_index("V")["Q"]


class QV(Namespace):
    """QV curves: ``pw.qv``.

    ::

        qv = pw.qv.solve(Bus.BusNomVolt >= 100)    # a curve at each bus of 100 kV and up
        qv.curve(5)                                # bus 5: Q by V
    """

    __slots__ = ()

    def solve(self, buses: Where, *, distributed: bool = False) -> QvResults:
        """QV curves at the buses ``buses`` selects, using the case's QV options. Each bus's QV
        setting is restored afterwards."""
        pw = self._pw
        chosen = pw[Bus, Bus.QVSelected]
        path = pw.scratch / "qv.csv"
        path.unlink(missing_ok=True)
        try:
            pw[Bus, Bus.QVSelected] = "NO"
            pw[Bus, Bus.QVSelected, buses] = "YES"
            pw.run(QVRun(path, False, distributed))
        finally:
            pw[Bus] = chosen
        summary = pw[QVCurve, _CURVES].set_index(QVCurve.schema.key_names)
        return QvResults(summary, _curves(read_text(path)))

    def clear(self) -> None:
        """Remove the QV results."""
        self._pw.run(QVDeleteAllResults())


def _curves(text: str) -> pd.DataFrame:
    """QVRun's file: a ``**BUS** 5 (NAME),**CASE** BASECASE`` line, a line of column names, then
    a row per point: V, Q_sync, Q_shunt, Q_tot, Q_res, Q_tot_res."""
    rows: list[tuple[int, str, float, float]] = []
    bus, case = 0, ""
    for line in text.splitlines():
        if found := _HEADER.search(line):
            bus, case = int(found[1]), found[2]
        elif line[:1].isdigit() and bus:
            values = line.split(",")
            rows.append((bus, case, float(values[0]), float(values[3])))
    columns = ["BusNum", "CaseName", "V", "Q"]
    return pd.DataFrame(rows, columns=columns).drop_duplicates(ignore_index=True)
