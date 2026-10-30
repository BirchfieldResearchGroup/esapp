"""Power flow: ``pw.pf``."""

from __future__ import annotations

import numpy as np
import pandas as pd

from esapp.apps._base import Namespace
from esapp.commands._catalog import (
    JacForm,
    ResetToFlatStart,
    SaveJacobian,
    SaveYbusInMatlabFormat,
    SolutionMethod,
    SolvePowerFlow,
)
from esapp.grid import Bus
from esapp.matrix import JacobianForm, Matrix, jacobian_axes, read_matlab

__all__ = ["PowerFlow"]

_JACOBIAN: dict[JacobianForm, JacForm] = {"polar": "P", "rectangular": "R", "dc": "DC"}


class PowerFlow(Namespace):
    """Power flow: ``pw.pf``.

    ::

        pw.pf.solve()                         # Simulator's configured method
        pw.pf.solve("POLARNEWTON", flat=True)
        y, jac = pw.pf.ybus(), pw.pf.jacobian()
    """

    __slots__ = ()

    def solve(self, method: SolutionMethod | None = None, *, flat: bool = False) -> None:
        """Solve the power flow, from a flat start if ``flat``.

        Raises :class:`~esapp.NotConverged` if it doesn't converge. ``"DC"`` switches the case
        to DC power flow; any other method switches it back.
        """
        solve = SolvePowerFlow(method)
        self._pw.run(*((ResetToFlatStart(), solve) if flat else (solve,)))

    def flat_start(self) -> None:
        """Set every voltage to 1.0 pu at the slack bus's angle (Simulator's flat start)."""
        self._pw.run(ResetToFlatStart())

    # ------------------------------------------------------------------ state
    def voltages(self) -> pd.Series:
        """Each bus's complex voltage, in per unit."""
        v = self._pw[Bus, [Bus.BusPUVolt, Bus.BusAngle]].set_index("BusNum")
        return (v["BusPUVolt"] * np.exp(1j * np.deg2rad(v["BusAngle"]))).rename("V")

    def mismatches(self) -> pd.DataFrame:
        """Each bus's power mismatch: BusMismatchP (MW) and BusMismatchQ (Mvar)."""
        return self._pw[Bus, [Bus.BusMismatchP, Bus.BusMismatchQ]].set_index("BusNum")

    def categories(self) -> pd.DataFrame:
        """Each bus's type in the power flow, read from Simulator's BusCat.

        ``kind``: "slack", "pv" or "pq", as the bus is solved right now. ``at_limit``: a
        generator or control is at its var limit. ``remote``: it's part of remote voltage
        control, and ``role`` says how:
        "primary", "secondary", "target" or "". ``svc``: continuous shunt control. ``BusCat``:
        Simulator's own label.
        """
        labels = self._pw[Bus.BusCat]
        parsed = pd.DataFrame([_category(str(text)) for text in labels], index=labels.index)
        return parsed.assign(BusCat=labels.to_numpy())

    # ------------------------------------------------------------------ matrices
    def ybus(self) -> Matrix:
        """The bus admittance matrix in per unit, with rows and columns by bus number.

        Simulator only gives it to four decimal places.
        """
        pw, path = self._pw, self._pw.scratch / "ybus.m"
        pw.run(SaveYbusInMatlabFormat(path, False))
        buses = pd.Index(pw[Bus]["BusNum"], name="BusNum")  # Simulator's order is the read order
        return Matrix(read_matlab(path, "Ybus", (buses.size, buses.size)), buses, buses)

    def jacobian(self, form: JacobianForm = "polar") -> Matrix:
        """The power flow Jacobian, at full precision.

        Rows are (equation, bus): "P" and "Q" for a PQ bus, "P" and "V" for a PV bus, and
        "angle" and "V" for the slack. Rows for fixed quantities are identity rows.

        Columns are (variable, bus): "angle" and "V" in polar form; "e" and "f" (the real and
        imaginary voltage) in rectangular form, where Simulator flips the signs, since it
        differentiates the mismatch; and just "angle" for ``"dc"``, the DC power flow's B'
        matrix.
        """
        pw = self._pw
        jac, ids = pw.scratch / "jacobian.m", pw.scratch / "jacobian.txt"
        pw.run(SaveJacobian(jac, ids, "EXPM", _JACOBIAN[form]))
        rows, columns = jacobian_axes(ids.read_text(errors="replace"), form)
        return Matrix(read_matlab(jac, "Jac", (rows.size, columns.size)), rows, columns)


def _category(text: str) -> dict[str, object]:
    s = text.lower()
    kind = "slack" if "slack" in s else "pv" if s.startswith("pv") else "pq"
    role = (
        "target"
        if "remotely regulated" in s or "droop reg bus" in s
        else "secondary"
        if "secondary" in s or "droop remote bus" in s
        else "primary"
        if "primary" in s or "local/remote" in s
        else ""
    )
    return {
        "kind": kind,
        "at_limit": "limit" in s,
        "remote": "remote" in s or "droop" in s,
        "role": role,
        "svc": "svc" in s or "continuous" in s,
    }
