"""SUBDATA: the auxiliary-file reader and writer, and ``pw[T, T.List]`` over FakeSimAuto."""

import pandas as pd
import pytest

from esapp import PowerWorld
from esapp._aux import entries, read_subdata, tokens, write_subdata
from esapp.grid import Contingency, Gen
from esapp.testing import FakeSimAuto

WRITTEN = """\
DATA (Gen, [BusNum,GenID])
{
    1 "1 "
   <SUBDATA BidCurve>
     //MW   Price[$/MWhr]
         10.00     4.50
         20.00     6.25
   </SUBDATA>
    3 "1"
   <SUBDATA BidCurve>
   </SUBDATA>
}
"""


def test_tokens_keep_quoted_text_whole():
    assert tokens('"BRANCH 1 2 1 OPEN" "" CHECK 0 NO') == [
        "BRANCH 1 2 1 OPEN",
        "",
        "CHECK",
        "0",
        "NO",
    ]


def test_entries_carry_their_owner_and_place():
    found = list(entries(WRITTEN, 2, "BidCurve"))
    assert found == [(["1", "1"], 0, "10.00     4.50"), (["1", "1"], 1, "20.00     6.25")]


def test_read_names_and_types_the_columns():
    frame = read_subdata(WRITTEN, Gen, Gen.BidCurve)
    assert list(frame.columns) == ["BusNum", "GenID", "point", "MW", "Price"]
    assert frame["BusNum"].tolist() == [1, 1]
    assert frame["GenID"].tolist() == ["1", "1"]  # Simulator pads IDs; keys come back stripped
    assert frame["Price"].tolist() == [4.5, 6.25]


def test_read_names_columns_past_the_documented_ones_by_position():
    text = 'DATA (Contingency, [CTGLabel])\n{\n"A"\n<SUBDATA LimitViol>\n'
    text += " ".join(f'"{n}"' for n in range(10)) + "\n</SUBDATA>\n}\n"
    frame = read_subdata(text, Contingency, Contingency.LimitViol)
    assert list(frame.columns)[-2:] == ["CTGSpecifiedLimit", "10"]


def test_write_replaces_each_list_and_clears_the_owners_left_out():
    rows = pd.DataFrame({"BusNum": [1, 1], "GenID": ["1", "1"], "MW": [5.0, 1.0], "point": [1, 0]})
    text = write_subdata(Gen, Gen.BidCurve, rows.assign(Price=[2.0, 3.0]), owners=[(3, "1")])
    assert text.splitlines() == [
        "DATA (Gen, [BusNum,GenID])",
        "{",
        '1 "1"',
        "<SUBDATA BidCurve>",
        "1.0 3.0",  # in point order
        "5.0 2.0",
        "</SUBDATA>",
        '3 "1"',
        "<SUBDATA BidCurve>",
        "</SUBDATA>",
        "}",
    ]


@pytest.mark.parametrize(
    ("frame", "message"),
    [
        (pd.DataFrame({"MW": [1.0]}), "keys"),
        (pd.DataFrame({"BusNum": [1], "GenID": ["1"], "Cost": [1.0]}), "no columns"),
    ],
)
def test_write_rejects_frames_it_cannot_place(frame: pd.DataFrame, message: str):
    with pytest.raises(ValueError, match=message):
        write_subdata(Gen, Gen.BidCurve, frame)


@pytest.fixture
def pw(fake_pw: PowerWorld, fake_simauto: FakeSimAuto) -> PowerWorld:
    fake_simauto.add(Gen, {"BusNum": [1, 3], "GenID": ["1", "1"]})
    fake_simauto.add_subdata(Gen, "BidCurve", {(1, "1"): [[10.0, 4.5], [20.0, 6.25]]})
    return fake_pw


def test_index_reads_every_object_or_one(pw: PowerWorld):
    every = pw[Gen, Gen.BidCurve]
    assert every[["BusNum", "point", "MW"]].values.tolist() == [[1, 0, 10.0], [1, 1, 20.0]]
    assert pw[Gen(3, "1"), Gen.BidCurve].empty
    assert pw[Gen, Gen.BidCurve, Gen.BusNum >= 2].empty


def test_index_writes_lists(pw: PowerWorld, fake_simauto: FakeSimAuto):
    pw[Gen(3, "1"), Gen.BidCurve] = pd.DataFrame({"MW": [1.0, 2.0], "Price": [3.0, 4.0]})
    assert fake_simauto.subdata["Gen", "BidCurve"]["3", "1"] == [[1.0, 3.0], [2.0, 4.0]]
    pw[Gen(1, "1"), Gen.BidCurve] = []
    assert fake_simauto.subdata["Gen", "BidCurve"]["1", "1"] == []
    assert pw[Gen, Gen.BidCurve]["BusNum"].unique().tolist() == [3]


def test_index_rejects_another_types_list(pw: PowerWorld):
    with pytest.raises(ValueError, match="not a SUBDATA of Gen"):
        pw[Gen, Contingency.LimitViol]
