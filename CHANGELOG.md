# Changelog

## 1.0.0 (2026-10-30)

Not compatible with 0.2.

- `PowerWorld`: one session per Simulator process, used in a `with` block. Read with
  `pw[Bus, fields, where]` and write with `pw[Gen, Gen.GenMW] = 50.0`.
- `esapp.grid`: a class for each of Simulator's 957 object types, with their fields and lists
  such as `Gen.BidCurve`.
- Filtering inside Simulator, by saved filter, keyword, single object, or conditions on fields.
- `esapp.commands`: all 348 documented script commands as typed classes, which can run in
  batches.
- Analyses: `pw.pf` (including the Ybus, Jacobian and bus types), `pw.ctg`, `pw.sens`,
  `pw.groups`, `pw.pv`, `pw.opf`, `pw.atc`, `pw.qv`, `pw.gic` (including the G-matrix),
  `pw.fault`, `pw.files` and `pw.log`.
- `esapp.ctg` and `esapp.ts`: build static and transient contingencies in Python.
- Transient stability: records only the fields you ask for, transfers results in chunks into
  single-precision NumPy arrays, saves sweeps straight to disk, and can pause between time steps.
- `esapp.matrix`: sparse matrices labelled by bus (`pip install esapp[sparse]`).
- Specific exceptions, such as `NotConverged`, `ObjectNotFound` and `PrerequisiteError`.
- `esapp.testing` (`pip install esapp[testing]`): pytest fixtures that load automatically, a fake
  SimAuto for tests without PowerWorld, and live tests that run on copies of a case.
