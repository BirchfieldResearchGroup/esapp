"""Contingencies

Check every line and transformer outage for limit violations, then study a double-circuit outage.
"""

import matplotlib.pyplot as plt

from esapp import PowerWorld
from esapp.commands import CTGRestoreReference, CTGSolve
from esapp.ctg import Contingency
from esapp.grid import Branch

with PowerWorld("Hawaii40.pwb") as pw:
    pw.ctg.auto_insert()  # one contingency per line and transformer
    results = pw.ctg.solve()

    n2 = Contingency("Pearl City N-2", Branch(19, 23, "1"), Branch(19, 23, "2"))
    pw.ctg.define(n2)  # saved in the case, replacing any of the same name
    both = pw.ctg.solve(n2.name)

    before = pw[Branch.LinePercent]
    pw.run(CTGSolve(n2.name))  # unlike pw.ctg.solve, this leaves the outage in place
    after = pw[Branch.LinePercent]
    pw.run(CTGRestoreReference())

solved = results.solved  # True where CTGSolved is YES
print(f"{solved.sum()} of {len(solved)} solved; unsolved: {results.unsolved}")
worst = results.violations.nlargest(4, "LimViolPct")
print(worst[["CTGLabel", "LimViolCat", "LimViolPct"]].to_string(index=False))
print(both.violations[["LimViolID", "LimViolPct"]].round(1).to_string(index=False))

fig, ax = plt.subplots(figsize=(5.2, 4.6))
ax.axline((0, 0), slope=1, color="0.6", lw=0.6)  # no change
ax.axhline(100, color="0.6", lw=0.6)  # the rating
ax.scatter(before, after, s=18)
ax.set(xlabel="Pre-contingency loading (%)", ylabel="Post-contingency loading (%)", xlim=(0, 150))
ax.set_ylim(0, 150)
