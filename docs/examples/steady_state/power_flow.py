"""Power flow

Solve a power flow, handle one that doesn't converge, and change a solver option temporarily.
"""

import matplotlib.pyplot as plt

from esapp import NotConverged, PowerWorld
from esapp.grid import Bus
from esapp.grid import Sim_Solution_Options as Options

with PowerWorld("WECC240.pwb") as pw:
    pw.pf.solve()
    buses = pw[Bus, [Bus.BusNomVolt, Bus.BusPUVolt, Bus.BusAngle]]

    with pw.options({Options.MaxItr: 1}):  # restored when the block ends
        try:
            pw.pf.solve(flat=True)
        except NotConverged as err:
            print(type(err).__name__, "-", err.message.split(": ")[-1])
    print("iterations allowed again:", pw[Options.MaxItr].iloc[0])

volts = buses["BusPUVolt"]
print(f"{len(buses)} buses, voltages from {volts.min():.3f} to {volts.max():.3f} pu")

fig, ax = plt.subplots(figsize=(7.6, 3.0))
for kv in (500, 345, 230):  # transmission voltages, highest first
    level = buses[buses["BusNomVolt"] == kv]
    ax.scatter(level["BusAngle"], level["BusPUVolt"], s=16, label=f"{kv} kV")
ax.set(xlabel="Voltage angle (deg)", ylabel="Voltage magnitude (pu)")
ax.legend()
