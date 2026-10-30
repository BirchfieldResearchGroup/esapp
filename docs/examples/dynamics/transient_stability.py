"""Transient stability

Apply a three-cycle fault and plot the voltage at every bus.
"""

import matplotlib.pyplot as plt

from esapp import PowerWorld
from esapp.grid import Bus
from esapp.ts import TS, Contingency, clear_fault, fault

with PowerWorld("Hawaii40.pwb") as pw:
    ctg = Contingency("bus 1", fault(Bus(1), at=1.0), clear_fault(Bus(1), at=1.05), stop=5.0)
    sim = pw.ts.solve(ctg, record=[TS.Bus.VPU])

print(sim)
volts = sim[TS.Bus.VPU]  # rows: times, columns: buses
dip = 1 - volts.loc[1.0:1.05].min()  # how far each bus fell during the fault
print(f"{(dip > 0.5).sum()} of {dip.size} buses fell below half voltage")

fig, ax = plt.subplots(figsize=(7.6, 3.0))
for bus in dip.sort_values().index:  # draw the deepest dips last, on top
    ax.plot(volts.index, volts[bus], color="C0", alpha=0.2 + 0.8 * dip[bus])
ax.set(xlabel="Time (s)", ylabel="Voltage (pu)", xlim=(0.6, 3.0), ylim=(0, 1.1))
