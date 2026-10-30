"""Frequency response

Trip the largest generator in WECC240 and measure how far the frequency falls.
"""

import matplotlib.pyplot as plt

from esapp import PowerWorld
from esapp.grid import Gen
from esapp.ts import TS, Contingency, trip

with PowerWorld("WECC240.pwb") as pw:
    output = pw[Gen.GenMW]  # by (BusNum, GenID)
    largest = max(pw.refs(Gen), key=lambda gen: output[gen.key])
    ctg = Contingency("trip", trip(largest, at=1.0), stop=20.0)
    sim = pw.ts.solve(ctg, record=[TS.Bus.Frequency])

hz = sim[TS.Bus.Frequency]  # rows are times, columns are buses
lowest = hz.min(axis=1)  # the lowest frequency at each time
print(f"tripped {output.max():.0f} MW of {output.sum():.0f} MW, at bus {largest.key[0]}")
print(f"nadir {lowest.min():.3f} Hz at {lowest.idxmin():.2f} s")
print(f"settling near {hz.iloc[-1].mean():.3f} Hz")

fig, ax = plt.subplots(figsize=(7.6, 3.0))
ax.plot(hz.index, hz.to_numpy(), color="C0", alpha=0.3)  # every bus in one color
ax.set(xlabel="Time (s)", ylabel="Frequency (Hz)", xlim=(0, 20))
