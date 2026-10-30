"""Geomagnetically induced currents: ``pw.gic``."""

from __future__ import annotations

import pandas as pd

from esapp.apps._base import Namespace
from esapp.commands._catalog import GICCalculate, GICClear, GICSaveGMatrix
from esapp.grid import GICXFormer
from esapp.matrix import Matrix, gmatrix_axes, read_matlab

# GICXFIEffective1 is the current this field drives; GICMaxIEffective, the most any heading would.
__all__ = ["GIC"]

_TRANSFORMERS = [GICXFormer.GICXFIEffective1, GICXFormer.GICQLosses]


class GIC(Namespace):
    """Geomagnetically induced currents: ``pw.gic``.

    ::

        pw.gic.calculate(2.0, 90.0)     # a uniform 2 V/km field from the east
        g = pw.gic.gmatrix()
    """

    __slots__ = ()

    def calculate(self, field: float, direction: float, *, solve: bool = False) -> pd.DataFrame:
        """Calculate GICs for a uniform electric field of ``field`` V/km, pointing
        ``direction`` degrees (0 is north, 90 east). With ``solve``, also solve a power flow
        that includes the GIC losses.

        Returns each transformer's effective GIC under this field (GICXFIEffective1, amps per
        phase) and its Mvar losses.
        """
        self._pw.run(GICCalculate(field, direction, solve))
        return self._pw[GICXFormer, _TRANSFORMERS].set_index(GICXFormer.schema.key_names)

    def clear(self) -> None:
        """Remove GIC results and their losses from the power flow."""
        self._pw.run(GICClear())

    def gmatrix(self) -> Matrix:
        """The GIC network's conductance matrix in siemens. Rows and columns are
        (ObjectType, Number): substation grounding nodes first, then buses."""
        pw = self._pw
        path, ids = pw.scratch / "gmatrix.m", pw.scratch / "gmatrix.txt"
        pw.run(GICSaveGMatrix(path, ids))
        axis = gmatrix_axes(ids.read_text(errors="replace"))
        return Matrix(read_matlab(path, "GMatrix", (axis.size, axis.size)), axis, axis)
