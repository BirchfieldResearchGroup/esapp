"""Transient results across COM: bounded requests decoded into preallocated buffers.

SimAuto sends every value, time included, as a decimal string: about 60 bytes each in
Python against 4 in the result. Requests are cut so one reply holds about ``CELLS`` values,
which caps the transfer's peak near 60 MiB whatever the size of the result.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import partial
from itertools import batched
from typing import Any, NamedTuple

import numpy as np
import numpy.typing as npt

from esapp.errors import EsappError, ObjectNotFound, ResultsUnavailable
from esapp.simauto import SimAuto
from esapp.ts._plan import Pair, Plan

CELLS = 1 << 20

type Values = npt.NDArray[np.floating[Any]]


class Buffers(NamedTuple):
    time: npt.NDArray[np.float64]  # (T,)
    values: Values  # (T, C), column-major


type Allocate = Callable[[int, int], Buffers]
"""Buffers for ``rows`` time points of ``cols`` columns: in memory, or memory-mapped files."""


def memory(dtype: npt.DTypeLike = np.float32) -> Allocate:
    """Buffers in RAM. float32 is exact: Simulator keeps its results in single precision."""

    def allocate(rows: int, cols: int) -> Buffers:
        return Buffers(np.empty(rows), np.empty((rows, cols), dtype, order="F"))

    return allocate


def fetch(
    simauto: SimAuto,
    ctg: str,
    plan: Plan,
    allocate: Allocate,
    start: float | None = None,
    stop: float | None = None,
    *,
    cells: int = CELLS,
) -> Buffers:
    """``plan``'s fields of ``ctg`` into buffers from ``allocate``, a bounded slice at a time.

    Raises :class:`ResultsUnavailable` when Simulator holds no data for any of them.
    """
    if not plan.width:
        return allocate(0, 0)
    pull = partial(_pull, simauto, ctg, start=start, stop=stop)
    # The first field that answers gives the row count and becomes the sentinel: appended
    # to every request, it keeps PowerWorld from dropping trailing fields that have no
    # data, which would make the layout depend on where a request happens to end.
    for sentinel in plan.pairs():
        if (first := pull([sentinel])) is not None:
            break
    else:
        raise ResultsUnavailable(
            f"Simulator holds no results for {ctg!r}: it was not solved, or not recorded"
        )
    out = allocate(len(first), plan.width)
    out.time[:] = first[:, 0]
    column = 0
    for chunk in batched(plan.pairs(), max(1, cells // len(first) - 1)):
        # Simulator answers a repeated request once, so the sentinel goes last exactly once.
        requests = [*(c for c in chunk if c != sentinel), sentinel]
        table = pull(requests)
        if table is None or not np.array_equal(table[:, 0], out.time):
            raise EsappError(f"{ctg}: Simulator returned different time axes within one simulation")
        where = {c: j for j, c in enumerate(requests, start=1)}
        out.values[:, column : column + len(chunk)] = table[:, [where[c] for c in chunk]]
        column += len(chunk)
    return out


def _pull(
    simauto: SimAuto,
    ctg: str,
    pairs: Sequence[Pair],
    *,
    start: float | None,
    stop: float | None,
) -> npt.NDArray[np.float64] | None:
    """One request: (rows, 1 + fields) with time first, or None if Simulator has no data."""
    requests = [f"{ref.who} | {field.name}" for ref, field in pairs]
    header, rows = simauto.TSGetContingencyResults(ctg, requests, start, stop) or ((), None)
    if len(header or ()) != len(requests):  # Simulator leaves out what it cannot resolve
        raise ObjectNotFound(
            f"{ctg}: Simulator resolved {len(header or ())} of {len(requests)} requested "
            f"fields ({', '.join(requests[:3])}{', ...' if len(requests) > 3 else ''})"
        )
    if not rows or rows[0] is None:
        return None
    return np.array(rows, dtype=np.float64)  # parses the strings; the tuples die with this frame
