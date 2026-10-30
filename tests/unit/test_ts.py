"""Transient stability over FakeSimAuto: catalog, values, plans, transfer, results, simulations."""

import math
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from esapp import (
    ObjectNotFound,
    PowerWorld,
    ReadOnlySessionError,
    ResultsUnavailable,
)
from esapp.grid import (
    Branch,
    Bus,
    Gen,
    Load,
    PWCaseInformation,
    Transient_Options,
    TSContingency,
    TSStats_Bus,
)
from esapp.grid import PlayIn as PlayInObject
from esapp.schema import GObject
from esapp.testing import FakeSimAuto
from esapp.ts import (
    TS,
    Contingency,
    Cycles,
    Event,
    Plan,
    PlayIn,
    Result,
    Store,
    bypass,
    change_load,
    clear_fault,
    close,
    exciter_setpoint,
    fault,
    flags,
    governor_setpoint,
    power_setpoint,
    trip,
)
from esapp.ts._plan import resolve
from esapp.ts._recordable import fields_of, recordable
from esapp.ts._transfer import fetch, memory

TIME = [0.0, 0.5, 1.0, 1.0, 1.5, 2.0]  # 1.0 twice: just before the event, then just after
BUSES = [Bus(1), Bus(2), Bus(3)]
GENS = [Gen(1, "1"), Gen(3, "1")]
FAULT = Contingency(
    "bus2 fault",
    clear_fault(Bus(2), at=1.05),
    fault(Bus(2), at=1.0),
    stop=2.0,
    step=Cycles(0.25),
)


def value(ref: GObject, field: str, i: int) -> float:
    """A distinct value per object, field and time step."""
    number = ref.key[0] if ref.key else 0
    return number * 10 + i + len(field) / 100


def trajectories() -> dict[tuple[GObject, str], list[float]]:
    out: dict[tuple[GObject, str], list[float]] = {}
    for refs, fields in (
        (BUSES, ("TSBusVPU", "TSBusDeg")),
        (GENS, ("TSGenW", "TSGenDelta")),
        ([PWCaseInformation()], ("TSSystemGenP",)),
    ):
        for ref in refs:
            for f in fields:
                out[ref, f] = [value(ref, f, i) for i in range(len(TIME))]
    return out


class Recorder:
    """FakeSimAuto dynamics that remember what each simulation was asked to do."""

    def __init__(self, fake: FakeSimAuto) -> None:
        self.fake = fake
        self.simulations: list[tuple[str, pd.DataFrame, list[str]]] = []

    def __call__(
        self, name: str, elements: pd.DataFrame
    ) -> tuple[Sequence[float], Mapping[tuple[GObject, str], Sequence[float]]]:
        scratch = [
            n for n in self.fake.tables["TSContingency"]["TSCTGName"] if n.startswith("esapp~")
        ]
        self.simulations.append((name, elements.copy(), scratch))
        return TIME, trajectories()


@pytest.fixture
def fake(fake_simauto: FakeSimAuto) -> FakeSimAuto:
    fake_simauto.add(Bus, {"BusNum": [1, 2, 3], "TSSaveAll": ["NO"] * 3})
    fake_simauto.add(Gen, {"BusNum": [1, 3], "GenID": ["1", "1"], "TSSaveAll": ["YES", "YES"]})
    fake_simauto.add(
        Transient_Options,
        {
            "TSStoreResultsInRAM": ["NO"],
            "TSOSaveResultsTimeStepsPerSave": [1],
            "TSOSaveResultsForOpenDevices": ["NO"],
        },
    )
    fake_simauto.dynamics = Recorder(fake_simauto)
    return fake_simauto


@pytest.fixture
def recorder(fake: FakeSimAuto) -> Recorder:
    assert isinstance(fake.dynamics, Recorder)
    return fake.dynamics


@pytest.fixture
def pw(fake: FakeSimAuto, fake_pw: PowerWorld) -> PowerWorld:
    return fake_pw


def expected(ref: GObject, field: str, rows: Sequence[int] = range(len(TIME))) -> np.ndarray:
    return np.array([value(ref, field, i) for i in rows], dtype=np.float32)


# ---------------------------------------------------------------------- catalog
class TestCatalog:
    def test_entries_are_the_schema_fields(self):
        assert TS.Gen.W is Gen.TSGenW
        assert TS.Bus.Frequency is Bus.Frequency
        assert TS.System.GenP is PWCaseInformation.TSSystemGenP

    def test_flags_record_fields(self):
        assert flags(TS.Gen.W) == (Gen.TSSaveGenW,)
        assert flags(TS.Load.P) == (Load.TSSaveBusLoadP,)  # irregular: from [ts.save]
        assert flags(TS.Gen.ExciterState[3]) == (Gen.TSSaveGenExciter,)
        assert flags(Bus.BusPUVolt) == ()

    def test_recordable_types(self):
        names = {t.__name__ for t in recordable()}
        assert {"Bus", "Gen", "Branch", "PWCaseInformation"} <= names
        assert "TSStats_Bus" not in names  # its TSResultStorageSetAll crashes Simulator 24

    def test_flags_take_bools(self):
        assert Gen.TSSaveGenW.vocab == ("YES", "NO")

    def test_fields_of_a_type(self):
        assert TS.Gen.W in fields_of(Gen)
        assert fields_of(TSContingency) == []


# ---------------------------------------------------------------------- values
class TestEvents:
    @pytest.mark.parametrize(
        ("event", "action"),  # every string verified against Simulator 24
        [
            pytest.param(fault(Bus(6), at=1.0), "FAULT 3PB SOLID", id="bus-fault"),
            pytest.param(
                fault(Bus(6), at=1.0, kind="SLG", z=0.01j), "FAULT SLG IMP 0 0.01", id="impedance"
            ),
            pytest.param(fault(Bus(6), at=1.0, y=-100j), "FAULT 3PB ADM 0 -100", id="admittance"),
            pytest.param(
                fault(Branch(1, 2, "1"), at=1.0, location=50), "FAULT 50 3PB SOLID", id="branch"
            ),
            pytest.param(
                fault(Bus(6), at=1.0, self_clear=True), "FAULT 3PB SOLID SELFCLEAR", id="self-clear"
            ),
            pytest.param(clear_fault(Bus(6), at=1.1), "CLEARFAULT", id="clear"),
            pytest.param(trip(Branch(1, 2, "1"), at=1.0), "OPEN BOTH", id="trip-branch"),
            pytest.param(trip(Branch(1, 2, "1"), at=1.0, end="near"), "OPEN NEAR", id="trip-near"),
            pytest.param(trip(Gen(2, "1"), at=1.0), "OPEN", id="trip-gen"),
            pytest.param(close(Branch(1, 2, "1"), at=1.0, end="far"), "CLOSE FAR", id="close-far"),
            pytest.param(bypass(Branch(1, 2, "1"), at=1.0), "BYPASS", id="bypass"),
            pytest.param(
                exciter_setpoint(Gen(2, "1"), 1.02, at=1.0),
                "SET Exciter_Setpoint 1.02 pu",
                id="exc",
            ),
            pytest.param(
                governor_setpoint(Gen(2, "1"), 1.05, at=1.0, ramp=2),
                "RAMP Governor_Setpoint 1.05 % 2",
                id="governor-ramp",
            ),
            pytest.param(
                power_setpoint(Gen(2, "1"), 50, "%", at=1.0), "SET Power 50 %", id="power"
            ),
            pytest.param(change_load(Load(2, "1"), -30, at=1.0), "CHANGEBY -30 %", id="load"),
        ],
    )
    def test_event_strings(self, event: Event, action: str):
        assert event.action == action

    def test_fault_location_rules(self):
        with pytest.raises(ValueError, match="needs a location"):
            fault(Branch(1, 2, "1"), at=1.0)
        with pytest.raises(ValueError, match="takes none"):
            fault(Bus(1), at=1.0, location=50)
        with pytest.raises(ValueError, match="not both"):
            fault(Bus(1), at=1.0, z=0.1j, y=1j)

    def test_who(self):
        assert Gen(2, "1").who == "Gen '2' '1'"
        assert PWCaseInformation().who == "PWCaseInformation"
        assert trip(Gen(2, "1"), at=1.0).who == "Gen '2' '1'"
        with pytest.raises(ValueError, match="quote"):
            Gen(2, "it's").who  # noqa: B018

    def test_contingency_orders_events(self):
        assert [e.action for e in FAULT.events] == ["FAULT 3PB SOLID", "CLEARFAULT"]
        assert FAULT.step == Cycles(0.25)

    @pytest.mark.parametrize(
        ("make", "message"),
        [
            pytest.param(lambda: Contingency('say "hi"', stop=1.0), "quotes", id="quote"),
            pytest.param(lambda: Contingency("x", stop=0.0), "start < stop", id="stop"),
            pytest.param(lambda: Event(Bus(1), -1.0, "OPEN"), "event times", id="negative-time"),
            pytest.param(lambda: Event(Bus(1), math.nan, "OPEN"), "event times", id="nan-time"),
        ],
    )
    def test_invalid_values(self, make, message: str):
        with pytest.raises(ValueError, match=message):
            make()


# ---------------------------------------------------------------------- plans
class TestPlan:
    def test_grammar(self, pw: PowerWorld):
        plan = resolve(
            pw,
            [
                TS.Bus.VPU,  # every bus
                (Gen, [TS.Gen.W, TS.Gen.Delta]),  # every generator, two fields
                (Bus(2), TS.Bus.Deg),  # one bus
                ([Gen(3, "1")], "TSGenP"),  # a list of objects, a field by name
                (Bus, TS.Bus.VPU, Bus.BusNum <= 2),  # through a selection (merged)
            ],
        )
        assert [(b.field.name, b.refs, b.every) for b in plan.blocks] == [
            ("TSBusVPU", tuple(BUSES), True),
            ("TSGenW", tuple(GENS), True),
            ("TSGenDelta", tuple(GENS), True),
            ("TSBusDeg", (Bus(2),), False),
            ("TSGenP", (Gen(3, "1"),), False),
        ]
        assert plan.width == 3 + 2 + 2 + 1 + 1
        assert plan.span(TS.Gen.W)[0] == slice(3, 5)
        assert plan.column(Gen(3, "1"), TS.Gen.Delta) == 6

    def test_keys_are_python_scalars(self, pw: PowerWorld):
        plan = resolve(pw, [TS.Gen.W])
        assert all(type(k) in (int, str) for ref in plan.blocks[0].refs for k in ref.key)

    def test_flags(self, pw: PowerWorld):
        plan = resolve(pw, [TS.Bus.VPU, TS.Bus.VinKV, (Gen(1, "1"), [TS.Gen.W])])
        assert plan.flags() == {
            (Bus, Bus.TSSaveBusVPU): None,
            (Gen, Gen.TSSaveGenW): (Gen(1, "1"),),
        }

    def test_every_field_expands_indexed_ones(self, pw: PowerWorld):
        names = [b.field.name for b in resolve(pw, [(Gen, slice(None))]).blocks]
        assert "TSGenW" in names
        assert "TSGenExciterState:1" in names
        assert "TSGenExciterState" not in names

    def test_keyless_type(self, pw: PowerWorld):
        plan = resolve(pw, [TS.System.GenP])
        assert plan.blocks[0].refs == (PWCaseInformation(),)

    @pytest.mark.parametrize(
        ("spec", "error"),
        [
            pytest.param(Bus.BusPUVolt, ValueError, id="not-recordable"),
            pytest.param((Gen, Gen.TSGenExciterState), ValueError, id="indexed-base"),
            pytest.param(([Bus(1), Gen(1, "1")], TS.Bus.VPU), TypeError, id="mixed-types"),
            pytest.param("TSBusVPU", TypeError, id="bare-name"),
        ],
    )
    def test_rejects(self, pw: PowerWorld, spec: object, error: type[Exception]):
        with pytest.raises(error):
            resolve(pw, [spec])  # pyright: ignore[reportArgumentType]

    def test_json_round_trip(self, pw: PowerWorld):
        plan = resolve(pw, [TS.Bus.VPU, (Gen(3, "1"), TS.Gen.W)])
        assert Plan.from_json(plan.to_json()) == plan

    def test_subset(self, pw: PowerWorld):
        plan = resolve(pw, [TS.Bus.VPU, TS.Gen.W])
        sub, columns = plan.subset([(Gen(3, "1"), TS.Gen.W), (Bus, TS.Bus.VPU)])
        assert [(b.field.name, b.refs) for b in sub.blocks] == [
            ("TSGenW", (Gen(3, "1"),)),
            ("TSBusVPU", tuple(BUSES)),
        ]
        assert columns.tolist() == [4, 0, 1, 2]
        with pytest.raises(KeyError):
            plan.subset([TS.Bus.Deg])


# ---------------------------------------------------------------------- transfer
def solved(fake: FakeSimAuto, name: str = "X") -> None:
    """Results for ``name`` as if Simulator recorded every field."""
    recorded = {
        (ref.schema.type, tuple(str(k) for k in ref.key), f): vals
        for (ref, f), vals in trajectories().items()
    }
    fake.results[name] = (TIME, recorded)


class TestTransfer:
    def test_values_and_time(self, pw: PowerWorld, fake: FakeSimAuto):
        solved(fake)
        plan = resolve(pw, [TS.Bus.VPU, TS.Gen.W, TS.System.GenP])
        out = fetch(pw.simauto, "X", plan, memory())
        assert out.time.tolist() == TIME
        assert out.values.dtype == np.float32
        assert out.values.flags.f_contiguous
        np.testing.assert_array_equal(out.values[:, 4], expected(Gen(3, "1"), "TSGenW"))
        np.testing.assert_array_equal(
            out.values[:, 5], expected(PWCaseInformation(), "TSSystemGenP")
        )

    def test_any_request_size_gives_the_same_result(self):
        fake = FakeSimAuto()
        fake.add(Bus, {"BusNum": [1, 2, 3]})
        fake.add(Gen, {"BusNum": [1, 3, 4], "GenID": ["1", "1", "9"]})  # Gen 4 has no data
        solved(fake)
        with PowerWorld(transport=fake) as pw:
            plan = resolve(pw, [TS.Gen.W, TS.Bus.VPU, (Gen, TS.Gen.Delta)])
            reference = fetch(pw.simauto, "X", plan, memory(), cells=10_000)
            for cells in range(1, 61):
                chunked = fetch(pw.simauto, "X", plan, memory(), cells=cells)
                np.testing.assert_array_equal(chunked.values, reference.values)
        # No data reads 0 wherever the field falls, never a dropped column.
        assert (reference.values[:, plan.column(Gen(4, "9"), TS.Gen.Delta)] == 0).all()

    def test_requests_stay_bounded(self, pw: PowerWorld, fake: FakeSimAuto):
        solved(fake)
        plan = resolve(pw, [TS.Bus.VPU, TS.Bus.Deg, TS.Gen.W])
        fetch(pw.simauto, "X", plan, memory(), cells=3 * len(TIME))
        sizes = [
            len(args[1].items)  # pyright: ignore[reportAttributeAccessIssue]
            for method, args in fake.calls
            if method == "TSGetContingencyResults"
        ]
        assert sizes[0] == 1  # the probe that learns the row count
        assert max(sizes) <= 3

    def test_window(self, pw: PowerWorld, fake: FakeSimAuto):
        solved(fake)
        out = fetch(pw.simauto, "X", resolve(pw, [TS.Bus.VPU]), memory(), 0.5, 1.5)
        assert out.time.tolist() == [0.5, 1.0, 1.0, 1.5]

    def test_unknown_object(self, pw: PowerWorld, fake: FakeSimAuto):
        solved(fake)
        with pytest.raises(ObjectNotFound, match="resolved 0 of 1"):
            fetch(pw.simauto, "X", resolve(pw, [(Bus(99), TS.Bus.VPU)]), memory())

    def test_nothing_held(self, pw: PowerWorld):
        with pytest.raises(ResultsUnavailable):
            fetch(pw.simauto, "never run", resolve(pw, [TS.Bus.VPU]), memory())


# ---------------------------------------------------------------------- results
@pytest.fixture
def sim(pw: PowerWorld) -> Result:
    return pw.ts.solve(FAULT, record=[TS.Bus.VPU, (Gen, [TS.Gen.W, TS.Gen.Delta]), TS.System.GenP])


class TestResult:
    def test_field_frame_is_a_view(self, sim: Result):
        frame = sim[TS.Bus.VPU]
        assert frame.index.name == "time"
        assert frame.columns.name == "BusNum"
        assert frame.columns.tolist() == [1, 2, 3]
        assert np.shares_memory(frame.to_numpy(), sim.values)
        np.testing.assert_array_equal(frame[2].to_numpy(), expected(Bus(2), "TSBusVPU"))

    def test_multi_key_columns_match_pw_indexing(self, sim: Result):
        assert sim[TS.Gen.W].columns.names == ["BusNum", "GenID"]
        assert sim[TS.Gen.W].columns.tolist() == [(1, "1"), (3, "1")]

    def test_object_field_series_is_a_view(self, sim: Result):
        series = sim[Gen(3, "1"), TS.Gen.Delta]
        assert series.name == "TSGenDelta"
        assert np.shares_memory(series.to_numpy(), sim.values)
        np.testing.assert_array_equal(series.to_numpy(), expected(Gen(3, "1"), "TSGenDelta"))

    def test_one_object(self, sim: Result):
        assert sim[Gen(1, "1")].columns.tolist() == ["TSGenW", "TSGenDelta"]
        with pytest.raises(KeyError):
            sim[Bus(99)]

    def test_type_and_fields(self, sim: Result):
        frame = sim[Gen, [TS.Gen.W, TS.Gen.Delta]]
        assert frame.columns.names == ["field", "BusNum", "GenID"]
        assert frame.shape == (len(TIME), 4)

    def test_keyless_field_is_one_column(self, sim: Result):
        frame = sim[TS.System.GenP]  # a field is always a frame: time by object
        assert list(frame.columns) == [TS.System.GenP.name]

    def test_everything_recorded(self, sim: Result):
        assert sim.df.shape == (len(TIME), 3 + 2 + 2 + 1)
        assert sim.df.columns.names == ["type", "field", "key"]

    def test_window_and_at(self, sim: Result):
        assert sim.window(0.5, 1.0).time.tolist() == [0.5, 1.0, 1.0]
        assert np.shares_memory(sim.window(0.5, 1.0).values, sim.values)
        row = sim.at(1.0)  # the state just after the event
        assert row[("Bus", "TSBusVPU", (2,))] == np.float32(value(Bus(2), "TSBusVPU", 3))
        with pytest.raises(KeyError):
            sim.at(-1.0)

    def test_select_copies_compactly(self, sim: Result):
        small = sim.select((Bus(2), TS.Bus.VPU))
        assert small.values.shape == (len(TIME), 1)
        assert not np.shares_memory(small.values, sim.values)
        np.testing.assert_array_equal(
            small[Bus(2), TS.Bus.VPU].to_numpy(), expected(Bus(2), "TSBusVPU")
        )

    def test_buffers_are_read_only(self, sim: Result):
        assert not sim.values.flags.writeable
        with pytest.raises(ValueError, match="read-only"):
            sim.values[0, 0] = 1.0

    def test_summary(self, sim: Result):
        assert sim.solved
        assert sim.name == "bus2 fault"
        assert sim.nbytes == len(TIME) * 8 + len(TIME) * 8 * 4
        assert repr(sim) == f"<Result 'bus2 fault': {len(TIME)} steps x 8 columns, 0.0 MiB>"


# ---------------------------------------------------------------------- simulations
class TestSolve:
    def test_simulates_a_scratch_copy_and_deletes_it(
        self, pw: PowerWorld, fake: FakeSimAuto, recorder: Recorder
    ):
        pw.ts.solve(FAULT, record=[TS.Bus.VPU])
        [(name, elements, scratch)] = recorder.simulations
        assert name == "esapp~1"
        assert scratch == ["esapp~1"]
        assert elements[["TSTimeInSeconds", "WhoAmI", "TSEventString"]].values.tolist() == [
            [1.0, "Bus '2'", "FAULT 3PB SOLID"],
            [1.05, "Bus '2'", "CLEARFAULT"],
        ]
        assert fake.tables["TSContingency"].empty
        assert fake.tables["TSContingencyElement"].empty
        assert "esapp~1" not in fake.results  # deleting it is what frees Simulator's memory
        assert 'TSSolve("esapp~1");' in fake.scripts

    def test_records_exactly_the_plan_and_restores_flags(self, pw: PowerWorld, fake: FakeSimAuto):
        sim = pw.ts.solve(FAULT, record=[(Bus(2), TS.Bus.VPU)])
        assert sim.values.shape == (len(TIME), 1)
        resets = [s for s in fake.scripts if s.startswith("TSResultStorageSetAll")]
        assert resets == ["TSResultStorageSetAll(ALL, NO);"]  # every type in one statement
        assert set(fake.tables["Gen"]["TSSaveAll"]) == {"YES"}  # put back
        options = fake.tables["Transient_Options"].iloc[0]
        assert options["TSStoreResultsInRAM"] == "NO"
        assert options["TSOSaveResultsForOpenDevices"] == "NO"  # was YES during the simulation

    def test_unrecorded_fields_are_not_stored(self, pw: PowerWorld, fake: FakeSimAuto):
        pw.ts.solve(FAULT, record=[(Bus(2), TS.Bus.VPU)], keep=True)
        assert set(fake.results["bus2 fault"][1]) == {("Bus", ("2",), "TSBusVPU")}

    def test_every(self, pw: PowerWorld, fake: FakeSimAuto):
        sim = pw.ts.solve(FAULT, record=[TS.Bus.VPU], every=2)
        assert sim.time.tolist() == [0.0, 1.0, 1.5]
        assert fake.tables["Transient_Options"].iloc[0]["TSOSaveResultsTimeStepsPerSave"] == 1
        with pytest.raises(ValueError, match="positive"):
            pw.ts.solve(FAULT, record=[TS.Bus.VPU], every=0)

    def test_window_and_dtype(self, pw: PowerWorld):
        sim = pw.ts.solve(FAULT, record=[TS.Bus.VPU], window=(1.0, 2.0), dtype=np.float64)
        assert sim.time.tolist() == [1.0, 1.0, 1.5, 2.0]
        assert sim.values.dtype == np.float64

    def test_keep_leaves_the_contingency_and_its_results(self, pw: PowerWorld, fake: FakeSimAuto):
        kept = pw.ts.solve(FAULT, record=[TS.Bus.VPU], keep=True)
        assert fake.tables["TSContingency"]["TSCTGName"].tolist() == ["bus2 fault"]
        again = pw.ts.fetch("bus2 fault", record=[TS.Bus.VPU])
        np.testing.assert_array_equal(again.values, kept.values)

    def test_case_contingency_simulates_as_a_copy(
        self, pw: PowerWorld, fake: FakeSimAuto, recorder: Recorder
    ):
        pw.ts.define(FAULT)
        sim = pw.ts.solve(TSContingency("bus2 fault"), record=[TS.Bus.VPU])
        [(name, elements, _)] = recorder.simulations
        assert name == "esapp~1"
        assert elements["TSEventString"].tolist() == ["FAULT 3PB SOLID", "CLEARFAULT"]
        assert sim.name == "bus2 fault"
        assert fake.tables["TSContingency"]["TSCTGName"].tolist() == ["bus2 fault"]
        assert "bus2 fault" not in fake.results  # the case's own contingency never ran

    def test_missing_case_contingency(self, pw: PowerWorld, fake: FakeSimAuto):
        with pytest.raises(ObjectNotFound):
            pw.ts.solve("nope", record=[TS.Bus.VPU])
        assert fake.tables.get("TSContingency", pd.DataFrame()).empty  # no scratch copy left

    def test_scratch_names_skip_leftovers(self, pw: PowerWorld, recorder: Recorder):
        pw.create(TSContingency, pd.DataFrame({"TSCTGName": ["esapp~7"]}))
        pw.ts.solve(FAULT, record=[TS.Bus.VPU])
        assert recorder.simulations[0][0] == "esapp~8"

    def test_contingencies_round_trip(self, pw: PowerWorld):
        assert pw.ts.define(FAULT) == TSContingency("bus2 fault")
        assert pw.ts.contingencies() == [FAULT]

    def test_stats(self, pw: PowerWorld, fake: FakeSimAuto):
        def dynamics(name: str, elements: pd.DataFrame):
            fake.add(
                TSStats_Bus,
                {"BusNum": [1, 2], "TSCTGName": [name, "other"], "TSBusMinMaxVoltPU": [0.5, 0.9]},
            )
            return TIME, trajectories()

        fake.dynamics = dynamics
        sim = pw.ts.solve(FAULT, record=[], stats=[TSStats_Bus.TSBusMinMaxVoltPU])
        assert sim.values.shape == (0, 0)
        stat = sim.stats[TSStats_Bus.TSBusMinMaxVoltPU]
        assert stat.index.name == "BusNum"
        assert stat.to_dict() == {1: 0.5}

    def test_stats_must_be_per_contingency(self, pw: PowerWorld):
        with pytest.raises(ValueError, match="per-contingency"):
            pw.ts.solve(FAULT, record=[], stats=[Bus.BusPUVolt])

    def test_unsolved_contingency_gives_an_empty_result(self, pw: PowerWorld):
        pw.create(TSContingency, pd.DataFrame({"TSCTGName": ["idle"]}))
        sim = pw.ts.fetch("idle", record=[TS.Bus.VPU])
        assert not sim.solved
        assert sim.values.shape == (0, 3)

    def test_read_only_sessions_cannot_simulate(self, pw: PowerWorld, fake: FakeSimAuto):
        with pw.read_only(), pytest.raises(ReadOnlySessionError):
            pw.ts.solve(FAULT, record=[TS.Bus.VPU])
        assert not fake.results


TRIPS = [Contingency(f"trip {n}", trip(Gen(n, "1"), at=1.0), stop=2.0) for n in (1, 3)]


class TestSweep:
    def test_one_simulation_at_a_time(self, pw: PowerWorld, fake: FakeSimAuto, recorder: Recorder):
        sweep = pw.ts.sweep(TRIPS, record=[TS.Gen.W])
        assert len(sweep) == 2
        assert not recorder.simulations  # nothing starts until iterated
        names = [sim.name for sim in sweep]
        assert names == ["trip 1", "trip 3"]
        assert [scratch for _, _, scratch in recorder.simulations] == [["esapp~1"], ["esapp~2"]]
        resets = [s for s in fake.scripts if s.startswith("TSResultStorageSetAll")]
        assert len(resets) == 1  # flags set once for the whole sweep

    def test_save_and_reopen(self, pw: PowerWorld, recorder: Recorder, tmp_path: Path):
        store = pw.ts.sweep(TRIPS, record=[TS.Gen.W, TS.Bus.VPU], stats=[]).save(tmp_path)
        assert list(store) == ["trip 1", "trip 3"]
        reopened = Store(tmp_path)
        sim = reopened["trip 3"]
        assert isinstance(sim.values, np.memmap)
        assert sim.values.flags.f_contiguous
        np.testing.assert_array_equal(
            sim[Gen(3, "1"), TS.Gen.W].to_numpy(), expected(Gen(3, "1"), "TSGenW")
        )
        assert sim.solved
        assert repr(reopened) == f"Store({str(tmp_path)!r}: 2 simulations)"
        del sim, store, reopened

    def test_resume_skips_saved_simulations(
        self, pw: PowerWorld, recorder: Recorder, tmp_path: Path
    ):
        pw.ts.sweep(TRIPS[:1], record=[TS.Gen.W]).save(tmp_path)
        store = pw.ts.sweep(TRIPS, record=[TS.Gen.W]).save(tmp_path)
        assert len(recorder.simulations) == 2
        assert len(store) == 2
        with pytest.raises(ValueError, match="already in the store"):
            pw.ts.sweep(TRIPS, record=[TS.Gen.W]).save(tmp_path, resume=False)

    def test_simulations_without_fields(self, pw: PowerWorld, tmp_path: Path):
        store = pw.ts.sweep(TRIPS, record=[]).save(tmp_path)
        assert store["trip 1"].values.shape == (0, 0)

    def test_stats_survive_the_store(self, pw: PowerWorld, fake: FakeSimAuto, tmp_path: Path):
        def dynamics(name: str, elements: pd.DataFrame):
            fake.add(TSStats_Bus, {"BusNum": [2], "TSCTGName": [name], "TSBusMinMaxVoltPU": [0.7]})
            return TIME, trajectories()

        fake.dynamics = dynamics
        sweep = pw.ts.sweep(TRIPS, record=[], stats=[TSStats_Bus.TSBusMinMaxVoltPU])
        stat = sweep.save(tmp_path)["trip 1"].stats[TSStats_Bus.TSBusMinMaxVoltPU]
        assert stat.to_dict() == {2: 0.7}

    def test_missing_store(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            Store(tmp_path)


class TestStep:
    def test_steps_then_transfers(self, pw: PowerWorld, fake: FakeSimAuto):
        with pw.ts.step(FAULT, record=[TS.Bus.VPU]) as sim:
            assert sim.t is None
            times = list(sim.every(0.5, until=1.5))
            result = sim.result()
        assert times == [0.5, 1.0, 1.5]
        steps = [s for s in fake.scripts if s.startswith(("TSInitialize", "TSRunUntil"))]
        assert steps == [
            "TSInitialize;",
            'TSRunUntilSpecifiedTime("esapp~1", [0.5, 0.25, YES, YES]);',
            'TSRunUntilSpecifiedTime("esapp~1", [1.0]);',
            'TSRunUntilSpecifiedTime("esapp~1", [1.5]);',
        ]
        assert result.time.tolist() == [0.0, 0.5, 1.0, 1.0, 1.5]
        assert fake.tables["TSContingency"].empty

    def test_cannot_step_back(self, pw: PowerWorld):
        with pw.ts.step(FAULT, record=[TS.Bus.VPU]) as sim:
            sim.until(1.0)
            with pytest.raises(ValueError, match="already at"):
                sim.until(0.5)

    def test_events_join_the_paused_simulation(self, pw: PowerWorld, fake: FakeSimAuto):
        with pw.ts.step(FAULT, record=[TS.Bus.VPU]) as sim:
            sim.until(1.0)
            sim.apply(trip(Gen(3, "1"), at=1.25))
            with pytest.raises(ValueError, match="must come later"):
                sim.apply(trip(Gen(1, "1"), at=1.0))
            elements = fake.tables["TSContingencyElement"]
            mine = elements[elements["TSCTGName"] == "esapp~1"]
            assert mine["TSEventString"].tolist()[-1] == "OPEN"
            assert mine["WhoAmI"].tolist()[-1] == "Gen '3' '1'"
        assert fake.tables["TSContingencyElement"].empty  # went with the scratch copy


class TestPlayIn:
    SIGNALS = pd.DataFrame(
        {"speed": [1.0, 1.01, 0.99], "volts": [1.0, 0.9, 1.0]}, index=[0.0, 0.5, 1.0]
    )

    def test_defined_and_read_back(self, pw: PowerWorld, fake: FakeSimAuto):
        playin = PlayIn("gust", self.SIGNALS, scale={"volts": 2.0}, delay=0.25)
        ref = pw.ts.define(playin)
        assert ref == PlayInObject("gust")
        signals = fake.tables["PlayInSignal"]
        assert signals["TSSignal"].tolist() == [1.0, 1.01, 0.99]  # the first signal
        assert signals["TSSignal:1"].tolist() == [1.0, 0.9, 1.0]  # the second
        [back] = pw.ts.playins()
        assert back.name == "gust"
        assert back.delay == 0.25
        assert back.scale == {"speed": 1.0, "volts": 2.0}
        assert back.offset == {"speed": 0.0, "volts": 0.0}
        pd.testing.assert_frame_equal(
            back.signals, self.SIGNALS.rename_axis("time"), check_index_type=False
        )
        pw.ts.define(PlayIn("gust", self.SIGNALS.iloc[:2, :1]))  # replaces it
        [again] = pw.ts.playins()
        assert again.names == ["speed"]
        assert len(again.signals) == 2

    @pytest.mark.parametrize(
        ("signals", "extra", "message"),
        [
            (pd.DataFrame(), {}, "at least one"),
            (pd.DataFrame({"a": [1.0, 2.0]}, index=[1.0, 0.5]), {}, "increase"),
            (pd.DataFrame({"a": [1.0]}, index=[0.0]), {"scale": {"b": 2.0}}, "no signals named"),
        ],
        ids=["empty", "time-order", "unknown-signal"],
    )
    def test_invalid(self, signals: pd.DataFrame, extra: dict[str, object], message: str):
        with pytest.raises(ValueError, match=message):
            PlayIn("bad", signals, **extra)  # pyright: ignore[reportArgumentType]
