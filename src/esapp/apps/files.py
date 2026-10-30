"""Case and data files: ``pw.files``."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from esapp._indexing import Where, select_fields
from esapp.apps._base import Namespace
from esapp.commands._catalog import LoadAux, SaveCase, SaveData
from esapp.schema import Field, GObject
from esapp.simauto._script import StrPath

__all__ = ["Files"]


class Files(Namespace):
    """Case and data files: ``pw.files``.

    ::

        pw.files.save("study.pwb")
        pw.files.write("gens.aux", Gen, [Gen.GenMW, Gen.GenStatus])
        pw.files.load_aux("changes.aux")
    """

    __slots__ = ()

    def save(self, path: StrPath, format: str = "PWB") -> Path:
        """Save the case to ``path`` (``format``: PWB, or a PTI/GE format such as PTI33)."""
        target = Path(path).resolve()
        self._pw.run(SaveCase(target, format))
        return target

    def load_aux(self, path: StrPath, *, create: bool = False) -> None:
        """Load an auxiliary file: apply its data and run its script. With ``create``, objects
        in the file that aren't in the case are added."""
        self._pw.run(LoadAux(Path(path).resolve(), create))

    def write(
        self,
        path: StrPath,
        gtype: type[GObject],
        fields: Sequence[Field[Any] | str] | None = None,
        where: Where | None = None,
        *,
        append: bool = False,
    ) -> Path:
        """Write ``gtype``'s objects to ``path``: as an auxiliary file's DATA section if the name
        ends in ``.aux``, otherwise as CSV. ``where`` selects the objects and ``fields`` the
        columns (all of them by default), after the key fields. With ``append``, add to the
        file instead of replacing it."""
        target = Path(path).resolve()
        kind = "AUX" if target.suffix.lower() == ".aux" else "CSV"
        chosen = [f.name for f in select_fields(gtype, fields)] if fields else []
        names = list(dict.fromkeys([*gtype.schema.key_names, *chosen])) if fields else ["ALL"]
        everything = where is None or where == ""
        self._pw.run(
            SaveData(target, kind, gtype, names, [], "" if everything else where, [], False, append)
        )
        return target
