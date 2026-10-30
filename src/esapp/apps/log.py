"""Simulator's message log: ``pw.log``."""

from __future__ import annotations

from esapp.apps._base import Namespace

__all__ = ["Log"]


class Log(Namespace):
    """Simulator's message log: ``pw.log``."""

    __slots__ = ()

    def add(self, message: str) -> None:
        """Write ``message`` to the log."""
        self._pw.run("LogAdd", message, mutates=False)

    def clear(self) -> None:
        """Empty the log."""
        self._pw.run("LogClear", mutates=False)

    def text(self) -> str:
        """The whole log."""
        path = self._pw.scratch / "log.txt"
        self._pw.run("LogSave", path, False, mutates=False)
        return path.read_text(errors="replace")
