"""The analysis namespaces (pw.pf, pw.ctg, ...) against a live SimAuto session."""

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from esapp import NotConverged, PowerWorld
from esapp.apps.ctg import STATUS
from esapp.ctg import Contingency, trip
from esapp.grid import (
    Branch,
    Bus,
    ContingencyElement,
    Gen,
    Load,
    Sim_Solution_Options,
    Substation,
)
from esapp.testing import powerworld


def a_line(pw: PowerWorld) -> Branch:
    """The case's first in-service transmission line (not a transformer)."""
    df = pw[Branch, [Branch.BranchDeviceType, Branch.LineStatus]]
    lines = df[(df["BranchDeviceType"] == "Line") & (df["LineStatus"] == "Closed")]
    return Branch(*lines.iloc[0][Branch.schema.key_names].tolist())


@powerworld(isolation="state")
class TestPowerFlow:
    def test_solve(self, pw: PowerWorld):
        pw.pf.solve()
        pw.pf.solve("POLARNEWTON", flat=True)
        assert pw[Bus.BusPUVolt].between(0.8, 1.2).all()

    def test_not_converged(self, pw: PowerWorld):
        with pw.options({Sim_Solution_Options.MaxItr: 1}), pytest.raises(NotConverged):
            pw.pf.solve(flat=True)
        pw.pf.solve()  # and it solves again once allowed to


def the_slack(pw: PowerWorld) -> int:
    kinds = pw.pf.categories()["kind"]
    return int(kinds.index[kinds == "slack"][0])


@powerworld(isolation="state")
class TestMatrices:
    def test_ybus_carries_the_injections(self, pw: PowerWorld):
        pw.pf.solve()
        y, v = pw.pf.ybus(), pw.pf.voltages()
        assert y.rows.equals(v.index)
        s = v.to_numpy() * np.conj(y.values @ v.to_numpy()) * 100  # Simulator's 100 MVA base
        net = pw[Bus, [Bus.BusNetMW, Bus.BusNetMVR, Bus.BusSSMW, Bus.BusSS]].set_index("BusNum")
        # Ybus holds the switched shunts, which the bus's net injection counts as injections.
        assert s.real == pytest.approx((net["BusNetMW"] - net["BusSSMW"]).to_numpy(), abs=0.05)
        assert s.imag == pytest.approx((net["BusNetMVR"] - net["BusSS"]).to_numpy(), abs=0.05)

    def test_jacobian(self, pw: PowerWorld):
        pw.pf.solve()
        n, slack = len(pw[Bus]), the_slack(pw)
        jac = pw.pf.jacobian()
        assert jac.shape == (2 * n, 2 * n)
        held = jac.to_frame().loc[("angle", slack)]
        assert held[held != 0].to_dict() == {("angle", slack): 1.0}  # the slack's angle is held
        assert pw.pf.jacobian("rectangular").shape == (2 * n, 2 * n)
        assert pw.pf.jacobian("dc").shape == (n, n)

    def test_categories_and_mismatches(self, pw: PowerWorld):
        pw.pf.solve()
        kinds = pw.pf.categories()["kind"]
        assert set(kinds) <= {"slack", "pv", "pq"}
        assert (kinds == "slack").sum() == 1
        assert pw.pf.mismatches().abs().max().max() < 0.1  # MW, Mvar

    def test_gic_gmatrix(self, pw: PowerWorld):
        g = pw.gic.gmatrix()
        kinds = g.rows.get_level_values("ObjectType")
        assert (kinds == "Substation").sum() == len(pw[Substation])
        assert (kinds == "Bus").sum() == len(pw[Bus])
        dense = g.to_frame().to_numpy()
        assert np.allclose(dense, dense.T)


@powerworld(isolation="fresh")
def test_contingency_definitions(pw: PowerWorld):
    line = a_line(pw)
    other = pw.refs(Branch)[-1]
    n2 = Contingency("esapp N-2", line, trip(other, delay=0.5))
    n1 = Contingency("esapp N-1", other)
    pw.ctg.define(n2, n1)
    assert {n2, n1} <= set(pw.ctg.contingencies())
    pw.ctg.define(Contingency("esapp N-2", line))  # replaces it
    (again,) = [c for c in pw.ctg.contingencies() if c.name == "esapp N-2"]
    assert again.actions == Contingency("esapp N-2", line).actions
    assert pw.ctg.solve("esapp N-2").solved["esapp N-2"]
    assert pw[Branch.LineStatus].loc[line.key] == "Closed"  # back at the reference state
    with pytest.raises(ValueError, match="no object"):
        pw.ctg.define(Contingency("esapp bad", Branch(99999, 99998, "9")))
    assert "esapp bad" not in {c.name for c in pw.ctg.contingencies()}

    before = len(pw[ContingencyElement])
    value = pw.ctg.solve(Contingency("esapp value", line))  # run without saving it
    assert value.solved.to_dict() == {"esapp value": True}
    assert len(pw[ContingencyElement]) == before  # and gone from the case


@powerworld(isolation="fresh")
def test_groups_and_pv(pw: PowerWorld):
    pw.pf.solve()
    source = pw.groups.create("esapp src", Gen, Gen.BusNum.ne(the_slack(pw)), share="MAX GEN INC")
    sink = pw.groups.create("esapp sink", Load, share="LOAD MW")
    points = pw.groups.points(sink)
    assert len(points) == len(pw[Load])
    assert (points["PPntPFInit"] == "LOAD MW").all()
    tracked = pw[Bus.PVmonBusVoltage]
    pv = pw.pv.solve(source, sink, buses=Bus.BusNum.eq(pw.refs(Bus)[0].key[0]))
    assert pv.summary.loc["Base Case", "PVResultMaxShift"] > 0
    curve = pv.curve()
    assert curve.shape[1] == 1  # the one bus tracked
    assert curve.index[0] == 0.0
    assert pw[Bus.PVmonBusVoltage].equals(tracked)  # put back
    pw.pv.clear()
    pw.groups.delete(sink)
    assert len(pw.groups.points(sink)) == 0


@powerworld(isolation="state")
class TestVoltageAndShiftSensitivities:
    def test_voltage(self, pw: PowerWorld):
        pw.pf.solve()
        bus = pw.refs(Bus)[0]
        sens = pw.sens.voltage(bus)
        assert sens["BusgenericSensQ"].idxmax() == bus.key[0]  # a bus moves most for its own Mvar

    def test_shift_factors(self, pw: PowerWorld):
        line = a_line(pw)
        factors = pw.sens.shift_factors(line, Bus(the_slack(pw)))
        assert factors.loc[line.key[0]] > factors.loc[line.key[1]]  # injected at the near end
        assert factors.abs().max() <= 1.0


@powerworld(isolation="fresh")
def test_contingencies(pw: PowerWorld):
    pw.ctg.auto_insert()
    results = pw.ctg.solve()
    assert len(results.summary) > 0
    assert results.summary["CTGSolved"].dtype == STATUS
    assert results.solved.all()
    assert results.unsolved == []
    assert {"CTGLabel", "LimViolID", "LimViolValue", "LimViolLimit"} <= set(results.violations)
    first = str(results.summary.index[0])
    one = pw.ctg.solve(first)
    assert one.summary.index.tolist() == [first]
    assert set(one.violations["CTGLabel"]) <= {first}
    pw.ctg.clear()
    assert not pw.ctg.results().solved.any()


@powerworld(isolation="state")
class TestSensitivities:
    def test_ptdf(self, pw: PowerWorld):
        line = a_line(pw)
        ptdf = pw.sens.ptdf(Bus(line.key[0]), Bus(line.key[1]))
        assert ptdf.name == "ptdf"
        assert 0 < ptdf.loc[line.key] <= 1  # a fraction, not PowerWorld's percent

    def test_lodf(self, pw: PowerWorld):
        line = a_line(pw)
        lodf = pw.sens.lodf(line)
        assert lodf.loc[line.key] == pytest.approx(-1.0)  # the outaged line loses its flow
        matrix = pw.sens.lodf_matrix([line])
        pd.testing.assert_series_equal(matrix[line.key], lodf, check_names=False)

    def test_an_islanding_outage_has_no_lodfs(self, pw: PowerWorld):
        lines = pw.refs(Branch, Branch.BranchDeviceType.eq("Line"))
        missing = pw.sens.lodf_matrix(lines).isna()
        assert (missing.any() == missing.all()).all()  # a whole outage, or none of it


@powerworld(isolation="state")
def test_opf_prices(pw: PowerWorld):
    pw.opf.solve()
    prices = pw.opf.lmp()
    parts = prices["BusMWMCEnergy"] + prices["BusMWMCCongestion"] + prices["BusMWMCLosses"]
    assert prices["BusMCMW"].to_numpy() == pytest.approx(parts.to_numpy(), abs=1e-3)


@powerworld(isolation="state")
def test_atc(pw: PowerWorld):
    line = a_line(pw)
    transfer = pw.atc.determine(Bus(line.key[0]), Bus(line.key[1]))
    assert not transfer.limiters.empty
    assert math.isnan(transfer.limit) or transfer.limit in set(transfer.limiters["CTGViolMaxLine"])
    pw.atc.clear()


@powerworld(isolation="state")
def test_gic(pw: PowerWorld):
    transformers = pw.gic.calculate(2.0, 90.0)
    assert list(transformers.columns) == ["GICXFIEffective1", "GICQLosses"]
    assert (transformers["GICQLosses"] >= 0).all()
    turned = pw.gic.calculate(2.0, 0.0)  # the current follows the field's heading
    assert turned.index.equals(transformers.index)
    pw.gic.clear()


@powerworld(isolation="state")
class TestFaults:
    def test_bus(self, pw: PowerWorld):
        bus = pw.refs(Bus)[0]
        currents = pw.fault.calculate(bus, "SLG")
        assert currents.loc[bus.key[0], "FaultCurMag"] > 0
        pw.fault.clear()
        assert (pw[Bus.FaultCurMag] == 0).all()

    def test_along_a_line(self, pw: PowerWorld):
        currents = pw.fault.calculate(a_line(pw), "3PB", at=50.0)
        assert currents["FaultCurMag"].max() > 0
        pw.fault.clear()


@powerworld(isolation="state")
def test_qv(pw: PowerWorld):
    bus = pw.refs(Bus)[-1]
    qv = pw.qv.solve(bus)
    assert set(pw[Bus.QVSelected].str.strip()) <= {"NO", "YES"}  # put back
    assert {"qv_Q0", "qv_Qmin"} <= set(qv.summary.columns)
    assert bus.key[0] in qv.summary.index.get_level_values("BusNum")
    curve = qv.curve(bus.key[0])
    assert curve.min() == pytest.approx(qv.summary["qv_Qmin"].iloc[0], rel=0.02)  # sampled nose
    pw.qv.clear()


@powerworld(isolation="state")
def test_files(pw: PowerWorld, tmp_path: Path):
    saved = pw.files.save(tmp_path / "copy.pwb")
    assert saved.stat().st_size > 0
    aux = pw.files.write(tmp_path / "buses.aux", Bus, [Bus.BusName], Bus.BusNum <= 5)
    assert "DATA (Bus, [BusNum,BusName])" in aux.read_text()  # keys first
    csv = pw.files.write(tmp_path / "buses.csv", Bus, [Bus.BusPUVolt])
    assert len(csv.read_text().splitlines()) >= len(pw[Bus])
    pw[Bus, Bus.BusName, Bus.BusNum <= 5] = "renamed"
    pw.files.load_aux(aux)  # the file puts the names back
    assert "renamed" not in set(pw[Bus.BusName])
