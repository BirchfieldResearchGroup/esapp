---
myst:
  html_meta:
    description: "Fault a bus in a PowerWorld transient stability simulation from Python, and plot every bus voltage."
---

# Transient stability

Apply a three-cycle fault and plot the voltage at every bus.

```{example} transient_stability.py
:alt: Voltage at each of Hawaii40's 37 buses through a three-cycle fault at bus 1: all fall at one second, the faulted bus to zero, and recover to 1 pu within a second of the fault clearing
```
