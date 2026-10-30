"""Matrices from Simulator's MATLAB text, and the power flow's bus categories."""

from pathlib import Path

import numpy as np
import pytest

from esapp import PowerWorld
from esapp.grid import Bus
from esapp.matrix import gmatrix_axes, jacobian_axes, read_matlab
from esapp.testing import FakeSimAuto

YBUS = """\
j = sqrt(-1);
Ybus = sparse(3,3);
Ybus(1,1)= 5.0000+ j*(-15.0000);
Ybus(1,2)=-5.0000+ j*(15.0000);
Ybus(2,1)=-5.0000+ j*(15.0000);
Ybus(2,2)= 5.0000+ j*(-14.5000);
Ybus(3,3)= 0.0000+ j*(0.2500);
"""

# Number, Name, Type, V, Angle, P-Row, Q/V-Row: every bus has both rows.
JACOBIAN_IDS = """\
  Number   Name   Type        V      Angle  P-Row  Q/V-Row
       1   One    Slack  1.0000     0.0000      1        4
       2   Two    PV     1.0200    -2.5000      2        5
       3   Three  PQ     0.9800    -4.0000      3        6
"""

GMATRIX_IDS = """\
ObjectType, Number, Row/Col, Name
Substation, 7, 1, Sub Seven
Bus, 1, 2, One
Bus, 2, 3, Two
"""


def test_read_matlab_complex_and_real(tmp_path: Path):
    path = tmp_path / "y.m"
    path.write_text(YBUS)
    y = read_matlab(path, "Ybus", (3, 3)).toarray()
    assert y[0, 1] == -5 + 15j
    assert y[2, 2] == 0.25j
    path.write_text("Jac(1,1)=1.5e+01;\nJac(2,1)= -3;\n")
    assert read_matlab(path, "Jac", (2, 2)).toarray().tolist() == [[15.0, 0.0], [-3.0, 0.0]]
    path.write_text("GMatrix = sparse(0,0);\n")
    assert read_matlab(path, "GMatrix", (2, 2)).nnz == 0


def test_jacobian_axes_name_each_row_and_column():
    rows, columns = jacobian_axes(JACOBIAN_IDS, "polar")
    assert rows.tolist() == [("angle", 1), ("P", 2), ("P", 3), ("V", 1), ("V", 2), ("Q", 3)]
    assert columns.tolist() == [("angle", b) for b in (1, 2, 3)] + [("V", b) for b in (1, 2, 3)]
    assert rows.names == ["equation", "BusNum"]
    _, rect = jacobian_axes(JACOBIAN_IDS, "rectangular")
    assert rect.get_level_values("variable").unique().tolist() == ["e", "f"]
    dc_rows, dc_columns = jacobian_axes(JACOBIAN_IDS, "dc")
    assert dc_rows.tolist() == [("angle", 1), ("P", 2), ("P", 3)]
    assert dc_columns.size == 3


def test_gmatrix_axes_put_substations_first():
    axis = gmatrix_axes(GMATRIX_IDS)
    assert axis.tolist() == [("Substation", 7), ("Bus", 1), ("Bus", 2)]


@pytest.fixture
def pw(fake_pw: PowerWorld, fake_simauto: FakeSimAuto) -> PowerWorld:
    fake_simauto.add(
        Bus,
        {
            "BusNum": [1, 2, 3],
            "BusPUVolt": [1.0, 1.02, 0.98],
            "BusAngle": [0.0, -2.5, -4.0],
            "BusCat": ["Slack", "PV", "PQ (Gens at Var Limit)"],
        },
    )
    return fake_pw


def test_ybus_and_voltages(pw: PowerWorld, fake_simauto: FakeSimAuto):
    (pw.scratch / "ybus.m").write_text(YBUS)  # what SaveYbusInMatlabFormat writes
    y = pw.pf.ybus()
    assert fake_simauto.scripts[-1] == f'SaveYbusInMatlabFormat("{pw.scratch / "ybus.m"}", NO);'
    assert y.shape == (3, 3)
    assert y.to_frame().loc[1, 2] == -5 + 15j
    v = pw.pf.voltages()
    assert v.loc[2] == pytest.approx(1.02 * np.exp(-2.5j * np.pi / 180))
    assert "3 x 3, 5 nonzero" in repr(y)


def test_jacobian(pw: PowerWorld, fake_simauto: FakeSimAuto):
    (pw.scratch / "jacobian.m").write_text("Jac(1,1)=1;\nJac(6,3)=-2.5;\n")
    (pw.scratch / "jacobian.txt").write_text(JACOBIAN_IDS)
    jac = pw.pf.jacobian()
    assert fake_simauto.scripts[-1].endswith(", EXPM, P);")
    assert jac.to_frame().loc[("Q", 3), ("angle", 3)] == -2.5
    (pw.scratch / "jacobian.m").write_text("Jac(3,3)=7;\n")
    assert pw.pf.jacobian("dc").to_frame().loc[("P", 3), ("angle", 3)] == 7
    assert fake_simauto.scripts[-1].endswith(", EXPM, DC);")


def test_gmatrix(pw: PowerWorld):
    (pw.scratch / "gmatrix.m").write_text("GMatrix(1,1)=2;\nGMatrix(1,2)=-2;\n")
    (pw.scratch / "gmatrix.txt").write_text(GMATRIX_IDS)
    g = pw.gic.gmatrix()
    assert g.to_frame().loc[("Substation", 7), ("Bus", 1)] == -2


def test_categories(pw: PowerWorld):
    cats = pw.pf.categories()
    assert cats["kind"].tolist() == ["slack", "pv", "pq"]
    assert cats["at_limit"].tolist() == [False, False, True]
    assert cats.loc[3, "BusCat"] == "PQ (Gens at Var Limit)"


@pytest.mark.parametrize(  # every label Texas7k and WECC240 use, as Simulator 24 writes them
    ("label", "kind", "at_limit", "role"),
    [
        ("PQ", "pq", False, ""),
        ("PV", "pv", False, ""),
        ("Slack", "slack", False, ""),
        ("PQ (Gens at Var Limit)", "pq", True, ""),
        ("PQ (Remotely Regulated)", "pq", False, "target"),
        ("PQ (Remotely Regulated at Var Limit)", "pq", True, "target"),
        ("PV (Remote Reg Primary)", "pv", False, "primary"),
        ("PQ (Remote Reg Secondary)", "pq", False, "secondary"),
    ],
)
def test_category_labels(
    fake_pw: PowerWorld, fake_simauto: FakeSimAuto, label: str, kind: str, at_limit: bool, role: str
):
    fake_simauto.add(Bus, {"BusNum": [1], "BusCat": [label]})
    row = fake_pw.pf.categories().loc[1]
    assert (row["kind"], row["at_limit"], row["role"]) == (kind, at_limit, role)
    assert row["remote"] == bool(role)
