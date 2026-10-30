---
myst:
  html_meta:
    description: "Trace PV curves in PowerWorld from Python to find the transfer limit before voltage collapse, with and without a line outage."
---

# PV curves

Find how much power can move from the Northwest into California before voltage collapses, with the California-Oregon Intertie in service and with one or both of its circuits out. Each curve ends at its nose, the largest transfer with a power flow solution.

```{example} pv_curves.py
:alt: Voltage at the weakest 500 kV bus in California against the transfer, for the base case and with one or both intertie circuits out: each curve falls slowly, then steeply to its nose, and outages move the nose to a smaller transfer
```
