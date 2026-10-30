"""SimAuto rows and columns to DataFrames, typed by the schema."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype

from esapp.schema import Field, Role

_EMPTY_DTYPES: dict[type, str] = {int: "int64", float: "float64"}
_NUMPY: dict[type, type[np.generic]] = {int: np.int64, float: np.float64}


def frame(cols: Sequence[Sequence[Any]] | None, columns: Sequence[Field[Any]]) -> pd.DataFrame:
    """A DataFrame from columns as GetParamsTypedCols sends them, each in its field's type.

    No rows gives an empty frame that still has the columns and their dtypes.
    """
    if not cols or not len(cols[0]):
        return empty(columns)
    data = {
        c.name: _exact(values, c)
        if c.dtype in _NUMPY and not c.role & Role.NONSCALAR
        else _key(values)
        if c.role & Role.KEY and c.dtype is str
        else _column(values, c.dtype)
        for c, values in zip(columns, cols, strict=True)
    }
    return pd.DataFrame(data, columns=[c.name for c in columns])


def empty(columns: Sequence[Field[Any]]) -> pd.DataFrame:
    names = [c.name for c in columns]
    series = {c.name: pd.Series(dtype=_EMPTY_DTYPES.get(c.dtype, object)) for c in columns}
    return pd.DataFrame(series, columns=names)


def _exact(values: Sequence[Any], field: Field[Any]) -> pd.Series | np.ndarray:
    try:
        return np.asarray(values, dtype=_NUMPY[field.dtype])
    except (TypeError, ValueError):  # sent untyped after all (a non-scalar field)
        return _column(values, field.dtype)


def _key(values: Sequence[Any]) -> pd.Series:
    """Text keys as they are written: Simulator pads IDs to their width ("1 ")."""
    return pd.Series([v.strip() if isinstance(v, str) else v for v in values], dtype=object)


def _column(values: Sequence[Any], dtype: type) -> pd.Series:
    """``values`` as ``dtype`` when the conversion is lossless, otherwise as sent."""
    column = pd.Series(values)
    if dtype not in (int, float):
        return column
    if not is_numeric_dtype(column.dtype):
        try:
            column = pd.to_numeric(column)
        except (TypeError, ValueError):
            return column
    kind = column.dtype.kind
    if dtype is float and kind in "iuf":
        return column.astype("float64")
    if dtype is int and kind in "iu":
        return column.astype("int64")
    if dtype is int and kind == "f" and column.notna().all() and (column % 1 == 0).all():
        return column.astype("int64")
    return column
