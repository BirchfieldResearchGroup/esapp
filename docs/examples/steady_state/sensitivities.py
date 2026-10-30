"""Sensitivities

Find which branches carry a transfer, and which pick up the flow when a line opens.
"""

import matplotlib.pyplot as plt
import numpy as np

from esapp import PowerWorld
from esapp.grid import Branch, Bus

with PowerWorld("Hawaii40.pwb") as pw:
    ptdf = pw.sens.ptdf(Bus(1), Bus(5))  # fractions of the transfer, by branch
    lines = pw.refs(Branch, Branch.BranchDeviceType.eq("Line"))
    lodf = pw.sens.lodf_matrix(lines)  # rows: every branch, columns: each line outage

print(ptdf.abs().nlargest(3).round(3))

radial = lodf.columns[lodf.isna().all()]  # outages that cut buses off have no LODFs
keys = [line.key for line in lines if line.key not in radial]
shares = lodf.loc[keys, keys].abs().to_numpy(copy=True)
np.fill_diagonal(shares, np.nan)  # skip each line's entry for itself
print(f"{len(keys)} lines and {len(radial)} radial; an outage moves up to {np.nanmax(shares):.0%}")

fig, ax = plt.subplots(figsize=(5.6, 4.6))
shown = ax.imshow(shares, vmin=0, vmax=1)
ax.set(xlabel="Outaged line", ylabel="Monitored line", xticks=[], yticks=[])
fig.colorbar(shown, ax=ax, label="|LODF|")
