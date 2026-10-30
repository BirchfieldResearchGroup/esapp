"""PV curves

Find how much power can move from the Northwest into California before voltage collapses, with
the California-Oregon Intertie in service and with one or both circuits out.
"""

import matplotlib.pyplot as plt

from esapp import PowerWorld
from esapp.ctg import Contingency
from esapp.grid import Branch, Bus, Gen, Load
from esapp.grid import PVCurve_Options as Options

INTERTIE = Branch(3906, 4001, "1"), Branch(3906, 4001, "2")  # Round Mountain to Malin, 500 kV

with PowerWorld("WECC240.pwb") as pw:
    pw.pf.solve()
    pw.ctg.define(
        Contingency("One circuit out", INTERTIE[0]),
        Contingency("Both circuits out", *INTERTIE),
    )
    north = pw.groups.create("north", Gen, Gen.AreaNum.eq(3), share="MAX GEN INC")
    california = pw.groups.create("california", Load, Load.AreaNum.eq(2), share="LOAD MW")
    # Simulator stops every scenario at the first nose unless told to find them all.
    with pw.options({Options.PVCRunBaseToCompletion: True, Options.PVCNumLimitCases: 3}):
        pv = pw.pv.solve(north, california, buses=Bus.AreaNum.eq(2) & (Bus.BusNomVolt >= 500))
    names = pw[Bus.BusName]

print(pv.summary[["PVResultMaxShift", "PVResultCriticalReason"]].to_string())

nose = pv.curve().iloc[-1].droplevel("field")  # base case voltages at its nose
weakest = str(nose.idxmin())  # "Bus '8002'"
bus = int(weakest.split("'")[1])
print(f"weakest 500 kV bus: {names[bus].strip()} ({bus})")

fig, ax = plt.subplots(figsize=(7.6, 3.4))
for scenario in pv.summary.index:
    curve = pv.curve(scenario).droplevel("field", axis=1)[weakest]
    (line,) = ax.plot(curve.index, curve, label=scenario)
    ax.plot(curve.index[-1], curve.iloc[-1], "o", color=line.get_color())  # the nose
ax.set(xlabel="Transfer (MW)", ylabel=f"Voltage at {names[bus].strip()} (pu)")
ax.legend()
