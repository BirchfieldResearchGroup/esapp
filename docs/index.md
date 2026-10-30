---
myst:
  html_meta:
    description: "esa++ is Python for PowerWorld: read and write data as pandas DataFrames, run any script command, and get transient stability results as NumPy arrays."
---

# esa++

:::{container} banner
```{image} _static/banner.svg
:class: only-light
:alt: esa++, Python for PowerWorld, beside the Texas7k transmission network
```
```{image} _static/banner-dark.svg
:class: only-dark
:alt: esa++, Python for PowerWorld, beside the Texas7k transmission network
```
:::

:::{container} lede
Read and write PowerWorld data as pandas DataFrames, run any script command, and get transient
stability results as NumPy arrays. Every object type and field is typed.
:::

## Install

```bash
pip install esapp
```

Needs Windows, PowerWorld Simulator with SimAuto, and Python 3.12 to 3.14. For the Ybus,
Jacobian and G-matrix, also install SciPy: `pip install esapp[sparse]`.

## Quickstart

```python
from esapp import PowerWorld
from esapp.grid import Bus, Gen

with PowerWorld("Hawaii40.pwb") as pw:
    pw.pf.solve()
    volts = pw[Bus.BusPUVolt]  # a Series indexed by BusNum
    low = pw[Bus, Bus.BusName, Bus.BusPUVolt < 0.97]  # filtered inside Simulator
    pw[Gen, Gen.GenMW, Gen.AreaNum.eq(1)] = 50.0  # sets only the matching generators
```

## Examples

```{gallery}
data/grid_construction
data/grid_maps
stability/pv_curves
dynamics/transient_stability
steady_state/gic
steady_state/contingencies
```

All examples: {doc}`Data <examples/data/index>`, {doc}`Steady state <examples/steady_state/index>`,
{doc}`Stability <examples/stability/index>` and {doc}`Dynamics <examples/dynamics/index>`. They use three cases:

- **Hawaii40**: 37 buses on Oahu, with dynamic models, from the
  [Texas A&M Electric Grid Test Case Repository](https://electricgrids.engr.tamu.edu).
- **WECC240**: NREL's simplified 240-bus model of the western US grid, with dynamic models.
- **Texas7k**: 6,717 buses across Texas, with a location for every substation.

## Author

[Luke Lowery](https://lukelowry.github.io/), PhD student in the
[Birchfield Research Group](https://birchfield.engr.tamu.edu/) at Texas A&M University ·
[Google Scholar](https://scholar.google.com/citations?user=CTynuRMAAAAJ&hl=en) ·
[ORCID](https://orcid.org/0009-0000-0029-3403)

Also by the author: [pyvectorfit](https://pyvectorfit.readthedocs.io), vector fitting for EMT line
models · [OpenLine](https://openline.readthedocs.io), overhead transmission lines ·
[latkit](https://latkit.readthedocs.io), GPU visualization of large networks.

<nav class="project-links" aria-label="Project links">
  <a href="https://github.com/BirchfieldResearchGroup/esapp">GitHub</a>
  <a href="https://pypi.org/project/esapp/">PyPI</a>
  <a href="https://github.com/BirchfieldResearchGroup/esapp/actions/workflows/ci.yml">CI</a>
  <a href="https://github.com/BirchfieldResearchGroup/esapp/blob/main/LICENSE">Apache 2.0 license</a>
</nav>

```{toctree}
:maxdepth: 1
:titlesonly:
:hidden:

Start here <self>
guide
```

```{toctree}
:maxdepth: 1
:titlesonly:
:hidden:
:caption: Examples

examples/data/index
examples/steady_state/index
examples/stability/index
examples/dynamics/index
```

```{toctree}
:maxdepth: 1
:titlesonly:
:hidden:
:caption: Catalog

catalog/objects
catalog/commands
```

```{toctree}
:maxdepth: 1
:titlesonly:
:hidden:
:caption: API

api/data/index
api/steady_state/index
api/stability/index
api/dynamics
api/integration/index
```

```{toctree}
:maxdepth: 1
:titlesonly:
:hidden:
:caption: Development

development/contributing
development/changelog
```

```{toctree}
:hidden:
:caption: Related

pyvectorfit <https://pyvectorfit.readthedocs.io>
OpenLine <https://openline.readthedocs.io>
latkit <https://latkit.readthedocs.io>
```
