"""Grid construction

Create the WSCC 9-bus system one component at a time, add machine, exciter and governor models,
and simulate a fault.
"""

import matplotlib.pyplot as plt
import pandas as pd

from esapp import PowerWorld
from esapp.commands import NewCase
from esapp.grid import Branch, Bus, Gen, Load
from esapp.grid import Exciter_IEEET1 as Exciter
from esapp.grid import Governor_TGOV1 as Governor
from esapp.grid import MachineModel_GENROU as Machine
from esapp.ts import TS, Contingency, clear_fault, fault, trip

BUSES = pd.DataFrame(
    {
        Bus.BusNum: range(1, 10),
        Bus.BusName: [f"Bus {n}" for n in range(1, 10)],
        Bus.BusNomVolt: [16.5, 18, 13.8, 230, 230, 230, 230, 230, 230],
        Bus.BusSlack: [True, *[False] * 8],
        Bus.AreaNum: 1,
        Bus.ZoneNum: 1,
    }
)
GENERATORS = pd.DataFrame(
    {
        Gen.BusNum: [1, 2, 3],
        Gen.GenID: "1",
        Gen.GenStatus: "Closed",
        Gen.GenMVABase: [247.5, 192, 128],
        Gen.GenMWSetPoint: [71.6, 163, 85],
        Gen.GenMWMax: [247.5, 192, 128],
        Gen.GenMWMin: 0.0,
        Gen.GenVoltSet: [1.04, 1.025, 1.025],
        Gen.GenAVRAble: "YES",
        Gen.GenAGCAble: "NO",
        Gen.GenMvrSetPoint: 0.0,
        Gen.GenMVRMax: 999.0,
        Gen.GenMVRMin: -999.0,
    }
)
LOADS = pd.DataFrame(
    {
        Load.BusNum: [5, 6, 8],
        Load.LoadID: "1",
        Load.LoadStatus: "Closed",
        Load.LoadSMW: [125, 90, 100],
        Load.LoadSMVR: [50, 30, 35],
    }
)
# From bus, to bus, then R, X and B in per unit on 100 MVA. The first three are transformers.
BRANCHES = pd.DataFrame(
    [
        (1, 4, 0, 0.0576, 0),
        (2, 7, 0, 0.0625, 0),
        (3, 9, 0, 0.0586, 0),
        (4, 5, 0.01, 0.085, 0.176),
        (4, 6, 0.017, 0.092, 0.158),
        (5, 7, 0.032, 0.161, 0.306),
        (6, 9, 0.039, 0.17, 0.358),
        (7, 8, 0.0085, 0.072, 0.149),
        (8, 9, 0.0119, 0.1008, 0.209),
    ],
    columns=[Branch.BusNum, Branch.BusNum[1], Branch.LineR, Branch.LineX, Branch.LineC],
).assign(LineCircuit="1", LineAMVA=250.0, **{"LineAMVA:1": 250.0, "LineAMVA:2": 250.0})

# Dynamic models belong to a generator and use its MVA base.
UNITS = {"BusNum": [1, 2, 3], "GenID": "1"}
MACHINES = pd.DataFrame(
    {
        **UNITS,
        Machine.TSH: [9.55, 3.33, 2.35],
        Machine.TSD: 0.0,
        Machine.TSRa: 0.0,
        Machine.TSXd: [0.361, 1.72, 1.68],
        Machine.TSXd[1]: [0.15, 0.23, 0.232],
        Machine.TSXd[2]: [0.1, 0.18, 0.18],
        Machine.TSXq: [0.24, 1.66, 1.61],
        Machine.TSXq[1]: [0.24, 0.378, 0.32],
        Machine.TSXl: [0.083, 0.1, 0.095],
        Machine.TSTdo: [8.96, 6.0, 5.89],
        Machine.TSTdo[1]: 0.03,
        Machine.TSTqo: [0.31, 0.535, 0.6],
        Machine.TSTqo[1]: 0.033,
        Machine.TSS1: 0.0,
        Machine.TSS1[1]: 0.0,
    }
)
EXCITERS = pd.DataFrame(
    {
        **UNITS,
        Exciter.TSKa: 20.0,
        Exciter.TSTa: 0.2,
        Exciter.TSKe: 1.0,
        Exciter.TSTe: 0.314,
        Exciter.TSKf: 0.063,
        Exciter.TSTf: 0.35,
        Exciter.TSTr: 0.0,
        Exciter.TSVrMax: 5.0,
        Exciter.TSVrMin: -5.0,
    }
)
GOVERNORS = pd.DataFrame(
    {
        **UNITS,
        Governor.TSR: 0.05,
        Governor.TST[1]: 0.5,
        Governor.TST[2]: 3.0,
        Governor.TST[3]: 10.0,
        Governor.TSVmax: 1.0,
        Governor.TSVmin: 0.0,
        Governor.TSDt: 0.0,
    }
)

with PowerWorld() as pw:
    pw.run(NewCase())
    with pw.edit_mode():  # the case isn't valid until it has all of these
        pw.create(Bus, BUSES)
        pw.create(Gen, GENERATORS)
        pw.create(Load, LOADS)
        pw.create(Branch, BRANCHES)

    pw.pf.solve()
    print(pw[Bus, [Bus.BusPUVolt, Bus.BusAngle]].round(3).to_string(index=False))
    print(pw[Gen, [Gen.GenMW, Gen.GenMVR]].round(1).to_string(index=False))

    pw.create(Machine, MACHINES)
    pw.create(Exciter, EXCITERS)
    pw.create(Governor, GOVERNORS)
    pw.files.save("wscc9.pwb")  # open it later with PowerWorld("wscc9.pwb")

    ctg = Contingency(
        "fault at bus 7, line 5-7 opens",
        fault(Bus(7), at=1.0),
        clear_fault(Bus(7), at=1.083),  # five cycles
        trip(Branch(5, 7, "1"), at=1.083),
        stop=6.0,
    )
    sim = pw.ts.solve(ctg, record=[TS.Gen.Delta])

print(sim)
angles = sim[TS.Gen.Delta].droplevel("GenID", axis=1)
relative = angles.drop(columns=1).sub(angles[1], axis=0)  # rotor angles against generator 1

fig, ax = plt.subplots(figsize=(7.6, 3.0))
for bus in relative:
    ax.plot(relative.index, relative[bus], label=f"G{bus}")
ax.set(xlabel="Time (s)", ylabel="Rotor angle relative to G1 (deg)", xlim=(0, 6))
ax.legend()
