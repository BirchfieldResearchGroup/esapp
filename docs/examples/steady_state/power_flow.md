---
myst:
  html_meta:
    description: "Solve a PowerWorld power flow from Python, catch a solve that does not converge, change solution options for one block, and plot every bus's voltage."
---

# Power flow

Solve a power flow, handle one that doesn't converge, and change a solver option temporarily.

```{example} power_flow.py
:alt: WECC240's 500, 345 and 230 kV buses, each a dot at its voltage angle and magnitude: magnitudes from 0.96 to 1.13 pu, angles from -35 to 30 degrees
```
