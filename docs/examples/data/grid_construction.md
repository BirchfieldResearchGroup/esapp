---
myst:
  html_meta:
    description: "Build the WSCC 9-bus system in PowerWorld from Python, one component at a time, add dynamic models, and simulate a fault."
---

# Grid construction

Create the WSCC 9-bus system one component at a time, add machine, exciter and governor models, and simulate a fault cleared by opening a line. Each object type's required fields are listed under its class in {doc}`../../catalog/objects`.

The network data and most machine data come from Anderson and Fouad, *Power System Control and Stability*, chapter 2, and the power flow below matches theirs. The book doesn't give subtransient reactances, generator 1's quadrature-axis time constants, or governor data, so those are typical values.

```{example} grid_construction.py
:alt: Rotor angles of generators 2 and 3 relative to generator 1 through a five-cycle fault at bus 7: both swing widely and stay in step with generator 1
```
