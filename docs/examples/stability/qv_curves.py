"""QV curves

Trace QV curves at Hawaii40's 138 kV buses and find each bus's reactive power margin.
"""

import matplotlib.pyplot as plt

from esapp import PowerWorld
from esapp.grid import Bus

with PowerWorld("Hawaii40.pwb") as pw:
    pw.pf.solve()
    qv = pw.qv.solve(Bus.BusNomVolt >= 100)
    names = pw[Bus.BusName]

margin = (-qv.summary["qv_Qmin"]).droplevel("CaseName").sort_values()  # Mvar
print(margin.rename("Margin (Mvar)").round(0).to_string())

fig, ax = plt.subplots(figsize=(7.6, 3.4))
for bus in margin.index[[0, len(margin) // 2, -1]]:  # the smallest, middle and largest margins
    curve = qv.curve(bus)
    ax.plot(curve.index, curve, label=names[bus].strip())
ax.axhline(0, color="0.6", lw=0.6)
ax.set(xlabel="Voltage (pu)", ylabel="Reactive power (Mvar)")
ax.legend()
