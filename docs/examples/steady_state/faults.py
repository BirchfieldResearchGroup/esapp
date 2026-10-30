"""Faults

Calculate three-phase and single-line-to-ground fault currents at Hawaii40's 138 kV buses.
"""

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from esapp import PowerWorld
from esapp.grid import Bus

BASE_KA = 100 / (np.sqrt(3) * 138)  # 100 MVA at 138 kV

with PowerWorld("Hawaii40.pwb") as pw:
    pw.pf.solve()
    buses = pw.refs(Bus, Bus.BusNomVolt.eq(138))
    current = pd.DataFrame(
        {
            kind: [pw.fault.calculate(b, kind)["FaultCurMag"].loc[b.key[0]] for b in buses]
            for kind in ("3PB", "SLG")
        },
        index=[b.key[0] for b in buses],
    ).mul(BASE_KA)
    pw.fault.clear()

print(current.round(2).rename_axis("Bus").to_string())

fig, ax = plt.subplots(figsize=(7.6, 3.2))
x = np.arange(len(current))
ax.bar(x - 0.2, current["3PB"], 0.4, label="Three-phase")
ax.bar(x + 0.2, current["SLG"], 0.4, label="Single line-to-ground")
ax.set_xticks(x, current.index)
ax.set(xlabel="Bus number", ylabel="Fault current (kA)")
ax.legend(ncols=2, loc="lower center", bbox_to_anchor=(0.5, 1.0))
