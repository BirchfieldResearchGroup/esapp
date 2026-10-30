"""Grid maps

Draw Texas7k from its substation coordinates and color each branch by how loaded it is.
"""

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection

from esapp import PowerWorld
from esapp.grid import Branch, Bus, Substation

with PowerWorld("Texas7k.pwb") as pw:
    pw.pf.solve()
    where = pw[Substation, [Substation.Longitude, Substation.Latitude]].set_index("SubNum")
    sub = pw[Bus.SubNum]  # each bus's substation, by BusNum
    lines = pw[Branch, Branch.LinePercent]

ends = [where.loc[sub.loc[lines[end]]].to_numpy() for end in ("BusNum", "BusNum:1")]
segments = np.stack(ends, axis=1)  # a start and end point for each branch
loading = lines["LinePercent"].to_numpy()
print(f"{len(lines)} branches between {len(where)} substations")
print(f"loading: median {np.median(loading):.0f}%, highest {loading.max():.0f}%")

order = np.argsort(loading)  # draw the most loaded last, on top
fig, ax = plt.subplots(figsize=(6.4, 5.6))
drawn = ax.add_collection(LineCollection(list(segments[order]), array=loading[order], lw=0.7))
ax.autoscale()
ax.set_aspect(1 / np.cos(np.radians(31)))  # a degree of longitude is shorter than one of latitude
ax.set_axis_off()
fig.colorbar(drawn, ax=ax, shrink=0.5, label="Loading (%)")
