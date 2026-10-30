"""The PowerWorld session: one SimAuto process, its open case, and the write guard."""

from __future__ import annotations

import shutil
from collections.abc import Generator, Mapping
from contextlib import ExitStack, contextmanager
from functools import cached_property
from itertools import count
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, overload

import pandas as pd

from esapp._indexing import Indexable, Where, as_where, prepare
from esapp.apps.log import Log
from esapp.commands._command import Command
from esapp.errors import ObjectNotFound, PowerWorldError, UnknownCommand
from esapp.schema import Clause, Conditions, Field, GObject, Role
from esapp.simauto import ComTransport, Kw, SimAuto, Transport, script
from esapp.simauto._script import StrPath

if TYPE_CHECKING:
    from esapp.apps.atc import ATC
    from esapp.apps.ctg import Contingencies
    from esapp.apps.fault import Faults
    from esapp.apps.files import Files
    from esapp.apps.gic import GIC
    from esapp.apps.groups import Groups
    from esapp.apps.opf import OPF
    from esapp.apps.pf import PowerFlow
    from esapp.apps.pv import PV
    from esapp.apps.qv import QV
    from esapp.apps.sens import Sensitivity
    from esapp.apps.ts import Transient


class PowerWorld(Indexable):
    """A connection to one running copy of Simulator (one SimAuto license) and its open case.

    Open it in a ``with`` block so Simulator closes when you're done::

        with PowerWorld("case.pwb") as pw:
            v = pw[Bus.BusPUVolt]

    Pass ``transport`` to use something other than COM, such as
    :class:`esapp.testing.FakeSimAuto` in tests.

    Reads::

        pw[Bus]                              # the key columns
        pw[Bus, "BusPUVolt"]                 # keys + fields: names, Fields, or a list
        pw[Bus, :]                           # keys + every field
        pw[Bus, fields, Bus.BusNomVolt > 100]  # only matching objects (see Where)
        pw[Bus.BusPUVolt]                    # one field, as a Series indexed by key
        pw[Bus(5), fields]                   # one object

    Writes::

        pw[Gen, Gen.GenMW] = 100.0           # every object
        pw[Gen, Gen.GenMW, Gen.AreaNum.eq(1)] = 50.0   # only matching objects
        pw[Gen(1, "1"), Gen.GenMW] = 75.0    # one object
        pw[Load.LoadSMW] = series            # each object its own value, matched by key
        pw[Gen] = df                         # the objects in df (use pw.create for new ones)

    Columns come back keys first, then in the order you asked for. A selection is a saved
    filter's name, ``Kw.SELECTED`` or ``Kw.AREAZONE``, one object, or conditions on fields.
    """

    def __init__(
        self,
        case: StrPath | None = None,
        *,
        visible: bool = False,
        transport: Transport | None = None,
    ) -> None:
        self.simauto = SimAuto(
            transport if transport is not None else ComTransport(visible=visible)
        )
        self.case: Path | None = None
        self._scratch: Path | None = None
        self._mode = Kw.RUN
        self._filters = count(1)
        self._learned: set[tuple[str, str]] = set()
        self._owns_transport = True
        if case is not None:
            try:
                self.open(case)
            except BaseException:
                self.simauto.transport.close()
                raise

    @classmethod
    def attach(cls, transport: Transport, case: StrPath | None = None) -> Self:
        """A session on ``transport`` whose case is already open. Closing it leaves the
        transport open, so several sessions can use one Simulator, one after another."""
        session = cls(transport=transport)
        session.case = Path(case) if case is not None else None
        session._owns_transport = False
        return session

    def __repr__(self) -> str:
        return f"PowerWorld({str(self.case) if self.case else 'no case'})"

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------ lifecycle
    def open(self, case: StrPath) -> Self:
        """Open ``case``, closing any case that's already open."""
        path = Path(case).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"case file not found: {path}")
        if self.case is not None:
            self.simauto.CloseCase()
            self.case = None
        self.simauto.OpenCase(str(path))
        self.case, self._mode = path, Kw.RUN
        return self

    def ensure_open(self, case: StrPath) -> Self:
        """Open ``case``, unless it's already open."""
        if self.case != Path(case).resolve():
            self.open(case)
        return self

    def close(self) -> None:
        """Close the case and end the Simulator process. The session can't be used again.

        A session from :meth:`attach` leaves the case and the process open.
        """
        try:
            if self.case is not None and self._owns_transport:
                self.simauto.CloseCase()
        finally:
            self.case = None
            if self._owns_transport:
                self.simauto.transport.close()
            if self._scratch is not None:
                shutil.rmtree(self._scratch, ignore_errors=True)
                self._scratch = None

    # ------------------------------------------------------------------ commands
    @overload
    def run(self, command: Command, /, *more: Command) -> None: ...
    @overload
    def run(self, command: str, /, *args: object, mutates: bool = True) -> None: ...
    def run(self, command: Command | str, /, *rest: Any, mutates: bool = True) -> None:
        """Run script commands.

        ``Command`` objects (from :mod:`esapp.commands`) are sent together in one call, run in
        order, and stop at the first error. You can also pass a command name and its arguments
        to run any command; ``mutates=False`` allows it in read-only sessions. Selections, such
        as ``Bus.AreaNum.eq(1)``, are turned into filter arguments.
        """
        batch: tuple[Command, ...] = () if isinstance(command, str) else (command, *rest)
        if batch and not all(isinstance(c, Command) for c in batch):
            raise TypeError("pw.run takes Command values, or a command name and its arguments")
        if mutates if isinstance(command, str) else any(c.mutates for c in batch):
            self._check_writable()
        with ExitStack() as scope:

            def select(where: Any) -> object:
                return scope.enter_context(self._selection(as_where(where)))

            if isinstance(command, str):
                args = [select(a) if isinstance(a, Clause | Conditions) else a for a in rest]
                text = script(command, *args)
            else:
                text = "".join(c.statement(select) for c in batch)
            try:
                self.simauto.RunScriptCommand(text)
            except PowerWorldError as err:
                if len(batch) > 1:
                    err.add_note(f"in a batch of {len(batch)}: {', '.join(c.name for c in batch)}")
                if isinstance(err, UnknownCommand) and (late := [c for c in batch if c.since]):
                    err.add_note(
                        "; ".join(
                            f"{c.name} needs a Simulator built {c.since} or later" for c in late
                        )
                    )
                raise

    # ------------------------------------------------------------------ namespaces
    # Each loads on first use, with the object types and commands it needs.
    @cached_property
    def log(self) -> Log:
        """PowerWorld's message log. See :class:`~esapp.apps.log.Log`."""
        return Log(self)

    @cached_property
    def pf(self) -> PowerFlow:
        """Power flow. See :class:`~esapp.apps.pf.PowerFlow`."""
        from esapp.apps.pf import PowerFlow

        return PowerFlow(self)

    @cached_property
    def ctg(self) -> Contingencies:
        """Contingency analysis. See :class:`~esapp.apps.ctg.Contingencies`."""
        from esapp.apps.ctg import Contingencies

        return Contingencies(self)

    @cached_property
    def groups(self) -> Groups:
        """Injection groups: sources and sinks for transfers and PV curves.
        See :class:`~esapp.apps.groups.Groups`."""
        from esapp.apps.groups import Groups

        return Groups(self)

    @cached_property
    def sens(self) -> Sensitivity:
        """Linear sensitivities: PTDFs, LODFs, shift factors and voltage sensitivities.
        See :class:`~esapp.apps.sens.Sensitivity`."""
        from esapp.apps.sens import Sensitivity

        return Sensitivity(self)

    @cached_property
    def opf(self) -> OPF:
        """Optimal power flow and its prices. See :class:`~esapp.apps.opf.OPF`."""
        from esapp.apps.opf import OPF

        return OPF(self)

    @cached_property
    def atc(self) -> ATC:
        """Available transfer capability. See :class:`~esapp.apps.atc.ATC`."""
        from esapp.apps.atc import ATC

        return ATC(self)

    @cached_property
    def pv(self) -> PV:
        """PV curves. See :class:`~esapp.apps.pv.PV`."""
        from esapp.apps.pv import PV

        return PV(self)

    @cached_property
    def qv(self) -> QV:
        """QV curves. See :class:`~esapp.apps.qv.QV`."""
        from esapp.apps.qv import QV

        return QV(self)

    @cached_property
    def gic(self) -> GIC:
        """Geomagnetically induced currents. See :class:`~esapp.apps.gic.GIC`."""
        from esapp.apps.gic import GIC

        return GIC(self)

    @cached_property
    def fault(self) -> Faults:
        """Fault analysis. See :class:`~esapp.apps.fault.Faults`."""
        from esapp.apps.fault import Faults

        return Faults(self)

    @cached_property
    def files(self) -> Files:
        """Saving the case, writing data, loading auxiliary files.
        See :class:`~esapp.apps.files.Files`."""
        from esapp.apps.files import Files

        return Files(self)

    @cached_property
    def ts(self) -> Transient:
        """Transient stability: simulate contingencies and get what they record.
        See :class:`~esapp.apps.ts.Transient`."""
        from esapp.apps.ts import Transient

        return Transient(self)

    # ------------------------------------------------------------------ objects
    def create(self, gtype: type[GObject], df: pd.DataFrame) -> None:
        """Create the objects in ``df``. It needs all of one of ``gtype``'s sets of keys, and the
        fields Simulator requires for the type; :class:`~esapp.ObjectNotFound` names any that
        are missing."""
        self._check_writable()
        rows = prepare(gtype, df)
        columns = {str(c) for c in rows.columns}
        if not any(key_set <= columns for key_set in gtype.schema.key_sets):
            accepted = " or ".join(str(sorted(ks)) for ks in gtype.schema.key_sets) or "none"
            raise ValueError(f"creating {gtype.__name__} needs a complete key set: {accepted}")
        with self.edit_mode(), self._creating():
            try:
                self.simauto.ChangeParametersMultipleElement(
                    gtype.schema.type, [str(c) for c in rows.columns], rows.to_numpy().tolist()
                )
            except ObjectNotFound as err:  # Simulator's words for a missing required field too
                required = (f.name for f in gtype.schema.fields if f.role & Role.REQUIRED)
                if missing := [name for name in required if name not in columns]:
                    err.add_note(f"{gtype.__name__} also needs {missing}")
                raise

    def delete(self, gtype: type[GObject], where: Where) -> None:
        """Delete the objects ``where`` selects; ``Kw.ALL`` deletes every object of the type."""
        self._check_writable()
        with self.edit_mode():
            self._by_selection("Delete", gtype, where)

    def select(self, gtype: type[GObject], where: Where = Kw.ALL) -> None:
        """Mark objects as selected, so ``Kw.SELECTED`` refers to them."""
        self._by_selection("SelectAll", gtype, where)

    def unselect(self, gtype: type[GObject], where: Where = Kw.ALL) -> None:
        """Clear the selected mark of the objects ``where`` selects."""
        self._by_selection("UnSelectAll", gtype, where)

    def refs[G: GObject](self, gtype: type[G], where: Where = "") -> list[G]:
        """A reference to every object of ``gtype``, or to those ``where`` selects.

        A type without keys (options, case information) has exactly one object.
        """
        if not gtype.schema.keys:
            return [gtype()]
        keys = self._read(gtype, [], as_where(where)).astype(object)  # Python scalars
        return [gtype(*row) for row in keys.itertuples(index=False, name=None)]

    def _by_selection(self, command: str, gtype: type[GObject], where: Where) -> None:
        with self._selection(as_where(where)) as selected:
            if selected is Kw.ALL:
                self.simauto.RunScriptCommand(script(command, gtype))  # no filter: every object
            else:
                self.simauto.RunScriptCommand(script(command, gtype, selected))

    # ------------------------------------------------------------------ scopes
    @contextmanager
    def options(self, values: Mapping[Field[Any], object]) -> Generator[Self]:
        """Set options (fields of objects without keys) inside the ``with`` block, and put
        them back after it::

            with pw.options({Sim_Solution_Options.MaxItr: 50}):
                pw.pf.solve()
        """
        owners: dict[type[GObject], dict[Field[Any], object]] = {}
        for f, value in values.items():
            if f.owner.schema.keys:
                raise ValueError(f"{f!r} is not an option: options are fields of keyless objects")
            owners.setdefault(f.owner, {})[f] = value
        saved = {o: self[o, list(fs)].iloc[0].tolist() for o, fs in owners.items()}
        changed: list[type[GObject]] = []
        try:
            for owner, fs in owners.items():
                self[owner, list(fs)] = list(fs.values())
                changed.append(owner)
            yield self
        finally:
            for owner in changed:
                self[owner, list(owners[owner])] = saved[owner]

    @contextmanager
    def edit_mode(self) -> Generator[Self]:
        """Switch to EDIT mode for the block, then back to the previous mode."""
        previous = self._mode
        self._enter(Kw.EDIT)
        try:
            yield self
        finally:
            self._enter(previous)

    @contextmanager
    def snapshot(self) -> Generator[Self]:
        """Save the case's state with SimAuto's SaveState, and restore it when the block ends.

        SaveState isn't a full undo. If the block changes the network itself, work on a fresh
        copy of the case.
        """
        self.simauto.SaveState()
        try:
            yield self
        finally:
            self.simauto.LoadState()

    @contextmanager
    def read_only(self) -> Generator[Self]:
        """Inside the block, writes and commands that change the case raise
        :class:`ReadOnlySessionError`."""
        previous, self._read_only = self._read_only, True
        try:
            yield self
        finally:
            self._read_only = previous

    def _enter(self, mode: Kw) -> None:
        if mode is not self._mode:
            self.run("EnterMode", mode, mutates=False)
            self._mode = mode
