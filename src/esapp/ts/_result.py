"""One simulation's recorded fields, indexed the way ``pw[...]`` is."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, overload

import numpy as np
import numpy.typing as npt
import pandas as pd

from esapp._indexing import FieldSpec, select_fields
from esapp.schema import Field, GObject
from esapp.simauto._script import is_yes
from esapp.ts._plan import Block, Plan, Watch
from esapp.ts._transfer import Values


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Result:
    """One simulation's results: ``time`` (T,) and ``values`` (T, C), indexed like ``pw[...]``::

        sim[TS.Gen.W]                    # DataFrame: rows are times, columns are generators
        sim[Gen(2, "1"), TS.Gen.W]       # Series over time
        sim[Gen(2, "1")]                 # that generator's fields
        sim[Gen, [TS.Gen.W, TS.Gen.P]]   # columns (field, keys)

    These DataFrames and Series share memory with the simulation instead of copying it. As long
    as one exists, the whole simulation stays in memory: use :meth:`select` to keep only what
    you need.
    The time of an event appears twice in ``time``: just before the event, then just after.
    """

    name: str
    time: npt.NDArray[np.float64]
    values: Values
    plan: Plan
    summary: pd.Series = field(default_factory=lambda: pd.Series(dtype=object))
    events: pd.DataFrame = field(default_factory=pd.DataFrame)
    stats: Mapping[Field[Any], pd.Series] = field(default_factory=dict[Field[Any], pd.Series])

    @property
    def solved(self) -> bool:
        """Whether PowerWorld ran the contingency to the end (CTGSolved)."""
        return is_yes(self.summary.get("CTGSolved", ""))

    @property
    def nbytes(self) -> int:
        return self.time.nbytes + self.values.nbytes

    def __repr__(self) -> str:
        rows, cols = self.values.shape
        state = "" if self.solved else ", not solved"
        return (
            f"<Result {self.name!r}: {rows} steps x {cols} columns, "
            f"{self.nbytes / 2**20:.1f} MiB{state}>"
        )

    # ------------------------------------------------------------------ indexing
    @overload
    def __getitem__(self, key: Field[Any], /) -> pd.DataFrame: ...
    @overload
    def __getitem__(self, key: tuple[GObject, Field[Any]], /) -> pd.Series: ...
    @overload
    def __getitem__(self, key: GObject | tuple[type[GObject], FieldSpec], /) -> pd.DataFrame: ...
    def __getitem__(self, key: object, /) -> pd.DataFrame | pd.Series:
        match key:
            case Field():
                span, block = self.plan.span(key)  # a keyless type's field: one column
                return self._frame(self.values[:, span], _columns(block))
            case (GObject() as ref, Field() as f):
                column = self.values[:, self.plan.column(ref, f)]
                return pd.Series(column, index=self._index, name=f.name, copy=False)
            case GObject() as ref:
                hits = self.plan.of(ref)
                if not hits:
                    raise KeyError(f"nothing was recorded for {ref!r}")
                columns = pd.Index([f.name for _, f in hits], name="field")
                return self._frame(self.values[:, [j for j, _ in hits]], columns)
            case (type() as gtype, spec) if issubclass(gtype, GObject):
                parts = {f.name: self[f] for f in select_fields(gtype, spec)}
                return pd.concat(parts, axis=1, names=["field"])
            case _:
                raise TypeError(f"unsupported index {key!r}; see help(esapp.ts.Result)")

    @property
    def df(self) -> pd.DataFrame:
        """Everything recorded: columns (type, field, key). Shares memory with the simulation."""
        return self._frame(self.values, self.plan.columns)

    # ------------------------------------------------------------------ slicing
    def window(self, start: float, stop: float) -> Result:
        """The time points from ``start`` to ``stop``, sharing memory with this result."""
        i = int(np.searchsorted(self.time, start, "left"))
        j = int(np.searchsorted(self.time, stop, "right"))
        return replace(self, time=self.time[i:j], values=self.values[i:j])

    def at(self, t: float) -> pd.Series:
        """Everything recorded, at ``t``. At the time of an event, the values just after it."""
        i = int(np.searchsorted(self.time, t, "right")) - 1
        if i < 0:
            raise KeyError(f"{t} is before the first time point, {self.time[0]}")
        return pd.Series(self.values[i], index=self.plan.columns, name=self.time[i])

    def select(self, *watch: Watch) -> Result:
        """A copy with only these fields. Delete the original to free the rest."""
        plan, columns = self.plan.subset(watch)
        values = np.asfortranarray(self.values[:, columns])
        return replace(self, time=self.time.copy(), values=values, plan=plan)

    # ------------------------------------------------------------------ helpers
    @property
    def _index(self) -> pd.Index:
        return pd.Index(self.time, name="time", copy=False)

    def _frame(self, values: Values, columns: pd.Index) -> pd.DataFrame:
        return pd.DataFrame(values, index=self._index, columns=columns, copy=False)


def _columns(block: Block) -> pd.Index:
    """The objects' keys, named as ``pw[Gen.GenMW]`` names its index."""
    names = block.field.owner.schema.key_names
    keys = [ref.key for ref in block.refs]
    match names:
        case []:
            return pd.Index([block.field.name])
        case [name]:
            return pd.Index([k[0] for k in keys], name=name)
        case _:
            return pd.MultiIndex.from_tuples(keys, names=names)
