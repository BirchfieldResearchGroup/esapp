"""SimAuto columns to typed DataFrames."""

import pytest

from esapp._frames import frame
from esapp.schema import Field, GObject, Role


class Thing(GObject, pw="Thing", keys=("N", "S")):
    N = Field("N", int, Role.KEY)
    X = Field("X", float)
    S = Field("S", str, Role.KEY)
    Y = Field("Y", float, Role.NONSCALAR)


COLUMNS = [Thing.N, Thing.X, Thing.S]


@pytest.mark.parametrize("cols", [None, (), [[], [], []]])
def test_no_rows_still_has_typed_columns(cols: object):
    df = frame(cols, COLUMNS)  # pyright: ignore[reportArgumentType]
    assert list(df.columns) == ["N", "X", "S"]
    assert df.empty
    assert (df["N"].dtype, df["X"].dtype, df["S"].dtype) == ("int64", "float64", object)


def test_numbers_take_their_field_dtype():
    df = frame([(1, 2), (2, 3), ("a", "b")], COLUMNS)
    assert (df["N"].dtype, df["X"].dtype) == ("int64", "float64")
    assert df["X"].tolist() == [2.0, 3.0]


def test_text_keys_lose_simulators_padding():
    assert frame([(1,), (1.5,), ("H ",)], COLUMNS)["S"].tolist() == ["H"]


def test_a_nonscalar_field_stays_as_sent():
    assert frame([("1-3", "n/a")], [Thing.Y])["Y"].tolist() == ["1-3", "n/a"]


def test_a_numeric_column_sent_untyped_stays_as_sent():
    assert frame([(1, 2), ("n/a", 1.5), ("a", "b")], COLUMNS)["X"].tolist() == ["n/a", 1.5]


def test_a_column_per_field():
    with pytest.raises(ValueError, match="zip"):
        frame([(1, 2), (1.0, 2.0)], COLUMNS)
