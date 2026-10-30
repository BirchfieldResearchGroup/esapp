---
myst:
  html_meta:
    description: "Every PowerWorld object type in esapp.grid with its key fields, and every field a transient simulation can record."
---

# Objects & Fields

`esapp.grid` has a class for each of Simulator's object types. Its attributes are the type's
fields, and calling it refers to one object by its key:

```python
from esapp.grid import Bus

Bus.BusPUVolt  # a field
Bus(5)  # one bus
```

## Object types

Each type links to its generated class, which lists every field with PowerWorld's description.

```{objects}
```

## Fields a simulation can record

`esapp.ts.TS` lists the fields a transient simulation can record, by object type, under shorter
names: `TS.Gen.W` is the same field as `Gen.TSGenW`. Pass them to `record`, and esapp turns on the
settings that make Simulator store them:

```python
pw.ts.solve(ctg, record=[TS.Bus.VPU, (Gen(2, "1"), TS.Gen.W)])
```

```{recordable}
```
