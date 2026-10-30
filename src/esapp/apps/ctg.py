"""Contingency analysis: ``pw.ctg``."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass

import pandas as pd

import esapp.ctg
from esapp.apps._base import SCRATCH, Namespace
from esapp.commands._catalog import (
    CTGAutoInsert,
    CTGClearAllResults,
    CTGRestoreReference,
    CTGSolve,
    CTGSolveAll,
    Delete,
)
from esapp.ctg import Action, target
from esapp.grid import Contingency, ContingencyElement, ViolationCTG
from esapp.simauto._script import is_yes

__all__ = ["STATUS", "Contingencies", "CtgResults"]

_SUMMARY = [
    Contingency.CTGSolved,
    Contingency.CTGSkip,
    Contingency.CTGViol,
    Contingency.CTGNBranchViol,
    Contingency.CTGNVoltViol,
    Contingency.CTGNInterfaceViol,
    Contingency.CTGViolMaxLine,
    Contingency.CTGViolMaxInterface,
    Contingency.CTGViolMinVolt,
    Contingency.CTGViolMaxVolt,
    Contingency.CTGNItr,
]
_VIOLATIONS = [
    ViolationCTG.LimViolID,
    ViolationCTG.LimViolCat,
    ViolationCTG.LimViolValue,
    ViolationCTG.LimViolLimit,
    ViolationCTG.LimViolPct,
]
STATUS = pd.CategoricalDtype(["YES", "PARTIAL", "RESERVE_LIMITS", "ABORTED", "NO"])
"""How a contingency solved, in Simulator's words (CTGSolved): YES; PARTIAL, some island didn't
solve; RESERVE_LIMITS, not enough generation to make up the loss; ABORTED, stopped by an Abort
action; NO. NaN if it hasn't been solved."""


@dataclass(frozen=True, slots=True)
class CtgResults:
    """Contingency results, in PowerWorld's fields and units."""

    summary: pd.DataFrame
    """One row per contingency, indexed by CTGLabel: how it solved (CTGSolved, a
    :data:`STATUS`), whether it's skipped (CTGSkip), violation counts and worst values."""
    violations: pd.DataFrame
    """One row per violated limit: element, category, value, limit and percent."""

    @property
    def solved(self) -> pd.Series:
        """Whether each contingency solved fully, indexed by CTGLabel."""
        return (self.summary["CTGSolved"] == "YES").rename("solved")

    @property
    def unsolved(self) -> list[str]:
        """Contingencies that were tried but didn't solve, or were aborted. Skipped ones aren't
        listed, and PARTIAL or RESERVE_LIMITS count as solved."""
        s = self.summary
        failed = s["CTGSolved"].isin(["NO", "ABORTED"]) & ~s["CTGSkip"]
        return [str(label) for label in s.index[failed]]


class Contingencies(Namespace):
    """Contingency analysis: ``pw.ctg``.

    ::

        pw.ctg.auto_insert()               # N-1 of every line and transformer
        results = pw.ctg.solve()
        results.violations.sort_values("LimViolPct", ascending=False)

        n2 = esapp.ctg.Contingency("N-2", Branch(1, 5, "1"), Branch(1, 5, "2"))
        pw.ctg.solve(n2)                   # solves it without changing the case
    """

    __slots__ = ()

    def auto_insert(self) -> None:
        """Add contingencies using the case's auto-insert options (by default, every branch)."""
        self._pw.run(CTGAutoInsert())

    def define(self, *ctgs: esapp.ctg.Contingency) -> list[Contingency]:
        """Save ``ctgs`` in the case under their names, replacing any with the same name.

        Raises ValueError, and saves none of them, if an action names an object the case
        doesn't have.
        """
        names = [c.name for c in ctgs]
        if len(set(names)) != len(names):
            raise ValueError(f"contingency names repeat: {sorted(names)}")
        self._write(ctgs, names)
        return [Contingency(name) for name in names]

    def contingencies(self) -> list[esapp.ctg.Contingency]:
        """The case's contingencies as ``esapp.ctg.Contingency`` objects, in Simulator's order."""
        pw = self._pw
        elements = pw[ContingencyElement]  # all five fields are keys
        actions: dict[str, list[Action]] = {}
        for name, text, verb, delay in zip(
            elements["CTGLabel"].astype(str),
            elements["Object"].astype(str),
            elements["Action"].astype(str),
            elements["TimeDelay"].astype(float).tolist(),
            strict=True,
        ):
            actions.setdefault(name, []).append(Action(target(text), verb, delay))
        return [
            esapp.ctg.Contingency(str(name), *actions.get(str(name), []), skip=is_yes(flag))
            for name, flag in pw[Contingency.CTGSkip].items()
            if not str(name).startswith(SCRATCH)
        ]

    def solve(
        self, ctg: str | esapp.ctg.Contingency | None = None, *, distributed: bool = False
    ) -> CtgResults:
        """Solve every contingency not marked to skip, one by name, or a ``Contingency`` object,
        and return the results. Each starts from the reference state, which is restored after.

        A ``Contingency`` object is solved without being saved in the case, and its results use its
        name. To stay in the post-contingency state, run ``CTGSolve(name)`` from
        :mod:`esapp.commands` instead.
        """
        match ctg:
            case None:
                self._pw.run(CTGSolveAll(distributed, True))
                return self.results()
            case str():
                self._pw.run(CTGSolve(ctg), CTGRestoreReference())  # CTGSolve leaves it applied
                return self.results(ctg)
            case esapp.ctg.Contingency():
                with self._scratch(ctg) as held:
                    results = self.solve(held)
                return _named(results, held, ctg.name)

    def results(self, name: str | None = None) -> CtgResults:
        """The results Simulator holds, for every contingency or just ``name``."""
        summary = self._pw[Contingency, _SUMMARY].set_index("CTGLabel")
        status = summary["CTGSolved"].astype(str).str.strip()
        summary["CTGSolved"] = status.where(status.isin(STATUS.categories)).astype(STATUS)
        summary["CTGSkip"] = summary["CTGSkip"].map(is_yes)
        violations = self._pw[ViolationCTG, _VIOLATIONS]
        if name is not None:
            summary = summary.loc[summary.index == name]
            violations = violations[violations["CTGLabel"] == name]
        return CtgResults(summary, violations.reset_index(drop=True))

    def clear(self) -> None:
        """Delete every contingency's results."""
        self._pw.run(CTGClearAllResults())

    # ------------------------------------------------------------------ writing
    def _write(self, ctgs: tuple[esapp.ctg.Contingency, ...], names: list[str]) -> None:
        pw = self._pw
        self._delete(names)
        skip = ["YES" if c.skip else "NO" for c in ctgs]
        pw.create(
            Contingency, pd.DataFrame({Contingency.CTGLabel: names, Contingency.CTGSkip: skip})
        )
        steps = [(name, a) for c, name in zip(ctgs, names, strict=True) for a in c.actions]
        if not steps:
            return
        pw.create(
            ContingencyElement,
            pd.DataFrame(
                {
                    ContingencyElement.CTGLabel: [name for name, _ in steps],
                    ContingencyElement.Object: [a.object for _, a in steps],
                    ContingencyElement.Action: [a.verb for _, a in steps],
                    ContingencyElement.FilterName: "",
                    ContingencyElement.TimeDelay: [a.delay for _, a in steps],
                }
            ),
        )
        written = pw[ContingencyElement, ContingencyElement.WhoAmI]
        mine = written[written["CTGLabel"].isin(names)]["WhoAmI"].astype(str)
        if unlinked := [w for w in mine if w.startswith("Unlinked")]:
            self._delete(names)
            raise ValueError(f"Simulator found no object for {unlinked}")

    def _delete(self, names: list[str]) -> None:
        # An inline filter deletes just that contingency, and its elements with it.
        if names:
            self._pw.run(*(Delete(Contingency, f"CTGLabel = '{name}'") for name in names))

    @contextmanager
    def _scratch(self, ctg: esapp.ctg.Contingency) -> Generator[str]:
        held = f"{SCRATCH}{ctg.name}"
        try:
            self._write((ctg,), [held])
            yield held
        finally:
            self._delete([held])


def _named(results: CtgResults, held: str, name: str) -> CtgResults:
    summary = results.summary.rename(index={held: name})
    violations = results.violations.replace({"CTGLabel": {held: name}})
    return CtgResults(summary, violations)
