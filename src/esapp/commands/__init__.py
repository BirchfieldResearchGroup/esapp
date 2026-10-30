"""PowerWorld script commands, one class per documented command.

::

    from esapp.commands import CTGSolveAll, SolvePowerFlow

    pw.run(SolvePowerFlow(), CTGSolveAll(do_distributed=False))   # one batch, one COM call
    str(SolvePowerFlow("POLARNEWTON"))                             # 'SolvePowerFlow(POLARNEWTON);'

The catalog is generated from PowerWorld's "Auxiliary File Format" document
(``schema/commands.tsv``) and ``schema/commands.toml``; it loads on first use.
"""

from importlib import import_module
from typing import Any

from esapp.commands._command import Command as Command


def __getattr__(name: str) -> Any:
    catalog = import_module("esapp.commands._catalog")
    if name == "__all__":  # a star import takes the whole catalog
        return ["Command", *catalog.__all__]
    try:
        value = getattr(catalog, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({"Command", *import_module("esapp.commands._catalog").__all__})
