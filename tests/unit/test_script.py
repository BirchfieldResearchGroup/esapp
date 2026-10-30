"""Script-command encoding."""

import math
from pathlib import Path

import numpy as np
import pytest

from esapp.grid import Bus, Gen
from esapp.simauto import Bare, Kw, arg, script


@pytest.mark.parametrize(
    ("value", "encoded"),
    [
        pytest.param("Hello world", '"Hello world"', id="str"),
        pytest.param(Kw.ALL, "ALL", id="keyword"),
        pytest.param(Bare("POLARNEWTON"), "POLARNEWTON", id="bare"),
        pytest.param(True, "YES", id="true"),
        pytest.param(False, "NO", id="false"),
        pytest.param(3, "3", id="int"),
        pytest.param(np.int64(3), "3", id="numpy-int"),
        pytest.param(1.5, "1.5", id="float"),
        pytest.param(np.float64(0.1), "0.1", id="numpy-float"),
        pytest.param(Path("a b.pwb"), '"a b.pwb"', id="path"),
        pytest.param(Bus(5), "[BUS 5]", id="object"),
        pytest.param(Gen(1, "1"), '[GEN 1 "1"]', id="object-str-key"),
        pytest.param(Gen.GenMW, "GenMW", id="field"),
        pytest.param(Gen, "Gen", id="type"),
        pytest.param([1, "a", Kw.SELECTED], '[1, "a", SELECTED]', id="list"),
        pytest.param((), "[]", id="empty"),
        pytest.param(None, "", id="none"),
    ],
)
def test_arguments(value: object, encoded: str):
    assert arg(value) == encoded


def test_statements():
    assert [
        script("SetData", Gen, [Gen.GenMW, Gen.GenStatus], [100.0, "Closed"], Kw.ALL),
        script("EnterMode", Kw.EDIT),
        script("TSRunUntilSpecifiedTime", "fault_5", [2.0, 0.25, True, True]),
        script("LogSave", Path("log.txt"), False),
        script("TSSolve", "fault_5", None, None),
        script("LogClear"),
    ] == [
        'SetData(Gen, [GenMW, GenStatus], [100.0, "Closed"], ALL);',
        "EnterMode(EDIT);",
        'TSRunUntilSpecifiedTime("fault_5", [2.0, 0.25, YES, YES]);',
        'LogSave("log.txt", NO);',
        'TSSolve("fault_5");',
        "LogClear;",
    ]


@pytest.mark.parametrize(
    ("value", "error"),
    [
        pytest.param('say "hi"', ValueError, id="double-quote"),
        pytest.param(math.inf, ValueError, id="infinite"),
        pytest.param(math.nan, ValueError, id="nan"),
        pytest.param([0.0, 10.0, None, True], ValueError, id="blank-list-item"),
        pytest.param(object(), TypeError, id="object"),
        pytest.param(int, TypeError, id="non-gobject-class"),
    ],
)
def test_unencodable(value: object, error: type[Exception]):
    with pytest.raises(error):
        arg(value)
