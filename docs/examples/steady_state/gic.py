"""GIC

Find which transformers in Texas7k are most exposed to geomagnetically induced currents, and
from which direction.
"""

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from esapp import PowerWorld

headings = list(range(0, 360, 10))  # degrees: 0 is north, 90 east
with PowerWorld("Texas7k.pwb") as pw:
    results = {h: pw.gic.calculate(1.0, h) for h in headings}  # one per direction
    pw.gic.clear()

# rows: directions, columns: transformers
amps = pd.DataFrame({h: r["GICXFIEffective1"] for h, r in results.items()}).T
mvar = pd.Series({h: r["GICQLosses"].sum() for h, r in results.items()})
print(f"{amps.shape[1]} transformers; total losses {mvar.min():.0f} to {mvar.max():.0f} Mvar")
peak = amps.max().sort_values(ascending=False)
exposed = peak[~peak.index.get_level_values(0).duplicated()].head(3)  # at three different buses
print(exposed.round(1))

fig, ax = plt.subplots(figsize=(5.0, 5.0), subplot_kw={"projection": "polar"})
around = np.radians(90 - np.array([*headings, headings[0]]))  # compass angles, loop closed
for key in exposed.index:  # the three most exposed
    ax.plot(around, [*amps[key], amps[key].iloc[0]], label=f"Bus {key[0]}")
ax.set_xticks(np.radians([90, 0, 270, 180]), ["N", "E", "S", "W"])
ax.set_title("Effective GIC (A/phase)", pad=18)
ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.2), ncols=3)
