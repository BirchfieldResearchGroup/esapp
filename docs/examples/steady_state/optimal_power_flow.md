---
myst:
  html_meta:
    description: "Solve an optimal power flow in PowerWorld from Python and split each bus's price into energy, congestion and losses."
---

# Optimal power flow

Solve an OPF on Hawaii40, then reduce the rating of a transmission path to see its LMPs separate.

```{example} optimal_power_flow.py
:alt: Stacked bars of each Hawaii40 bus's LMP at the reduced rating, split into energy and congestion, with a dashed line at the uniform LMP without the limit
```
