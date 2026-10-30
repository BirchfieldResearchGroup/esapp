"""Sparse matrices from Simulator, with labelled rows and columns.

``pw.pf.ybus()``, ``pw.pf.jacobian()`` and ``pw.gic.gmatrix()`` return a :class:`Matrix`.
They need SciPy: ``pip install esapp[sparse]``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd

__all__ = ["JacobianForm", "Matrix"]

if TYPE_CHECKING:
    from scipy.sparse import csr_array

type JacobianForm = Literal["polar", "rectangular", "dc"]
"""Which Jacobian: polar, rectangular, or the DC power flow's B' matrix."""

_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_ID_ROW = re.compile(r"^\s*(\d+)\s+(.*?)\s+(Slack|PV|PQ)\s+\S+\s+\S+\s+(\d+)\s+(\d+)\s*$")


@dataclass(frozen=True, slots=True, eq=False)
class Matrix:
    """A sparse matrix with labelled rows and columns. ``values`` is a SciPy CSR array;
    ``rows`` and ``columns`` are pandas indexes."""

    values: csr_array
    rows: pd.Index
    columns: pd.Index

    @property
    def shape(self) -> tuple[int, int]:
        """The number of rows and columns."""
        return self.values.shape

    def to_frame(self) -> pd.DataFrame:
        """The matrix as a dense, labelled DataFrame. Use it only on small systems."""
        return pd.DataFrame(self.values.toarray(), index=self.rows, columns=self.columns)

    def __repr__(self) -> str:
        (n, m), axes = self.shape, f"{self.rows.names} by {self.columns.names}"
        return f"<Matrix {n} x {m}, {self.values.nnz} nonzero: {axes}>"


def read_matlab(path: Path, name: str, shape: tuple[int, int]) -> csr_array:
    """``name(i,j) = a`` and ``name(i,j) = a + j*(b)`` lines, 1-based, as CSR."""
    sparse = scipy_sparse()
    entry = (
        rf"{name}\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*=\s*({_NUMBER})\s*(?:\+\s*j\*\(\s*({_NUMBER})\s*\))?"
    )
    found = re.findall(entry, path.read_text())
    if not found:
        return sparse.csr_array(shape)
    i, j, real, imag = (np.array(column) for column in zip(*found, strict=True))
    values: np.ndarray[Any, Any] = real.astype(float)
    if (imag != "").any():
        values = values + 1j * np.where(imag == "", "0", imag).astype(float)
    return sparse.csr_array((values, (i.astype(int) - 1, j.astype(int) - 1)), shape=shape)


def jacobian_axes(ids: str, form: JacobianForm) -> tuple[pd.MultiIndex, pd.MultiIndex]:
    """Rows (equation, bus) and columns (variable, bus) from the Jacobian's ID table.

    Every bus gets its rows. The slack bus's rows fix its angle and voltage, and a PV bus's
    second row fixes its voltage; those rows hold identity entries.
    """
    second = {"PQ": "Q", "PV": "V", "Slack": "V"}
    first = {"PQ": "P", "PV": "P", "Slack": "angle"}
    variables = {"polar": ("angle", "V"), "rectangular": ("e", "f"), "dc": ("angle", "")}[form]
    rows: dict[int, tuple[str, int]] = {}
    columns: dict[int, tuple[str, int]] = {}
    for line in ids.splitlines():
        if (found := _ID_ROW.match(line)) is None:
            continue
        bus, kind, p, q = int(found[1]), found[3], int(found[4]), int(found[5])
        rows[p], columns[p] = (first[kind], bus), (variables[0], bus)
        if form != "dc":
            rows[q], columns[q] = (second[kind], bus), (variables[1], bus)

    def index(labels: dict[int, tuple[str, int]], names: list[str]) -> pd.MultiIndex:
        return pd.MultiIndex.from_tuples([labels[k] for k in sorted(labels)], names=names)

    return index(rows, ["equation", "BusNum"]), index(columns, ["variable", "BusNum"])


def gmatrix_axes(ids: str) -> pd.MultiIndex:
    """The G-matrix's rows and columns, (ObjectType, Number), from its ID file."""
    entries: dict[int, tuple[str, int]] = {}
    for line in ids.splitlines()[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and parts[1].lstrip("-").isdigit() and parts[2].isdigit():
            entries[int(parts[2])] = (parts[0], int(parts[1]))
    return pd.MultiIndex.from_tuples(
        [entries[k] for k in sorted(entries)], names=["ObjectType", "Number"]
    )


def scipy_sparse() -> Any:
    try:
        import scipy.sparse
    except ImportError:
        raise ImportError("esapp's matrices need SciPy: pip install esapp[sparse]") from None
    return scipy.sparse
