"""Transient stability against a live SimAuto session (needs ESAPP_TEST_CASE set).

The case needs dynamic models. Besides exercising pw.ts, several tests pin a SimAuto
behavior esapp.ts is built around, so a change in Simulator shows up here first.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from esapp import PowerWorld, ResultsUnavailable, SimulatorFault
from esapp.commands import TSGetResults
from esapp.grid import Branch, Bus, Gen, Load, Transient_Options, TSContingency, TSStats_Bus
from esapp.schema import GObject, Role
from esapp.testing import powerworld
from esapp.ts import (
    TS,
    Contingency,
    Cycles,
    PlayIn,
    Result,
    Store,
    change_load,
    clear_fault,
    close,
    fault,
    trip,
)
from esapp.ts._plan import resolve
from esapp.ts._recordable import _GROUPS  # pyright: ignore[reportPrivateUsage]
from esapp.ts._transfer import _pull, fetch, memory  # pyright: ignore[reportPrivateUsage]


def first_in_service(pw: PowerWorld, gtype: type[GObject]) -> GObject:
    status = {"Gen": "GenStatus", "Load": "LoadStatus", "Branch": "LineStatus"}.get(gtype.__name__)
    refs = pw.refs(gtype)
    if status is None:
        return refs[0]
    closed = pw[gtype, status][status].astype(str).str.strip() == "Closed"
    return refs[int(np.flatnonzero(closed.to_numpy())[0])]


def _exported(path: Path) -> np.ndarray:
    """TSGetResults' rows for one contingency: time, then a column per object/field. They
    follow an ObjectFields...END header and a ``Results,"<contingency>"`` line, up to END."""
    lines = path.read_text(errors="replace").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("Results")) + 1
    end = lines.index("END", start)
    return np.array([line.rstrip(",").split(",") for line in lines[start:end]], dtype=np.float64)


def bus_fault(pw: PowerWorld, name: str = "esapp test fault") -> tuple[Bus, Contingency]:
    bus = pw.refs(Bus)[0]
    return bus, Contingency(name, fault(bus, at=0.5), clear_fault(bus, at=0.55), stop=1.5)


@powerworld(isolation="fresh")
class TestSolve:
    def test_fault(self, pw: PowerWorld):
        bus, ctg = bus_fault(pw)
        before = pw[TSContingency]
        sim = pw.ts.solve(ctg, record=[TS.Bus.VPU, TS.Gen.W])
        assert sim.solved, sim.summary
        assert sim.values.dtype == np.float32
        assert sim[TS.Bus.VPU].shape[1] == len(pw.refs(Bus))
        # An event instant appears twice: the state just before the fault, then just after.
        v = sim[bus, TS.Bus.VPU].to_numpy()
        [pre, post] = np.flatnonzero(sim.time == 0.5)
        assert v[pre] > 0.5
        assert v[post] < 0.1
        assert sim.at(0.5)[("Bus", "TSBusVPU", bus.key)] == v[post]
        # The scratch contingency, its elements and its events are gone.
        pd.testing.assert_frame_equal(pw[TSContingency], before)

    def test_flags_and_options_are_restored(self, pw: PowerWorld):
        flags = [f for f in Gen.schema.attributes if f.name.startswith("TSSave")]
        storage = [
            Transient_Options.TSStoreResultsInRAM,
            Transient_Options.TSOSaveResultsTimeStepsPerSave,
        ]
        gens, options = pw[Gen, flags], pw[Transient_Options, storage]
        pw.ts.solve(bus_fault(pw)[1], record=[(first_in_service(pw, Gen), TS.Gen.P)])
        pd.testing.assert_frame_equal(pw[Gen, flags], gens)
        pd.testing.assert_frame_equal(pw[Transient_Options, storage], options)

    def test_every_decimates_inside_simulator(self, pw: PowerWorld):
        _, ctg = bus_fault(pw)
        full = pw.ts.solve(ctg, record=[TS.Bus.VPU])
        sparse = pw.ts.solve(ctg, record=[TS.Bus.VPU], every=4)
        assert len(sparse.time) < len(full.time) / 3

    def test_case_contingency_simulates_as_a_copy(self, pw: PowerWorld):
        names = [n for n in pw[TSContingency]["TSCTGName"] if not n.startswith("esapp~")]
        if not names:
            pytest.skip("the case has no transient contingencies")
        before = pw[TSContingency]
        sim = pw.ts.solve(names[0], record=[TS.Gen.W])
        assert sim.name == names[0]
        assert sim.solved
        pd.testing.assert_frame_equal(pw[TSContingency], before)

    def test_contingencies_are_values(self, pw: PowerWorld):
        _, ctg = bus_fault(pw, "esapp round trip")
        pw.ts.define(ctg)
        [read] = [c for c in pw.ts.contingencies() if c.name == ctg.name]
        assert [(e.target, e.at, e.action) for e in read.events] == [
            (e.target, e.at, e.action) for e in ctg.events
        ]

    def test_stats_without_time_series(self, pw: PowerWorld):
        _, ctg = bus_fault(pw)
        sim = pw.ts.solve(ctg, record=[], stats=[TSStats_Bus.TSBusMinMaxVoltPU])
        assert sim.values.shape == (0, 0)
        assert len(sim.stats[TSStats_Bus.TSBusMinMaxVoltPU]) == len(pw.refs(Bus))


@powerworld(isolation="fresh")
class TestTransfer:
    def test_chunking_changes_nothing(self, pw: PowerWorld):
        _, ctg = bus_fault(pw, "esapp chunks")
        record = [TS.Bus.VPU, (Gen, [TS.Gen.W, TS.Gen.P])]
        pw.ts.solve(ctg, record=record, keep=True)
        plan = resolve(pw, record)
        whole = fetch(pw.simauto, ctg.name, plan, memory(), cells=10**8)
        sliced = fetch(pw.simauto, ctg.name, plan, memory(), cells=4 * len(whole.time))
        np.testing.assert_array_equal(sliced.values, whole.values)

    def test_matches_simulators_own_export(self, pw: PowerWorld, tmp_path: Path):
        """What esapp transfers is what Simulator writes with TSGetResults."""
        bus, ctg = bus_fault(pw, "esapp export")
        sim = pw.ts.solve(ctg, record=[(bus, TS.Bus.VPU)], keep=True)
        path = tmp_path / "export.csv"
        pw.run(TSGetResults(path, "SINGLE", [ctg.name], [f"{bus.who} | TSBusVPU"]))
        exported = _exported(path)
        np.testing.assert_allclose(exported[:, 0], sim.time, atol=1e-6)
        values = sim[bus, TS.Bus.VPU].to_numpy()
        np.testing.assert_allclose(exported[:, 1], values, rtol=1e-6, atol=1e-10)  # 10 decimals

    def test_values_are_single_precision(self, pw: PowerWorld):
        _, ctg = bus_fault(pw, "esapp precision")
        pw.ts.solve(ctg, record=[TS.Bus.VPU], keep=True)
        doubles = fetch(pw.simauto, ctg.name, resolve(pw, [TS.Bus.VPU]), memory(np.float64))
        singles = doubles.values.astype(np.float32).astype(np.float64)
        np.testing.assert_allclose(singles, doubles.values, rtol=1e-14)

    def test_every_catalog_field_records_with_its_flag(self, pw: PowerWorld):
        """The [ts.save] pairings: each non-indexed field comes back when its flag alone is on."""
        _, ctg = bus_fault(pw, "esapp pairing")
        ctg = Contingency(ctg.name, *ctg.events, stop=0.6)
        missing: list[str] = []
        for name, group in _GROUPS.items():
            gtype = group.gtype()
            if gtype.schema.keys and not pw.refs(gtype):
                continue
            ref = first_in_service(pw, gtype)
            fields = [f for f in group.fields() if not f.role & Role.INDEXED]
            with pw.ts.recording([(ref, fields)]):
                pw.ts.define(ctg)
                pw.run("TSSolve", ctg.name)
                missing += [
                    f"{name}.{f.name}"
                    for f in fields
                    if _pull(pw.simauto, ctg.name, [(ref, f)], start=None, stop=None) is None
                ]
        assert missing == []


@powerworld(isolation="fresh")
class TestManySimulations:
    def test_sweep_to_disk(self, pw: PowerWorld, tmp_path: Path):
        gens = pw.refs(Gen)[:2]
        trips = [Contingency(f"trip {i}", trip(g, at=0.2), stop=1.0) for i, g in enumerate(gens)]
        store = pw.ts.sweep(trips, record=[TS.Gen.W], every=2).save(tmp_path)
        assert list(store) == ["trip 0", "trip 1"]
        reopened = Store(tmp_path)["trip 1"]
        assert isinstance(reopened.values, np.memmap)
        assert reopened.solved
        assert not [n for n in pw[TSContingency]["TSCTGName"] if n.startswith("esapp~")]
        del store, reopened

    def test_step(self, pw: PowerWorld):
        bus, ctg = bus_fault(pw)
        with pw.ts.step(ctg, record=[TS.Bus.VPU]) as sim:
            sim.until(0.4)
            before = pw[Bus.TSBusVPU].loc[bus.key[0]]
            sim.until(0.52)
            during = pw[Bus.TSBusVPU].loc[bus.key[0]]
            result: Result = sim.result()
        assert during < 0.1 < before
        assert result.time[-1] == pytest.approx(0.52, abs=1e-3)

    def test_events_join_a_paused_simulation(self, pw: PowerWorld):
        gen = first_in_service(pw, Gen)
        quiet = Contingency("esapp quiet", stop=1.0)
        with pw.ts.step(quiet, record=[(gen, TS.Gen.P)]) as sim:
            sim.until(0.4)
            sim.apply(trip(gen, at=0.5))
            sim.until(1.0)
            result = sim.result()
        p = result[gen, TS.Gen.P].to_numpy()
        assert p[result.time < 0.5].min() > 0  # running until the trip...
        assert np.all(p[result.time > 0.5] == 0)  # ...and off after it

    def test_every_event_builder(self, pw: PowerWorld):
        bus, gen = pw.refs(Bus)[0], first_in_service(pw, Gen)
        branch, load = first_in_service(pw, Branch), first_in_service(pw, Load)
        events = [
            fault(bus, at=0.1),
            clear_fault(bus, at=0.15),
            trip(branch, at=0.2),
            close(branch, at=0.4),
            change_load(load, by=-10.0, at=0.5),
            trip(gen, at=0.6),
        ]
        sim = pw.ts.solve(Contingency("esapp events", *events, stop=1.0), record=[TS.Bus.VPU])
        assert sim.solved, sim.summary
        assert len(sim.events) >= len(events)


@powerworld(isolation="fresh")
def test_playin_round_trip(pw: PowerWorld):
    signals = pd.DataFrame({"a": [1.0, 1.5, 1.0], "b": [0.0, -1.0, 2.0]}, index=[0.0, 0.5, 1.0])
    pw.ts.define(PlayIn("esapp play-in", signals, scale={"b": 2.0}, delay=0.1))
    [back] = [p for p in pw.ts.playins() if p.name == "esapp play-in"]
    assert back.names == ["a", "b"]
    assert back.scale == {"a": 1.0, "b": 2.0}
    assert back.delay == pytest.approx(0.1)
    np.testing.assert_allclose(back.signals.to_numpy(), signals.to_numpy())
    np.testing.assert_allclose(back.signals.index.to_numpy(), signals.index.to_numpy())


@powerworld(isolation="fresh")
class TestSimulatorContract:
    """Simulator behaviors esapp.ts works around; a failure here means one has changed."""

    def test_a_refused_simulation_is_silent(self, pw: PowerWorld):
        """A step too coarse for the models: TSSolve raises nothing and stores nothing."""
        _, ctg = bus_fault(pw, "esapp coarse")
        with pw.ts.recording([TS.Bus.VPU]) as plan:
            pw.ts.define(ctg)
            pw.run("TSSolve", ctg.name, [0.0, 1.0, 2.0, True])  # 2-cycle steps
            with pytest.raises(ResultsUnavailable):
                fetch(pw.simauto, ctg.name, plan, memory())
        # ...which a result reports as unsolved, with no rows, instead of raising.
        coarse = Contingency(ctg.name, *ctg.events, stop=1.0, step=Cycles(2.0))
        sim = pw.ts.solve(coarse, record=[TS.Bus.VPU])
        assert not sim.solved
        assert sim.values.shape == (0, len(pw.refs(Bus)))

    @pytest.mark.xfail(
        raises=SimulatorFault, strict=True, reason="access violation in Simulator 24"
    )
    def test_clear_results_from_ram(self, pw: PowerWorld):
        _, ctg = bus_fault(pw, "esapp clear")
        pw.ts.solve(ctg, record=[TS.Bus.VPU], keep=True)
        pw.run("TSClearResultsFromRAM", ctg.name)
