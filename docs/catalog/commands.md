---
myst:
  html_meta:
    description: "Every PowerWorld script command as a typed Python class in esapp.commands, by section, with its parameters."
---

# Script commands

```{eval-rst}
.. module:: esapp.commands
```

Every script command PowerWorld documents, as a class in `esapp.commands`. Leave a parameter as
`None` to use Simulator's default. Pass several commands to `pw.run` to send them in one call:

```python
from esapp.commands import CTGSolveAll, SolvePowerFlow

pw.run(SolvePowerFlow("POLARNEWTON"), CTGSolveAll())  # one call, run in order
```

```{eval-rst}
.. autoclass:: esapp.commands.Command
   :members: statement

.. typealias:: esapp.commands._catalog.SolutionMethod
.. typealias:: esapp.commands._catalog.LinearMethod
.. typealias:: esapp.commands._catalog.FaultType
.. typealias:: esapp.commands._catalog.ResultsLayout
.. typealias:: esapp.commands._catalog.ImageType
.. typealias:: esapp.commands._catalog.JacForm
.. typealias:: esapp.commands._catalog.MatrixFile
```

```{commands}
```
