"""The analysis namespaces over FakeSimAuto: the statements they send, the frames they shape."""

import math
from pathlib import Path

import pandas as pd
import pytest

import esapp.ctg
from esapp import PowerWorld, PrerequisiteError
from esapp.apps.ctg import STATUS
from esapp.apps.pv import _curves  # pyright: ignore[reportPrivateUsage]
from esapp.apps.qv import QvResults
from esapp.apps.qv import _curves as _qv_curves  # pyright: ignore[reportPrivateUsage]
from esapp.ctg import Action, close, target, trip
from esapp.grid import (
    Branch,
    Bus,
    Contingency,
    ContingencyElement,
    Gen,
    InjectionGroup,
    PWPVResultListContainer,
    TransferLimiter,
    ViolationCTG,
)
from esapp.testing import FakeSimAuto


def test_power_flow_statements(fake_pw: PowerWorld, fake_simauto: FakeSimAuto):
    fake_pw.pf.solve()
    fake_pw.pf.solve("POLARNEWTON", flat=True)
    batches = [args[0] for method, args in fake_simauto.calls if method == "RunScriptCommand"]
    assert batches == ["SolvePowerFlow;", "ResetToFlatStart;SolvePowerFlow(POLARNEWTON);"]


def test_contingency_results(fake_pw: PowerWorld, fake_simauto: FakeSimAuto):
    fake_simauto.add(
        Contingency,
        {
            "CTGLabel": ["A", "B", "C", "D"],
            "CTGSolved": ["YES", "NO ", "NO", "PARTIAL"],
            "CTGSkip": ["NO", "NO", "YES", "NO"],
        },
    )
    fake_simauto.add(
        ViolationCTG,
        {"CTGLabel": ["A", "A"], "LimViolID:1": ["x", "y"], "LimViolPct": [101.0, 120.0]},
    )
    results = fake_pw.ctg.solve()
    assert fake_simauto.scripts[-1] == "CTGSolveAll(NO, YES);"
    assert results.summary["CTGSolved"].tolist() == ["YES", "NO", "NO", "PARTIAL"]
    assert results.summary["CTGSolved"].dtype == STATUS
    assert results.solved.tolist() == [True, False, False, False]
    assert results.unsolved == ["B"]  # C was skipped; D solved but for an island
    assert results.violations["LimViolPct"].tolist() == [101.0, 120.0]
    one = fake_pw.ctg.solve("A")
    assert fake_simauto.scripts[-2:] == ['CTGSolve("A");', "CTGRestoreReference;"]
    assert one.summary.index.tolist() == ["A"]


def test_contingency_values():
    n2 = esapp.ctg.Contingency("N-2", Branch(1, 5, "1"), trip(Gen(2, "1"), delay=0.5))
    assert n2.actions == (Action(Branch(1, 5, "1")), Action(Gen(2, "1"), "OPEN", 0.5))
    assert [a.object for a in n2.actions] == ["BRANCH 1 5 '1'", "GEN 2 '1'"]
    assert close(Bus(3)).verb == "CLOSE"
    assert target("BRANCH 1 5 1") == Branch(1, 5, "1")
    assert target("BRANCH 1 5") == "BRANCH 1 5"  # not a primary key: kept as text
    assert Action("INTERFACE 'Left-Right'").object == "INTERFACE 'Left-Right'"
    with pytest.raises(ValueError, match="quotes"):
        esapp.ctg.Contingency("it's")
    with pytest.raises(ValueError, match="seconds"):
        trip(Bus(1), delay=-1.0)


def test_contingency_definitions(fake_pw: PowerWorld, fake_simauto: FakeSimAuto):
    fake_simauto.add(Contingency, {"CTGLabel": ["N-2"], "CTGSkip": ["NO"]})
    fake_simauto.add(ContingencyElement, {"CTGLabel": ["N-2"], "Object": ["BUS 9"]})
    n2 = esapp.ctg.Contingency("N-2", Branch(1, 5, "1"), close(Gen(2, "1"), delay=1.0), skip=True)
    n1 = esapp.ctg.Contingency("N-1", Branch(1, 5, "1"))
    assert fake_pw.ctg.define(n2, n1) == [Contingency("N-2"), Contingency("N-1")]
    assert fake_simauto.scripts[:2] == [  # one batch deletes both
        "Delete(Contingency, \"CTGLabel = 'N-2'\");",
        "Delete(Contingency, \"CTGLabel = 'N-1'\");",
    ]
    assert fake_pw.ctg.contingencies() == [n2, n1]  # the old element went with the old N-2
    with pytest.raises(ValueError, match="repeat"):
        fake_pw.ctg.define(n1, n1)


def test_contingency_as_a_value(fake_pw: PowerWorld, fake_simauto: FakeSimAuto):
    n1 = esapp.ctg.Contingency("N-1", Branch(1, 5, "1"))
    results = fake_pw.ctg.solve(n1)
    assert 'CTGSolve("esapp~N-1");' in fake_simauto.scripts
    assert results.summary.index.tolist() == ["N-1"]  # under its own name
    assert fake_pw[Contingency].empty  # and gone from the case, its elements with it
    assert fake_pw[ContingencyElement].empty


def test_injection_groups(fake_pw: PowerWorld, fake_simauto: FakeSimAuto):
    west = fake_pw.groups.create("west", Gen, Gen.GenMW > 50, share="MAX GEN INC")
    assert west == InjectionGroup("west")
    assert fake_simauto.scripts[-2:] == [
        "Delete(InjectionGroup, \"InjGrpName = 'west'\");",
        'InjectionGroupCreate("west", Gen, MAX GEN INC, "GenMW > 50", NO);',
    ]
    fake_pw.groups.create("west", Gen, share=2.0, append=True)
    assert fake_simauto.scripts[-1] == 'InjectionGroupCreate("west", Gen, 2.0, "", YES);'


QV_FILE = """\
QV analysis launched at 10/30/2026 3:59:13 PM.
**BUS** 5 (HONOLULU138_138.0),**CASE** BASECASE
V(PU), Q_sync(MVR), Q_shunt(MVR), Q_tot(MVR), Q_res(MVR), Q_tot_res(MVR)
1.100000, 593.6468,  0.0000, 593.6468,  0.0000, 593.6468
0.988985,  0.0000,  0.0000,  0.0000,  0.0000,  0.0000
0.988985,  0.0000,  0.0000,  0.0000,  0.0000,  0.0000
**BUS** 9 (OTHER_138.0),**CASE** L_1
V(PU), Q_sync(MVR), Q_shunt(MVR), Q_tot(MVR), Q_res(MVR), Q_tot_res(MVR)
0.900000, -1.5000,  0.0000, -1.5000,  0.0000, -1.5000
"""


PV_AUX = """\
DATA (PWPVResultListContainer, [PVContingency])
{
"Base Case"
   <SUBDATA PWPVResultObject>
    INDNOM    0.000  100.000  200.000
    INDEXP    0.000  100.000  200.000
    INDIMP    0.000 -100.000 -197.296
    Bus '3'?f=BusPUVolt&v=0.98455 0.98291 0.98127
    Gen '28' '1'?f=GenMVR&v=-5.48675 -3.45208 -1.46159
   </SUBDATA>
"L_1"
   <SUBDATA PWPVResultObject>
    INDNOM    0.000  100.000
    INDEXP    0.000  100.000
    INDIMP    0.000 -100.000
    Bus '3'?f=BusPUVolt&v=0.98436 0.98270
   </SUBDATA>
}
"""


def test_pv_curves():
    curves = _curves(PV_AUX)
    assert len(curves) == 3 + 3 + 2
    first = curves.iloc[1]
    assert (first["PVContingency"], first["step"], first["shift"], first["import"]) == (
        "Base Case",
        1,
        100.0,
        -100.0,
    )
    assert curves.iloc[3][["object", "field", "value"]].tolist() == [
        "Gen '28' '1'",
        "GenMVR",
        -5.48675,
    ]


def test_pv_run(fake_pw: PowerWorld, fake_simauto: FakeSimAuto):
    fake_simauto.add(Bus, {"BusNum": [1, 2], "PVmonBusVoltage": ["NO", "YES"]})
    source, sink = InjectionGroup("src"), InjectionGroup("sink")
    with pytest.raises(PrerequisiteError, match="declined"):
        fake_pw.pv.solve(source, sink, buses=Bus.BusNum <= 1)
    assert fake_pw[Bus.PVmonBusVoltage].tolist() == ["NO", "YES"]  # put back
    assert 'PVRun([INJECTIONGROUP "src"], [INJECTIONGROUP "sink"]);' in fake_simauto.scripts
    fake_simauto.add(
        PWPVResultListContainer,
        {"PVContingency": ["Base Case"], "PVResultMaxShift": [200.0], "PVResultCritical": ["YES"]},
    )
    results = fake_pw.pv.solve(source, sink)
    assert results.summary.loc["Base Case", "PVResultCritical"]
    assert fake_simauto.scripts[-1].endswith(
        'AUX, PWPVResultListContainer, [PVContingency], [PWPVResultObject], "");'
    )


def test_voltage_sensitivity_and_shift_factors(fake_pw: PowerWorld, fake_simauto: FakeSimAuto):
    fake_simauto.add(Bus, {"BusNum": [1, 2], "BusgenericSensQ": [4e-4, 1e-4]})
    assert fake_pw.sens.voltage(Bus(1)).loc[1, "BusgenericSensQ"] == 4e-4
    assert fake_simauto.scripts[-1] == "CalculateVoltSense([BUS 1]);"
    factors = fake_pw.sens.shift_factors(Branch(1, 2, "1"), Bus(2))
    assert (
        fake_simauto.scripts[-1] == 'CalculateShiftFactors([BRANCH 1 2 "1"], BUYER, [BUS 2], DC);'
    )
    assert factors.name == "shift_factor"


def test_sensitivities_are_fractions(fake_pw: PowerWorld, fake_simauto: FakeSimAuto):
    keys = {"BusNum": [1, 1], "BusNum:1": [2, 3], "LineCircuit": ["1", "1"]}
    fake_simauto.add(Branch, keys | {"LinePTDF": [40.0, -5.0], "LineLODF": [-100.0, 50.0]})
    ptdf = fake_pw.sens.ptdf(Bus(1), Bus(2))
    assert fake_simauto.scripts[-1] == "CalculatePTDF([BUS 1], [BUS 2], DC);"
    assert ptdf.to_dict() == {(1, 2, "1"): 0.4, (1, 3, "1"): -0.05}
    matrix = fake_pw.sens.lodf_matrix([Branch(1, 2, "1")])
    assert fake_simauto.scripts[-1] == 'CalculateLODF([BRANCH 1 2 "1"], DC);'
    assert matrix[(1, 2, "1")].tolist() == [-1.0, 0.5]
    fake_simauto.tables["Branch"]["LineLODF"] = [-100.0, 1e8]  # how Simulator marks an islanding
    assert fake_pw.sens.lodf(Branch(1, 2, "1")).isna().all()  # outage: the whole of it


def test_opf(fake_pw: PowerWorld, fake_simauto: FakeSimAuto):
    fake_simauto.add(Bus, {"BusNum": [1], "BusMCMW": [21.5]})
    fake_pw.opf.solve()
    fake_pw.opf.solve(security=True)
    assert fake_simauto.scripts == ["SolvePrimalLP;", "SolveFullSCOPF;"]
    assert fake_pw.opf.lmp().loc[1, "BusMCMW"] == 21.5


def test_atc_reports_simulators_limit(fake_pw: PowerWorld, fake_simauto: FakeSimAuto):
    fake_simauto.add(
        TransferLimiter,
        {
            "ATCLineZoneChanges": [0, 0],
            "ATCGenChanges": [0, 0],
            "ATCInterfaceChanges": [0, 0],
            "DirName": ["d", "d"],
            "CTGViolMaxLine": [500.0, 350.0],
            "ReportLimit": ["NO", "YES"],
        },
    )
    transfer = fake_pw.atc.determine(Bus(1), Bus(2))
    assert fake_simauto.scripts[-1] == "ATCDetermine([BUS 1], [BUS 2], NO);"
    assert transfer.limit == 350.0
    assert transfer.limiters["CTGViolMaxLine"].tolist() == [350.0, 500.0]
    fake_simauto.tables["TransferLimiter"]["ReportLimit"] = "NO"
    assert math.isnan(fake_pw.atc.determine(Bus(1), Bus(2)).limit)


def test_faults(fake_pw: PowerWorld, fake_simauto: FakeSimAuto):
    fake_simauto.add(Bus, {"BusNum": [1, 2], "FaultCurMag": [0.0, 9.5]})
    currents = fake_pw.fault.calculate(Bus(2), "SLG", r=0.01)
    assert fake_simauto.scripts[-1] == "Fault([BUS 2], SLG, 0.01, 0.0);"
    assert currents.loc[2, "FaultCurMag"] == 9.5
    fake_pw.fault.calculate(Branch(1, 2, "1"), at=25)
    assert fake_simauto.scripts[-1] == 'Fault([BRANCH 1 2 "1"], 25.0, 3PB, 0.0, 0.0);'
    with pytest.raises(ValueError, match="location"):
        fake_pw.fault.calculate(Branch(1, 2, "1"))
    with pytest.raises(ValueError, match="location"):
        fake_pw.fault.calculate(Bus(1), at=10.0)


def test_qv_puts_the_selection_back(fake_pw: PowerWorld, fake_simauto: FakeSimAuto):
    fake_simauto.add(Bus, {"BusNum": [1, 2, 3], "QVSelected": ["YES", "NO", "NO"]})
    assert fake_pw.qv.solve(Bus.BusNum >= 2).curves.empty  # the fake writes no curve file
    assert 'SetData(Bus, [QVSelected], ["YES"], "BusNum >= 2");' in fake_simauto.scripts
    assert any(s.startswith("QVRun(") for s in fake_simauto.scripts)
    assert fake_pw[Bus.QVSelected].tolist() == ["YES", "NO", "NO"]


def test_qv_curves():
    curves = _qv_curves(QV_FILE)
    assert curves["BusNum"].tolist() == [5, 5, 9]
    assert curves["Q"].tolist() == [593.6468, 0.0, -1.5]  # the repeated point is dropped
    results = QvResults(pd.DataFrame(), curves)
    assert results.curve(5).to_dict() == {1.1: 593.6468, 0.988985: 0.0}
    assert results.curve(9, "L_1").to_dict() == {0.9: -1.5}


def test_files(fake_pw: PowerWorld, fake_simauto: FakeSimAuto, tmp_path: Path):
    fake_pw.files.write(tmp_path / "b.aux", Bus, [Bus.BusName], Bus.BusNum <= 5)
    assert fake_simauto.scripts[-1] == (
        f'SaveData("{tmp_path / "b.aux"}", AUX, Bus, [BusNum, BusName], [], "BusNum <= 5", '
        "[], NO, NO);"
    )
    fake_pw.files.write(tmp_path / "b.csv", Bus)
    assert fake_simauto.scripts[-1].startswith(
        f'SaveData("{tmp_path / "b.csv"}", CSV, Bus, [ALL], [], ""'
    )
    with fake_pw.read_only():
        fake_pw.files.save(tmp_path / "c.pwb")  # saving leaves the case as it was
    assert fake_simauto.scripts[-1] == f'SaveCase("{tmp_path / "c.pwb"}", PWB);'
