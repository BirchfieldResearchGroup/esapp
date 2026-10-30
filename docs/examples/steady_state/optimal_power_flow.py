"""Optimal power flow

Solve an OPF on Hawaii40, then reduce the rating of a transmission path to see its LMPs separate.
"""

import matplotlib.pyplot as plt

from esapp import PowerWorld
from esapp.grid import Branch

with PowerWorld("Hawaii40.pwb") as pw:
    pw.pf.solve()
    pw.opf.solve()
    uncongested = pw.opf.lmp()

    path = Branch.BusNum.eq(19) & Branch.BusNum[1].eq(23)  # four parallel circuits
    pw[Branch, Branch.LineAMVA, path] = 72.0
    pw.opf.solve()
    congested = pw.opf.lmp()
    flows = pw[Branch, Branch.LineMVA, path]

print(f"flow on each circuit: {flows['LineMVA'].max():.1f} MVA")
print(f"LMP without the limit: {uncongested['BusMCMW'].iloc[0]:.2f} $/MWh at every bus")
print(congested.sort_values("BusMCMW").iloc[[0, -1]].round(2).to_string())

fig, ax = plt.subplots(figsize=(7.6, 3.2))
ax.bar(congested.index, congested["BusMWMCEnergy"], label="Energy")
ax.bar(
    congested.index,
    congested["BusMWMCCongestion"],
    bottom=congested["BusMWMCEnergy"],
    label="Congestion",
)
ax.axhline(
    uncongested["BusMCMW"].iloc[0], color="0.3", ls="--", lw=1, label="LMP without the limit"
)
ax.set(xlabel="Bus number", ylabel="LMP ($/MWh)")
ax.legend(ncols=3, loc="lower center", bbox_to_anchor=(0.5, 1.0))
