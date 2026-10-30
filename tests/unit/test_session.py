"""The PowerWorld session over FakeSimAuto: lifecycle, commands, scopes, creation."""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from esapp import (
    Kw,
    ObjectNotFound,
    PowerWorld,
    ReadOnlySessionError,
    UnknownCommand,
    Where,
)
from esapp.commands import Command
from esapp.grid import Bus, Gen, Sim_Solution_Options
from esapp.schema import GObject
from esapp.testing import FakeSimAuto


@pytest.fixture
def case(tmp_path: Path) -> Path:
    path = tmp_path / "case.pwb"
    path.write_bytes(b"case")
    return path


class TestLifecycle:
    def test_open_and_close(self, case: Path):
        fake = FakeSimAuto()
        with PowerWorld(case, transport=fake) as pw:
            assert pw.case == case.resolve()
            assert fake.case == str(case.resolve())
            assert repr(pw) == f"PowerWorld({case.resolve()})"
        assert fake.case is None
        assert fake.closed

    def test_missing_case_releases_simauto(self, tmp_path: Path):
        fake = FakeSimAuto()
        with pytest.raises(FileNotFoundError):
            PowerWorld(tmp_path / "missing.pwb", transport=fake)
        assert fake.closed

    def test_opening_another_case_closes_the_first(self, case: Path, tmp_path: Path):
        other = tmp_path / "other.pwb"
        other.write_bytes(b"other")
        fake = FakeSimAuto()
        pw = PowerWorld(case, transport=fake)
        pw.open(other)
        assert [m for m, _ in fake.calls] == ["OpenCase", "CloseCase", "OpenCase"]

    def test_ensure_open_reuses_the_open_case(self, case: Path):
        fake = FakeSimAuto()
        pw = PowerWorld(case, transport=fake)
        pw.ensure_open(case)
        assert [m for m, _ in fake.calls] == ["OpenCase"]

    def test_attached_sessions_leave_the_transport_open(self, case: Path):
        fake = FakeSimAuto()
        with PowerWorld.attach(fake, case) as pw:
            assert pw.case == case
            scratch = pw.scratch
            assert scratch.is_dir()
        assert not fake.closed
        assert "CloseCase" not in [m for m, _ in fake.calls]
        assert not scratch.exists()  # the scratch directory goes with the session


@dataclass(frozen=True, slots=True)
class Probe(Command, name="LogAdd", mutates=False):
    message: str


@dataclass(frozen=True, slots=True)
class Mark(Command, name="SelectAll", filters=["where"]):
    objecttype: type[GObject]
    where: Where | None = None


@dataclass(frozen=True, slots=True)
class Future(Command, name="FutureCommand", since="2099-01"):
    pass


class TestCommands:
    def test_run_encodes_arguments(self, fake_pw: PowerWorld, fake_simauto: FakeSimAuto):
        fake_simauto.add(Gen, {"BusNum": [1], "GenID": ["1"], "GenMW": [0.0]})
        fake_pw.run("SetData", Gen, [Gen.BusNum, Gen.GenID, Gen.GenMW], [1, "1", 1.5], None)
        assert fake_simauto.scripts == ['SetData(Gen, [BusNum, GenID, GenMW], [1, "1", 1.5]);']
        assert fake_pw[Gen.GenMW].tolist() == [1.5]

    def test_commands_run_as_one_batch(self, fake_pw: PowerWorld, fake_simauto: FakeSimAuto):
        fake_simauto.add(Bus, {"BusNum": [1, 2], "AreaNum": [1, 2]})
        fake_pw.run(Probe("one"), Mark(Bus, Bus.AreaNum.eq(2)), Probe("two"))
        batches = [args[0] for method, args in fake_simauto.calls if method == "RunScriptCommand"]
        assert batches[0] == 'LogAdd("one");SelectAll(Bus, "AreaNum = 2");LogAdd("two");'
        assert fake_pw.refs(Bus, Kw.SELECTED) == [Bus(2)]

    def test_compound_selections_in_commands(self, fake_pw: PowerWorld, fake_simauto: FakeSimAuto):
        fake_simauto.add(Bus, {"BusNum": [1, 2, 3], "AreaNum": [1, 2, 2]})
        fake_pw.run(Mark(Bus, Bus.AreaNum.eq(2) & (Bus.BusNum > 2)))
        assert fake_pw.refs(Bus, Kw.SELECTED) == [Bus(3)]
        assert fake_simauto.tables["Filter"].empty  # made for the batch, then deleted

    def test_command_text_outside_a_session(self):
        assert str(Probe("hi")) == 'LogAdd("hi");'
        assert str(Mark(Bus, Bus(5))) == "SelectAll(Bus, \"<DEVICE>Bus '5'\");"
        with pytest.raises(TypeError, match="filter object"):
            str(Mark(Bus, Bus.AreaNum.eq(1) & (Bus.BusNum > 1)))

    def test_read_only_runs_only_commands_that_leave_the_case(self, fake_pw: PowerWorld):
        with fake_pw.read_only():
            fake_pw.run(Probe("fine"))
            with pytest.raises(ReadOnlySessionError):
                fake_pw.run(Probe("fine"), Mark(Bus))

    def test_errors_name_the_batch_and_late_commands(
        self, fake_pw: PowerWorld, fake_simauto: FakeSimAuto
    ):
        fake_simauto.errors["RunScriptCommand"] = 'Unknown script command "FutureCommand".'
        with pytest.raises(UnknownCommand) as caught:
            fake_pw.run(Probe("x"), Future())
        assert caught.value.__notes__ == [
            "in a batch of 2: LogAdd, FutureCommand",
            "FutureCommand needs a Simulator built 2099-01 or later",
        ]

    def test_read_only_blocks_mutating_commands(
        self, fake_pw: PowerWorld, fake_simauto: FakeSimAuto
    ):
        with fake_pw.read_only():
            fake_pw.run("LogClear", mutates=False)
            with pytest.raises(ReadOnlySessionError):
                fake_pw.run("SolvePowerFlow")
        fake_pw.run("SolvePowerFlow")
        assert fake_simauto.scripts == ["LogClear;", "SolvePowerFlow;"]

    def test_log(self, fake_pw: PowerWorld):
        fake_pw.log.add("hello")
        assert fake_pw.log.text() == "hello\n"
        fake_pw.log.clear()
        assert fake_pw.log.text() == ""


class TestScopes:
    def test_options_are_restored(self, fake_pw: PowerWorld, fake_simauto: FakeSimAuto):
        fake_simauto.add(Sim_Solution_Options, {"MaxItr": [250], "ConvergenceTol": [0.1]})
        options = {Sim_Solution_Options.MaxItr: 5, Sim_Solution_Options.ConvergenceTol: 0.5}
        with fake_pw.options(options):
            assert fake_pw[Sim_Solution_Options, ["MaxItr", "ConvergenceTol"]].iloc[0].tolist() == [
                5,
                0.5,
            ]
        assert fake_pw[Sim_Solution_Options, ["MaxItr", "ConvergenceTol"]].iloc[0].tolist() == [
            250,
            0.1,
        ]

    def test_options_are_keyless_fields(self, fake_pw: PowerWorld):
        with pytest.raises(ValueError, match="not an option"), fake_pw.options({Gen.GenMW: 1.0}):
            pass

    def test_edit_mode_returns_to_the_previous_mode(
        self, fake_pw: PowerWorld, fake_simauto: FakeSimAuto
    ):
        with fake_pw.edit_mode(), fake_pw.edit_mode():
            pass
        assert fake_simauto.scripts == ["EnterMode(EDIT);", "EnterMode(RUN);"]

    def test_snapshot_restores_state(self, fake_pw: PowerWorld, fake_simauto: FakeSimAuto):
        fake_simauto.add(Bus, {"BusNum": [1], "BusName": ["One"]})
        with fake_pw.snapshot():
            fake_pw[Bus, Bus.BusName] = ["Changed"]
        assert fake_pw[Bus, Bus.BusName]["BusName"].tolist() == ["One"]


class TestObjects:
    def test_create_in_edit_mode_with_create_if_not_found(
        self, fake_pw: PowerWorld, fake_simauto: FakeSimAuto
    ):
        fake_simauto.add(Bus, {"BusNum": [1], "BusName": ["One"]})
        fake_pw.create(Bus, pd.DataFrame({Bus.BusNum: [7], Bus.BusName: ["Seven"]}))
        assert fake_pw[Bus, Bus.BusName]["BusName"].tolist() == ["One", "Seven"]
        assert fake_simauto.scripts == ["EnterMode(EDIT);", "EnterMode(RUN);"]
        assert fake_simauto.properties["CreateIfNotFound"] is False

    def test_create_names_missing_required_fields(
        self, fake_pw: PowerWorld, fake_simauto: FakeSimAuto
    ):
        fake_simauto.errors["ChangeParametersMultipleElement"] = (
            "Error in object (0): Object not found."
        )
        with pytest.raises(ObjectNotFound) as caught:
            fake_pw.create(Bus, pd.DataFrame({Bus.BusNum: [7], Bus.BusNomVolt: [138.0]}))
        assert caught.value.__notes__ == ["Bus also needs ['AreaNum', 'BusName', 'ZoneNum']"]

    def test_create_needs_a_key_set(self, fake_pw: PowerWorld):
        with pytest.raises(ValueError, match=r"needs a complete key set: \['BusNum'\]"):
            fake_pw.create(Bus, pd.DataFrame({"BusName": ["Nameless"]}))

    def test_create_respects_read_only(self, fake_pw: PowerWorld):
        with fake_pw.read_only(), pytest.raises(ReadOnlySessionError):
            fake_pw.create(Bus, pd.DataFrame({"BusNum": [7]}))

    def test_refs(self, fake_pw: PowerWorld, fake_simauto: FakeSimAuto):
        fake_simauto.add(Gen, {"BusNum": [1, 3], "GenID": ["1", "2"]})
        refs = fake_pw.refs(Gen)
        assert refs == [Gen(1, "1"), Gen(3, "2")]
        assert repr(refs[0]) == "Gen(1, '1')"  # Python scalars, not numpy ones

    def test_refs_of_a_keyless_type(self, fake_pw: PowerWorld):
        assert fake_pw.refs(Sim_Solution_Options) == [Sim_Solution_Options()]
