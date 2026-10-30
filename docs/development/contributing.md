---
myst:
  html_meta:
    description: "Working on esapp: generated code, tests, and docs."
---

# Contributing

## Generated code

Don't edit `esapp.grid`, `esapp.ts.TS` or `esapp.commands` by hand. They're generated from the
files in `schema/`:

```bash
python -m tools.codegen                       # regenerate
python -m tools.codegen --check               # fail if anything is out of date
python -m tools.codegen --document AUX.pdf    # re-read PowerWorld's AUX file guide
python -m tools.codegen --from-simauto fields.tsv.gz --case case.pwb  # ask a running Simulator
python -m tools.codegen --compare fields.tsv.gz                       # diff two field lists
```

| File | What's in it |
| --- | --- |
| `schema/case_object_fields.tsv.gz` | every object type and field, exported from PowerWorld |
| `schema/commands.tsv` | every script command, from the AUX file guide |
| `schema/subdata.tsv` | lists that objects carry, like a generator's bid curve, from the same guide |
| `schema/overrides.toml`, `commands.toml`, `subdata.toml` | hand-written fixes to the files above |
| `schema/command_arity.tsv`, `option_vocab.toml` | what a running Simulator reports |

## Tests

```bash
pytest --pw=off                               # unit tests, no PowerWorld needed
pytest tests/live --pw=require                # live tests, needs Simulator and $ESAPP_TEST_CASE
coverage run -m pytest --pw=off               # coverage, as CI measures it
coverage report                               # fails below 90%
```

Live tests work on a copy of the case, and fail if the original file changes.

## Docs

Example pages show saved output. Re-run an example after you change it, or the build fails.
Re-running needs PowerWorld and the `cases` repository next to this one, or at `$ESAPP_CASES`.

```bash
python docs/capture.py                        # re-run every example
python docs/capture.py transient_stability              # re-run one
sphinx-build -W --keep-going -b html docs docs/_build/html
```

Plot colors live in `docs/_style.py`. Examples don't set their own.

## Releases

The version comes from the git tag. Update `CHANGELOG.md`, then publish a GitHub release with a
new tag such as `v1.0.0`. The `Release` workflow tests the package, builds it, and publishes it
to PyPI through trusted publishing, using the `pypi` environment.
