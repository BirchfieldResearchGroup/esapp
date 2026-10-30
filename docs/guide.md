---
myst:
  html_meta:
    description: "How to use esapp with PowerWorld Simulator: sessions, reading and writing data, selections, analyses, matrices, transient stability, commands, errors and testing."
---

# Guide

## Basics

- A `PowerWorld` session runs one copy of Simulator with one case open, and uses one SimAuto
  license. Open it in a `with` block so Simulator closes when you're done.
- Data comes back as pandas DataFrames. Columns use PowerWorld's field names and units, with the
  key columns first.
- When you write `True` or `False`, esapp turns it into the field's own words, like `"Closed"` or
  `"YES"`.
- Writes take effect right away. `pw.snapshot()`, `pw.options()` and `pw.edit_mode()` undo their
  changes when their `with` block ends. Inside `pw.read_only()`, any write raises an error.
- When Simulator refuses something, esapp raises an exception. All of them inherit from
  `PowerWorldError`.

## Objects and fields

`esapp.grid` has a class for each of Simulator's {doc}`object types <catalog/objects>`. A class's attributes
are its fields:

```python
from esapp.grid import Branch, Bus, Gen
from esapp.schema import GObject

Bus.BusPUVolt  # a float field: the per-unit voltage
Gen(2, "1")  # one generator, by its keys
Branch.BusNum[1]  # the field BusNum:1, the bus at the far end
Bus.schema.key_names  # ['BusNum']
Gen.BidCurve  # a list each generator holds, with columns MW and Price
GObject.parse("GEN 2 1")  # Gen(2, '1'), from Simulator's text for an object
```

Simulator pads IDs with spaces (`"1 "`). esapp strips them, so a key you read back matches the
one you typed.

## Reading data

| Code | Returns |
| --- | --- |
| `pw[Bus]` | the key columns |
| `pw[Bus, Bus.BusName]`, `pw[Bus, ["BusName", "BusPUVolt"]]` | the keys, then those fields |
| `pw[Bus, :]` | the keys, then every field |
| `pw[Bus.BusPUVolt]` | one field, as a Series indexed by key |
| `pw[Bus(5), fields]` | one object |
| `pw[Bus, fields, where]` | only the objects that match `where` |
| `pw[Gen, Gen.BidCurve]`, `pw[Gen(2, "1"), Gen.BidCurve]` | a list's rows: keys, `point`, then its columns |

Each read is one call to Simulator.

## Writing data

| Code | Writes |
| --- | --- |
| `pw[Gen, Gen.GenMW] = 100.0` | every generator |
| `pw[Gen, Gen.GenMW, where] = 50.0` | the generators that match |
| `pw[Gen(2, "1"), Gen.GenMW] = 75.0` | one generator |
| `pw[Load.LoadSMW] = series` | each load its own value, matched by key |
| `pw[Gen] = frame` | the generators in the DataFrame, matched by key or label |
| `pw.create(Bus, frame)` | new buses |
| `pw.delete(Gen, where)` | removes the generators that match |
| `pw[Gen(2, "1"), Gen.BidCurve] = frame` | replaces the generator's list; `[]` empties it |

## Creating objects

`pw.create` takes a DataFrame with each object's keys and the fields Simulator requires for its
type; if any are missing, the error names them. To start from an empty case, run `NewCase`, and
create the network inside `pw.edit_mode()`, since a case isn't valid until it's complete:

```python
from esapp.commands import NewCase

with PowerWorld() as pw:
    pw.run(NewCase())
    with pw.edit_mode():
        pw.create(Bus, buses)
        pw.create(Gen, generators)
```

{doc}`examples/data/grid_construction` builds a complete case with dynamic models.

## Selecting objects

Selections run inside Simulator, so only the matching objects come back to Python.

| Selection | Matches |
| --- | --- |
| `"Area 1 buses"` | a filter saved in the case |
| `Kw.SELECTED`, `Kw.AREAZONE` | objects marked as selected, or inside the case's area/zone filter |
| `Gen(2, "1")` | one object |
| `Bus.BusNomVolt > 100` | a condition; also `eq`, `ne`, `between`, `contains`, `startswith`, `isblank` |
| `(a & b) \| c` | combined conditions (esapp builds a temporary filter for these) |

To reuse a combined selection across calls, name it with {meth}`pw.filter <esapp.PowerWorld.filter>`:

```python
with pw.filter(Bus.AreaNum.eq(1) & (Bus.BusNomVolt > 100)) as hv:
    volts = pw[Bus, Bus.BusPUVolt, hv]
    pw[Bus, Bus.BusVoltSet, hv] = 1.02
```

## Analyses

| Attribute | Covers |
| --- | --- |
| {class}`pw.pf <esapp.apps.pf.PowerFlow>` | power flow: `solve`, `voltages`, `mismatches`, `categories`, `ybus`, `jacobian` |
| {class}`pw.ctg <esapp.apps.ctg.Contingencies>` | contingency analysis: `auto_insert`, `define`, `solve`, `results`, `contingencies` |
| {class}`pw.sens <esapp.apps.sens.Sensitivity>` | PTDFs, LODFs, shift factors and voltage sensitivities |
| {class}`pw.groups <esapp.apps.groups.Groups>` | injection groups, used as sources and sinks for transfers |
| {class}`pw.pv <esapp.apps.pv.PV>` | PV curves between two injection groups |
| {class}`pw.opf <esapp.apps.opf.OPF>` | optimal power flow and bus prices |
| {class}`pw.atc <esapp.apps.atc.ATC>` | available transfer capability, and what limits it |
| {class}`pw.qv <esapp.apps.qv.QV>` | QV curves |
| {class}`pw.gic <esapp.apps.gic.GIC>` | geomagnetically induced currents and the G-matrix |
| {class}`pw.fault <esapp.apps.fault.Faults>` | fault currents and voltages |
| {class}`pw.files <esapp.apps.files.Files>` | saving the case, writing data, loading auxiliary files |
| {class}`pw.log <esapp.apps.log.Log>` | Simulator's message log |

A contingency given just an object opens it:

```python
from esapp.ctg import Contingency, trip

n2 = Contingency("N-2", Branch(1, 5, "1"), trip(Branch(1, 5, "2"), delay=0.5))
pw.ctg.solve(n2).violations  # solves it without changing the case
pw.ctg.define(n2)  # or saves it in the case
```

`CTGSolved` keeps Simulator's status (`YES`, `PARTIAL`, `NO`, ...), and `results.solved` shows
which contingencies solved fully. If opening a line would cut buses off from the rest of the
grid, it has no LODFs, and its column in `pw.sens.lodf_matrix` is NaN.

## Matrices

`pw.pf.ybus()`, `pw.pf.jacobian()` and `pw.gic.gmatrix()` return a `Matrix`: a SciPy sparse
array, with pandas indexes for its rows and columns. They need SciPy: `pip install esapp[sparse]`.

```python
y = pw.pf.ybus()  # rows and columns by BusNum
jac = pw.pf.jacobian()  # rows (equation, BusNum), columns (variable, BusNum)
y.values @ pw.pf.voltages().to_numpy()  # each bus's current injection, in per unit
```

Every bus gets two Jacobian rows. Rows for fixed quantities (the slack bus's angle and voltage, a
PV bus's voltage) are identity rows. `jacobian("rectangular")` and `jacobian("dc")` give the
other forms.

## Transient stability

A simulation records only the fields you ask for:

```python
from esapp.ts import TS, Contingency, clear_fault, fault

ctg = Contingency("bus 1", fault(Bus(1), at=1.0), clear_fault(Bus(1), at=1.05), stop=5.0)
sim = pw.ts.solve(ctg, record=[TS.Bus.VPU, (Gen, TS.Gen.W)])
sim[TS.Bus.VPU]  # rows are times, columns are buses, in single precision like Simulator
```

`pw.ts.sweep(ctgs, record=...)` simulates many contingencies one after another, and `.save(path)`
writes them to disk. `pw.ts.step(ctg, record=...)` pauses between time steps, so you can read or
change things. {doc}`catalog/objects` lists every field you can record.

## Commands

`esapp.commands` has a class for every script command PowerWorld documents. Pass several to
`pw.run` to send them in one call, or run any command by name:

```python
from esapp.commands import CTGSolveAll, SolvePowerFlow

pw.run(SolvePowerFlow("POLARNEWTON"), CTGSolveAll())  # one call, run in order
pw.run("SetData", Gen, [Gen.GenMW], [50.0], Gen.BusNum.eq(2))
```

When a command takes a filter, you can pass any selection. If your Simulator is too old for a
command, you get `UnknownCommand`, with the build you need.

## Errors

| Exception | Raised when |
| --- | --- |
| `PowerWorldError` | Simulator refuses a call (the message is Simulator's) |
| `NotConverged` | a solve doesn't converge |
| `ObjectNotFound` | an object, field or filter doesn't exist |
| `UnknownCommand` | your Simulator doesn't have the command |
| `SimulatorFault` | Simulator crashes inside a call (an access violation) |
| `ReadOnlySessionError` | you write inside `pw.read_only()` |
| `ComError` | the connection fails: Simulator closed or stopped responding |

{doc}`api/data/errors` lists every exception.

## Testing

`pip install esapp[testing]` gives you the fixtures below
once your top-level `conftest.py` has `pytest_plugins = ["esapp.testing"]`. Unit tests use `fake_pw`, a session over an in-memory fake
SimAuto, so they don't need PowerWorld:

```python
from esapp.grid import Bus


def test_low_voltage(fake_pw, fake_simauto):
    fake_simauto.add(Bus, {"BusNum": [1, 2], "BusPUVolt": [1.0, 0.94]})
    low = fake_pw[Bus, Bus.BusPUVolt, Bus.BusPUVolt < 0.95]
    assert low["BusNum"].tolist() == [2]
```

Tests marked `@powerworld` get `pw`, a real session on the case in `$ESAPP_TEST_CASE`. Run them
with `pytest --pw=require` on a machine with Simulator.
