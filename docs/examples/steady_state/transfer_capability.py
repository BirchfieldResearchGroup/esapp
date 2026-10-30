"""Transfer capability

Find how many more megawatts can move between two buses in Hawaii40, and which lines limit them.
"""

import matplotlib.pyplot as plt

from esapp import PowerWorld
from esapp.grid import Bus

with PowerWorld("Hawaii40.pwb") as pw:
    pw.pf.solve()
    transfer = pw.atc.determine(Bus(28), Bus(5))
    names = pw[Bus.BusName]

print(f"{transfer.limit:.1f} MW more can move from {names[28]} to {names[5]}")
limiters = transfer.limiters.head(5)
print(
    limiters[["CTGViolMaxLine", "LimViolID", "LimViolValue", "LimViolLimit"]]
    .round(1)
    .to_string(index=False)
)

fig, ax = plt.subplots(figsize=(7.6, 2.8))
ax.barh(limiters["LimViolID"][::-1], limiters["CTGViolMaxLine"][::-1])
ax.set(xlabel="Transfer limit (MW)")
