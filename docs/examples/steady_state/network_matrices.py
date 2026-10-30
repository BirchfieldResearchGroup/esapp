"""Network matrices

Get the bus admittance matrix and power flow Jacobian as SciPy sparse arrays, labelled by bus.
"""

import matplotlib.pyplot as plt
import numpy as np

from esapp import PowerWorld

with PowerWorld("WECC240.pwb") as pw:
    pw.pf.solve()
    y, v = pw.pf.ybus(), pw.pf.voltages()
    jac = pw.pf.jacobian()
    kinds = pw.pf.categories()["kind"]

print(y)
print(jac)
print(kinds.value_counts().to_dict())

s = v * np.conj(y.values @ v.to_numpy()) * 100  # what each bus injects, on the 100 MVA base
print(s.abs().nlargest(3).round(1).rename("MVA"))

fig, (left, right) = plt.subplots(1, 2, figsize=(7.6, 3.9))
for ax, m, name in ((left, y, "Ybus"), (right, jac, "Jacobian")):
    ax.spy(m.values, markersize=0.8, color="C0")
    ax.set_title(f"{name}: {m.shape[0]} x {m.shape[1]}, {m.values.nnz} nonzero")
    ax.set_xticks([])
    ax.set_yticks([])
