---
myst:
  html_meta:
    description: "The bus admittance matrix and power flow Jacobian from PowerWorld, as SciPy sparse matrices labelled by bus, and their sparsity."
---

# Network matrices

Get WECC240's bus admittance matrix and power flow Jacobian as SciPy sparse arrays, with rows and columns labelled by bus.

```{example} network_matrices.py
:alt: The sparsity patterns of WECC240's Ybus, 243 by 243, and its Jacobian, 486 by 486 in four blocks, each nonzero a dot along a banded diagonal
```
