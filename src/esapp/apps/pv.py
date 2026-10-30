"""PV curves: ``pw.pv``."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from esapp._aux import entries, read_text
from esapp._indexing import Where
from esapp.apps._base import Namespace
from esapp.commands._catalog import PVClear, PVRun, SaveData
from esapp.errors import PrerequisiteError
from esapp.grid import Bus, InjectionGroup, PWPVResultListContainer
from esapp.simauto._script import is_yes

__all__ = ["PV", "PvResults"]

_SUMMARY = [
    PWPVResultListContainer.PVResultMaxShift,
    PWPVResultListContainer.PVResultNominalShift,
    PWPVResultListContainer.PVResultMaxExport,
    PWPVResultListContainer.PVResultMaxImport,
    PWPVResultListContainer.PVResultCritical,
    PWPVResultListContainer.PVResultCriticalReason,
    PWPVResultListContainer.PVResultWorstVolt,
    PWPVResultListContainer.PVResultWorstVoltBus,
    PWPVResultListContainer.PVResultNumViol,
]
_AXES = {"INDNOM": "shift", "INDEXP": "export", "INDIMP": "import"}


@dataclass(frozen=True, slots=True)
class PvResults:
    """A PV study's results: one scenario per contingency, plus "Base Case"."""

    summary: pd.DataFrame
    """One row per scenario, indexed by PVContingency: how far the transfer went (MW), and
    why it stopped."""
    curves: pd.DataFrame
    """Every tracked value at every step, one row each: PVContingency, step, shift (nominal
    MW), export and import (MW), object (its WhoAmI text), field and value."""

    def curve(self, scenario: str = "Base Case") -> pd.DataFrame:
        """One scenario's tracked values: rows are the MW moved, columns (object, field)."""
        mine = self.curves[self.curves["PVContingency"] == scenario]
        return mine.pivot_table(index="shift", columns=["object", "field"], values="value")


class PV(Namespace):
    """PV curves: ``pw.pv``.

    ::

        src = pw.groups.create("src", Gen, Gen.AreaNum.eq(1), share="MAX GEN INC")
        sink = pw.groups.create("sink", Load, share="LOAD MW")
        pv = pw.pv.solve(src, sink, buses=Bus.BusNomVolt >= 100)
        pv.curve()                         # bus voltages by MW transferred
    """

    __slots__ = ()

    def solve(
        self, source: InjectionGroup, sink: InjectionGroup, *, buses: Where | None = None
    ) -> PvResults:
        """Move power from ``source`` to ``sink`` until the system or the source can't go
        further, for the base case and each contingency, using the case's PV options.

        ``buses``: track the voltage at these buses (their tracking settings are restored
        afterwards). Other values are tracked if the case's PVMon settings say so, such as
        ``Gen.PVMonGenMVR``.

        Raises :class:`~esapp.PrerequisiteError` when Simulator refuses the study without
        reporting an error. Its message log says why, for example when the slack bus is in
        both the source and the sink.

        On a large case the study can take minutes. To cap the transfer for one study::

            with pw.options({PVCurve_Options.PVCCapped: True, PVCurve_Options.PVCMaxShift: 5.0}):
                pw.pv.solve(source, sink)  # at most 500 MW, on the 100 MVA base
        """
        pw = self._pw
        tracked = pw[Bus, Bus.PVmonBusVoltage] if buses is not None else None
        try:
            if buses is not None:
                pw[Bus, Bus.PVmonBusVoltage] = "NO"
                pw[Bus, Bus.PVmonBusVoltage, buses] = "YES"
            pw.run(PVRun(source, sink))
        finally:
            if tracked is not None:
                pw[Bus] = tracked
        results = self.results()
        if results.summary.empty:
            raise PrerequisiteError(
                "PVRun gave no results: Simulator declined the study; see pw.log.text()",
                method="PVRun",
            )
        return results

    def results(self) -> PvResults:
        """The results Simulator holds."""
        pw = self._pw
        summary = pw[PWPVResultListContainer, _SUMMARY].set_index("PVContingency")
        summary["PVResultCritical"] = summary["PVResultCritical"].map(is_yes)
        path = pw.scratch / "pv.aux"
        path.unlink(missing_ok=True)
        container = PWPVResultListContainer
        pw.run(SaveData(path, "AUX", container, ["PVContingency"], ["PWPVResultObject"], ""))
        return PvResults(summary, _curves(read_text(path)))

    def clear(self) -> None:
        """Delete the results."""
        self._pw.run(PVClear())


def _curves(text: str) -> pd.DataFrame:
    """PWPVResultObject lines: three axis rows (INDNOM, INDEXP, INDIMP), then one row per
    tracked quantity, ``Bus '3'?f=BusPUVolt&v=0.98455 0.98291 ...``."""
    rows: list[tuple[object, ...]] = []
    axes: dict[str, list[float]] = {}
    for (scenario,), point, line in entries(text, 1, "PWPVResultObject"):
        if point == 0:
            axes = {}
        head, _, rest = line.partition(" ")
        if head in _AXES:
            axes[_AXES[head]] = [float(v) for v in rest.split()]
            continue
        target, _, after = line.partition("?f=")
        field, _, values = after.partition("&v=")
        for step, value in enumerate(values.split()):
            at = [_at(axes.get(axis, []), step) for axis in _AXES.values()]
            rows.append((scenario, step, *at, target.strip(), field, float(value)))
    columns = ["PVContingency", "step", *_AXES.values(), "object", "field", "value"]
    return pd.DataFrame(rows, columns=columns)


def _at(values: list[float], step: int) -> float:
    return values[step] if step < len(values) else float("nan")
