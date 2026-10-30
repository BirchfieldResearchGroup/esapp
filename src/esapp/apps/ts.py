"""Transient stability: ``pw.ts``."""

from __future__ import annotations

import math
from collections.abc import Callable, Generator, Iterable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from functools import partial
from itertools import count
from typing import TYPE_CHECKING, Any, overload

import numpy as np
import numpy.typing as npt
import pandas as pd

from esapp.apps._base import SCRATCH, Namespace
from esapp.errors import ObjectNotFound, ResultsUnavailable
from esapp.grid import PlayIn as PlayInObject
from esapp.grid import (
    PlayInInfo,
    PlayInSignal,
    Transient_Options,
    TSContingency,
    TSContingencyElement,
    TSResultEvent,
)
from esapp.schema import Field, GObject, Role
from esapp.simauto._script import Kw, StrPath, is_yes
from esapp.ts._events import Contingency, Cycles, Event
from esapp.ts._plan import Plan, Watch, resolve
from esapp.ts._playin import PlayIn, signal_field
from esapp.ts._recordable import recordable
from esapp.ts._result import Result
from esapp.ts._store import Store, Writer
from esapp.ts._transfer import Allocate, memory
from esapp.ts._transfer import fetch as fetch_buffers

__all__ = ["ContingencyLike", "Stepper", "Sweep", "Transient"]

if TYPE_CHECKING:
    from esapp._session import PowerWorld

type ContingencyLike = Contingency | TSContingency | str
"""A contingency value, or one in the case by reference or name."""

_SUMMARY = [
    TSContingency.CTGSolved,
    TSContingency.ReasonNotSolved,
    TSContingency.CTGViol,
    TSContingency.TSGenMinMaxAngle,
    TSContingency.TSGenMinMaxAngleTime,
    TSContingency.TSTotalGenMWTripped,
    TSContingency.TSTotalGenMWIslanded,
    TSContingency.TSTotalLoadMWTripped,
    TSContingency.TSTotalLoadMWIslanded,
    TSContingency.TSTotalLoadModelMWTripped,
    TSContingency.TSTotalLoadRelayMWTripped,
    TSContingency.TSTimeInSeconds,
]
_EVENTS = [
    TSResultEvent.TSTimeInSeconds,
    TSResultEvent.WhoAmI,
    TSResultEvent.TSEventString,
    TSResultEvent.TSResultEventLevel,
    TSResultEvent.TSModelClass,
    TSResultEvent.TSModelType,
]
_SETTINGS = [  # copied with a contingency from the case
    TSContingency.StartTime,
    TSContingency.EndTime,
    TSContingency.TimeStep,
    TSContingency.UseCyclesForTimeStep,
    TSContingency.Category,
    TSContingency.CTGLabel,
    TSContingency.Include,
]
_ELEMENT = [
    TSContingencyElement.Enabled,
    TSContingencyElement.FilterName,
    TSContingencyElement.Include,
    TSContingencyElement.Comment,
]


@dataclass(frozen=True, slots=True)
class _Transfer:
    window: tuple[float, float] | None
    stats: tuple[Field[Any], ...]


class Transient(Namespace):
    """Transient stability: ``pw.ts``.

    ::

        sim = pw.ts.solve(ctg, record=[TS.Gen.W, TS.Bus.VPU])     # one simulation, in memory
        for sim in pw.ts.sweep(ctgs, record=[TS.Gen.W]): ...      # many, one at a time
        store = pw.ts.sweep(ctgs, record=[TS.Gen.W]).save("sims/")  # many, to disk
    """

    __slots__ = ("_names",)

    def __init__(self, pw: PowerWorld) -> None:
        super().__init__(pw)
        self._names: Iterator[int] | None = None

    # ------------------------------------------------------------------ simulations
    def solve(
        self,
        ctg: ContingencyLike,
        *,
        record: Iterable[Watch],
        every: int = 1,
        window: tuple[float, float] | None = None,
        stats: Iterable[Field[Any]] = (),
        dtype: npt.DTypeLike = np.float32,
        keep: bool = False,
    ) -> Result:
        """Simulate ``ctg`` and copy the fields in ``record`` to Python.

        ``every``: keep every n-th time step. ``window``: copy only that time span. ``stats``:
        PowerWorld's per-object minimums and maximums to keep (fields of TSStats_Bus or
        TSStats_Gen), which need no time series.

        By default a temporary copy of ``ctg`` is simulated and then deleted, leaving the case
        unchanged. ``keep=True`` simulates ``ctg`` itself
        (saving a :class:`Contingency` in the case under its name) and leaves its results in
        Simulator, for its plots and tables or for :meth:`fetch`.

        An unstable or aborted simulation still returns a result, not an error: check
        ``sim.solved`` and ``sim.summary``. Simulator silently refuses some simulations, for
        example a time step too large for the models; those come back unsolved, with no time
        points, and ``pw.log.text()`` says why.
        """
        plan = resolve(self._pw, record)
        transfer = _Transfer(window, _stats(stats))
        with self.recording(plan, every=every):
            return self._simulate(ctg, plan, transfer, memory(dtype), keep=keep)

    def sweep(
        self,
        ctgs: Iterable[ContingencyLike],
        *,
        record: Iterable[Watch],
        every: int = 1,
        window: tuple[float, float] | None = None,
        stats: Iterable[Field[Any]] = (),
        dtype: npt.DTypeLike = np.float32,
    ) -> Sweep:
        """Many simulations, one at a time. Nothing starts until you loop over the sweep or save
        it."""
        plan = resolve(self._pw, record)
        transfer = _Transfer(window, _stats(stats))
        return Sweep(
            tuple(ctgs),
            partial(self.recording, plan, every=every),
            lambda ctg, allocate: self._simulate(ctg, plan, transfer, allocate, keep=False),
            dtype,
        )

    def fetch(
        self,
        ctg: TSContingency | str,
        *,
        record: Iterable[Watch],
        window: tuple[float, float] | None = None,
        stats: Iterable[Field[Any]] = (),
        dtype: npt.DTypeLike = np.float32,
    ) -> Result:
        """Copy results Simulator already has for ``ctg``, without simulating it again.

        For contingencies simulated with ``keep=True`` or from Simulator itself. Only the fields
        recorded at the time are available.
        """
        name = _name(ctg)
        plan = resolve(self._pw, record)
        return self._collect(name, name, plan, _Transfer(window, _stats(stats)), memory(dtype))

    @contextmanager
    def step(
        self,
        ctg: ContingencyLike,
        *,
        record: Iterable[Watch],
        every: int = 1,
        stats: Iterable[Field[Any]] = (),
        dtype: npt.DTypeLike = np.float32,
    ) -> Generator[Stepper]:
        """Simulate ``ctg`` in stretches, so you can read or change the case in between.

        ::

            with pw.ts.step(ctg, record=[TS.Bus.VPU]) as sim:
                for t in sim.every(0.1, until=5.0):
                    v = pw[Bus.TSBusVPU]          # the state at t
                result = sim.result()
        """
        pw = self._pw
        plan = resolve(pw, record)
        transfer = _Transfer(None, _stats(stats))
        with self.recording(plan, every=every), self._scratch(ctg) as held:
            step = pw[
                TSContingency(held), [TSContingency.TimeStep, TSContingency.UseCyclesForTimeStep]
            ]
            pw.run("TSInitialize")
            yield Stepper(
                _name(ctg),
                partial(self._advance, held),
                partial(self._collect, held, _name(ctg), plan, transfer, memory(dtype)),
                partial(self._add_events, held),
                [float(step["TimeStep"].iloc[0]), is_yes(step["UseCyclesForTimeStep"].iloc[0])],
            )

    @contextmanager
    def recording(self, record: Plan | Iterable[Watch], *, every: int = 1) -> Generator[Plan]:
        """Inside the block, Simulator records only ``record``, keeping every ``every``-th time
        step. The settings are restored when the block ends."""
        if every < 1:
            raise ValueError(f"every must be a positive number of time steps, not {every!r}")
        pw = self._pw
        plan = record if isinstance(record, Plan) else resolve(pw, record)
        saved = {gtype: pw[gtype, _flag_fields(gtype)] for gtype in recordable()}
        storage: dict[Field[Any], object] = {
            Transient_Options.TSStoreResultsInRAM: True,
            Transient_Options.TSOSaveResultsTimeStepsPerSave: every,
            Transient_Options.TSOSaveResultsForOpenDevices: True,  # real values, not zeros
        }
        with ExitStack() as scope:
            scope.enter_context(pw.options(storage))  # a read-only session stops here
            pw.run("TSResultStorageSetAll", Kw.ALL, False)  # every flag of every type off
            scope.callback(_restore, pw, saved)
            for (gtype, flag), refs in plan.flags().items():
                if refs is None:
                    pw[gtype, flag] = True
                else:
                    keys = pd.DataFrame([r.key for r in refs], columns=gtype.schema.key_names)
                    pw[gtype] = keys.assign(**{flag.name: True})
            yield plan

    # ------------------------------------------------------------------ the case's contingencies
    def contingencies(self) -> list[Contingency]:
        """The case's transient contingencies as ``Contingency`` objects (without per-event
        options)."""
        pw = self._pw
        settings = pw[TSContingency, _SETTINGS[:4]]
        elements = pw[TSContingencyElement]
        out: list[Contingency] = []
        for row in settings.to_dict("records"):
            name = str(row["TSCTGName"])
            if name.startswith(SCRATCH):
                continue
            mine = elements[elements["TSCTGName"] == name]
            events = [
                Event(GObject.parse(str(w)), float(t), str(e).strip())
                for t, w, e in zip(
                    mine["TSTimeInSeconds"], mine["WhoAmI"], mine["TSEventString"], strict=True
                )
            ]
            step = float(row["TimeStep"])
            out.append(
                Contingency(
                    name,
                    *events,
                    stop=float(row["EndTime"]),
                    start=float(row["StartTime"]),
                    step=Cycles(step) if is_yes(row["UseCyclesForTimeStep"]) else step,
                )
            )
        return out

    @overload
    def define(self, value: Contingency, /) -> TSContingency: ...
    @overload
    def define(self, value: PlayIn, /) -> PlayInObject: ...
    def define(self, value: Contingency | PlayIn, /) -> TSContingency | PlayInObject:
        """Save a contingency or play-in in the case under its name, replacing any with the
        same name."""
        if isinstance(value, PlayIn):
            return self._define_playin(value)
        self._delete(value.name)
        self._write(value, value.name)
        return TSContingency(value.name)

    def playins(self) -> list[PlayIn]:
        """The case's play-ins as ``PlayIn`` objects."""
        pw = self._pw
        info = pw[PlayInInfo, [PlayInInfo.TSInfoName, PlayInInfo.TSScale, PlayInInfo.TSOffSet]]
        delays = pw[PlayInObject.TSTimeOffSet]
        out: list[PlayIn] = []
        for name, delay in delays.items():
            mine = info[info["TSName"] == name].sort_values("TSSignalIndex")
            columns = [signal_field(int(k)) for k in mine["TSSignalIndex"]]
            signals = pw[PlayInSignal, columns]
            signals = signals[signals["TSName"] == name].set_index("TSTime")[columns]
            names = [
                str(n) or f"signal {k}"
                for n, k in zip(mine["TSInfoName"], mine["TSSignalIndex"], strict=True)
            ]
            signals.columns = pd.Index(names)
            out.append(
                PlayIn(
                    str(name),
                    signals.rename_axis("time").sort_index(),
                    scale=dict(zip(names, mine["TSScale"].astype(float), strict=True)),
                    offset=dict(zip(names, mine["TSOffSet"].astype(float), strict=True)),
                    delay=float(delay),
                )
            )
        return out

    def _define_playin(self, playin: PlayIn) -> PlayInObject:
        pw = self._pw
        name = playin.name
        for gtype in (PlayInSignal, PlayInInfo, PlayInObject):
            pw.run("Delete", gtype, f"TSName = '{name}'")
        pw.create(PlayInObject, pd.DataFrame({"TSName": [name], "TSTimeOffSet": [playin.delay]}))
        names = playin.names
        pw.create(
            PlayInInfo,
            pd.DataFrame(
                {
                    "TSName": name,
                    "TSSignalIndex": range(1, len(names) + 1),
                    "TSInfoName": names,
                    "TSScale": [playin.scale.get(n, 1.0) for n in names],
                    "TSOffSet": [playin.offset.get(n, 0.0) for n in names],
                }
            ),
        )
        signals = playin.signals.set_axis(
            [signal_field(k) for k in range(1, len(names) + 1)], axis=1
        )
        signals = signals.rename_axis("TSTime").reset_index().assign(TSName=name)
        pw.create(PlayInSignal, signals)
        return PlayInObject(name)

    # ------------------------------------------------------------------ one simulation
    def _simulate(
        self,
        ctg: ContingencyLike,
        plan: Plan,
        transfer: _Transfer,
        allocate: Allocate,
        *,
        keep: bool,
    ) -> Result:
        with ExitStack() as scope:
            if not keep:
                held = scope.enter_context(self._scratch(ctg))
            elif isinstance(ctg, Contingency):
                held = str(self.define(ctg).key[0])
            else:
                held = _name(ctg)
            self._pw.run("TSSolve", held)  # times and step come from the contingency itself
            return self._collect(held, _name(ctg), plan, transfer, allocate)

    def _collect(
        self, held: str, name: str, plan: Plan, transfer: _Transfer, allocate: Allocate
    ) -> Result:
        """Transfer what Simulator holds for ``held`` into a result named ``name``."""
        pw = self._pw
        summary = pw[TSContingency(held), _SUMMARY].iloc[0].drop("TSCTGName").rename(name)
        try:
            buffers = fetch_buffers(pw.simauto, held, plan, allocate, *(transfer.window or ()))
        except ResultsUnavailable:
            if is_yes(summary["CTGSolved"]):
                raise
            buffers = allocate(0, plan.width)  # stopped before it stored anything
        for array in buffers:
            array.flags.writeable = False
        return Result(
            name,
            buffers.time,
            buffers.values,
            plan,
            summary,
            self._events(held),
            self._extrema(held, transfer.stats),
        )

    def _advance(self, held: str, args: list[object]) -> None:
        self._pw.run("TSRunUntilSpecifiedTime", held, args)

    def _events(self, held: str) -> pd.DataFrame:
        events = self._pw[TSResultEvent, _EVENTS]
        mine = events["TSCTGName"].astype(str).str.strip() == held
        return events.loc[mine].drop(columns="TSCTGName").reset_index(drop=True)

    def _extrema(self, held: str, fields: tuple[Field[Any], ...]) -> dict[Field[Any], pd.Series]:
        out: dict[Field[Any], pd.Series] = {}
        for owner in dict.fromkeys(f.owner for f in fields):
            mine = [f for f in fields if f.owner is owner]
            table = self._pw[owner, mine]
            table = table[table["TSCTGName"].astype(str).str.strip() == held]
            keys = [k for k in owner.schema.key_names if k != "TSCTGName"]
            table = table.drop(columns="TSCTGName").set_index(keys)
            out.update({f: table[f.name] for f in mine})
        return out

    # ------------------------------------------------------------------ scratch contingencies
    @contextmanager
    def _scratch(self, ctg: ContingencyLike) -> Generator[str]:
        """A throwaway copy of ``ctg`` in the case; deleting it frees its results in Simulator."""
        name = self._scratch_name()
        try:
            if isinstance(ctg, Contingency):
                self._write(ctg, name)
            else:
                self._copy(_name(ctg), name)
            yield name
        finally:
            self._delete(name)

    def _scratch_name(self) -> str:
        if self._names is None:  # numbered, not random, so simulations repeat exactly
            used = [
                n.removeprefix(SCRATCH)
                for n in self._pw[TSContingency]["TSCTGName"].astype(str)
                if n.startswith(SCRATCH)
            ]
            self._names = count(1 + max((int(n) for n in used if n.isdigit()), default=0))
        return f"{SCRATCH}{next(self._names)}"

    def _write(self, ctg: Contingency, name: str) -> None:
        pw = self._pw
        row: dict[Field[Any], object] = {
            TSContingency.TSCTGName: name,
            TSContingency.StartTime: ctg.start,
            TSContingency.EndTime: ctg.stop,
        }
        match ctg.step:
            case Cycles(n):
                row |= {TSContingency.TimeStep: n, TSContingency.UseCyclesForTimeStep: "YES"}
            case None:
                pass
            case seconds:
                row |= {TSContingency.TimeStep: seconds, TSContingency.UseCyclesForTimeStep: "NO"}
        pw.create(TSContingency, pd.DataFrame([row]))
        self._add_events(name, ctg.events)

    def _add_events(self, name: str, events: Iterable[Event]) -> None:
        events = list(events)
        if not events:
            return
        elements = pd.DataFrame(
            {
                TSContingencyElement.TSCTGName: name,
                TSContingencyElement.TSTimeInSeconds: [e.at for e in events],
                TSContingencyElement.WhoAmI: [e.who for e in events],
                TSContingencyElement.TSEventString: [e.action for e in events],
            }
        )
        try:
            self._pw.create(TSContingencyElement, elements)
        except ObjectNotFound as err:
            err.add_note(
                "PowerWorld also says 'not found' for an event string it does not accept: "
                f"{sorted({e.action for e in events})}"
            )
            raise

    def _copy(self, source: str, name: str) -> None:
        pw = self._pw
        settings = pw[TSContingency(source), _SETTINGS]
        pw.create(TSContingency, settings.assign(TSCTGName=name))
        elements = pw[TSContingencyElement, _ELEMENT]
        mine = elements[elements["TSCTGName"] == source]
        if not mine.empty:
            pw.create(TSContingencyElement, mine.assign(TSCTGName=name))

    def _delete(self, name: str) -> None:
        # An inline filter deletes just this contingency, its elements and its result events.
        self._pw.run("Delete", TSContingency, f"TSCTGName = '{name}'")


@dataclass(frozen=True, slots=True)
class Sweep:
    """Many simulations, one at a time. Loop over it to get each result, or use :meth:`save` to
    write them to disk. Only one simulation's results are in memory at a time."""

    contingencies: tuple[ContingencyLike, ...]
    _recording: Callable[[], AbstractContextManager[Plan]]
    _simulate: Callable[[ContingencyLike, Allocate], Result]
    _dtype: npt.DTypeLike

    def __len__(self) -> int:
        return len(self.contingencies)

    def __iter__(self) -> Iterator[Result]:
        with self._recording():
            for ctg in self.contingencies:
                yield self._simulate(ctg, memory(self._dtype))

    def save(self, path: StrPath, *, resume: bool = True) -> Store:
        """Simulate every contingency and write the results straight to ``path``. With
        ``resume``, simulations already in ``path`` are skipped."""
        writer = Writer(path)
        with self._recording():
            for ctg in self.contingencies:
                if _name(ctg) in writer:
                    if resume:
                        continue
                    raise ValueError(f"{_name(ctg)!r} is already in the store at {path}")
                writer.commit(self._simulate(ctg, writer.allocate(self._dtype)))
        return Store(path)


@dataclass(slots=True)
class Stepper:
    """A simulation paused between steps. :meth:`until` moves it forward, ``pw[...]`` reads
    it, and :meth:`apply` adds events to it."""

    name: str
    _advance: Callable[[list[object]], None]
    _collect: Callable[[], Result]
    _apply: Callable[[Iterable[Event]], None]
    _first: list[object]  # TSRunUntilSpecifiedTime's step size and unit, for the first call
    t: float | None = None  # simulation time reached; None before the first step

    def apply(self, *events: Event) -> None:
        """Add events to the paused simulation, each at a time it hasn't reached yet::

            sim.until(1.0)
            sim.apply(trip(Gen(5, "1"), at=1.1))    # takes effect at 1.1 s

        The contingency saved in the case isn't changed: the simulation uses a copy of it.
        """
        if self.t is not None and (past := [e for e in events if e.at <= self.t]):
            raise ValueError(
                f"the simulation is at {self.t} s: events must come later, not at "
                f"{[e.at for e in past]}"
            )
        self._apply(events)

    def until(self, t: float) -> None:
        """Simulate up to ``t`` seconds."""
        if self.t is not None and t <= self.t:
            raise ValueError(f"the simulation is already at {self.t} s")
        self._advance([t] if self.t is not None else [t, *self._first, True])
        self.t = t

    def every(self, dt: float, *, until: float) -> Iterator[float]:
        """Simulate in steps of ``dt`` up to ``until``, yielding each time reached."""
        k = 0 if self.t is None else math.floor(self.t / dt + 1e-9)
        while (t := round((k + 1) * dt, 12)) <= until + 1e-12:
            self.until(t)
            yield t
            k += 1

    def result(self) -> Result:
        """Copy what has been recorded so far."""
        return self._collect()


def _name(ctg: ContingencyLike) -> str:
    match ctg:
        case Contingency(name=name):
            return name
        case GObject():
            return str(ctg.key[0])
        case str():
            return ctg


def _restore(pw: PowerWorld, flags: dict[type[GObject], pd.DataFrame]) -> None:
    for gtype, table in flags.items():
        if not table.empty:
            pw[gtype] = table


def _flag_fields(gtype: type[GObject]) -> list[Field[Any]]:
    """The TSSave flags a write can set (Branch lists transformer flags it cannot)."""
    return [
        f
        for f in gtype.schema.attributes
        if f.name.startswith("TSSave") and f.role & Role.ENTERABLE
    ]


def _stats(fields: Iterable[Field[Any]]) -> tuple[Field[Any], ...]:
    out = tuple(fields)
    if strays := [f for f in out if "TSCTGName" not in f.owner.schema.key_names]:
        raise ValueError(
            f"stats are per-contingency result fields (TSStats_Bus, TSStats_Gen), not {strays}"
        )
    return out
