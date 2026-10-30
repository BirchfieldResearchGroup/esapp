"""Contingency sweeps

Trip generators one at a time and save each simulation to disk as it finishes.
"""

import matplotlib.pyplot as plt

from esapp import PowerWorld
from esapp.grid import Gen
from esapp.ts import TS, Contingency, trip

with PowerWorld("Hawaii40.pwb") as pw:
    gens = pw.refs(Gen, Gen.GenID.eq("1"))[:4]  # unit 1 at four plants
    trips = [Contingency(f"gen {g.key[0]}-{g.key[1]}", trip(g, at=0.5), stop=3.0) for g in gens]
    store = pw.ts.sweep(trips, record=[TS.Gen.W], every=2).save("simulations")

fig, ax = plt.subplots(figsize=(7.6, 3.0))
for name in store:
    sim = store[name]  # read from disk only as it's used
    speed = sim[TS.Gen.W]  # rows: times, columns: generators (per unit)
    swing = speed.max().max() - speed.min().min()
    print(f"{name}: {type(sim.values).__name__}, swing {swing:.5f} pu")
    ax.plot(speed.index, 60 * speed.mean(axis=1), label=name)  # average speed, in Hz
ax.set(xlabel="Time (s)", ylabel="Average rotor speed (Hz)", xlim=(0, 3))
ax.legend(loc="lower left")
