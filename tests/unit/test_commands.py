"""The script-command catalog: extraction from the AUX document, generation, and encoding."""

import dataclasses
from pathlib import Path
from typing import ClassVar

import pytest
from tools.codegen import commands as gen
from tools.codegen.sources.simauto import command_arity

from esapp import Kw
from esapp.commands import (
    CalculatePTDF,
    Command,
    LogSave,
    SetData,
    SolvePowerFlow,
    TSClearResultsFromRAM,
    TSResultStorageSetAll,
    TSSolve,
    TSSolveContinue,
)
from esapp.grid import Area, Bus, Gen
from esapp.simauto import SimAuto
from esapp.testing import FakeSimAuto

DOCUMENT = """\
Auxiliary File Format
Table of Contents
SCRIPT Section ............................................................ 16
   Using Filters in Script Commands........................................ 16
      General Program Actions.............................................. 19
         LogClear; ........................................................ 19
         LogSave("filename", AppendFile); ................................. 20
TS (Transient Stability) .................................................. 120
   TSSolve("ContingencyName", [StartTime, StopTime, StepSize,

       StepInCycles]); .................................................... 136
   TimeStepResetRun .................................................. 146
   TSSolveContinue; .. 137
                                     4
DATA Section .............................................................. 157
   Nothing here(Is, A, Command); .......................................... 158

Introduction
LogClear;

Use this action to clear the Message Log.

LogSave("filename", AppendFile);
This action saves the contents of the Message Log to "filename". More text.
"filename"      : The file to save to.
AppendFile      : Optional parameter - default is NO.
TSSolve("ContingencyName", [StartTime, StopTime, StepSize,
StepInCycles]);
Solves only the specified contingency.
ContingencyName : The name of the contingency to solve
Times     : (optional) four optional parameters
TimeStepResetRun
Resets the run.
TSSolveContinue;
(This command was added on December 22, 2025 to Simulator 25) Continue a paused run.
"""


class TestExtraction:
    def test_rows(self):
        rows = {r.name: r for r in gen.extract(DOCUMENT)}
        assert list(rows) == [
            "LogClear",
            "LogSave",
            "TSSolve",
            "TimeStepResetRun",
            "TSSolveContinue",
        ]
        assert rows["LogSave"] == gen.Row(
            "LogSave",
            "General Program Actions",
            '"filename", AppendFile',
            1,
            "",
            'This action saves the contents of the Message Log to "filename".',
        )
        solve = rows["TSSolve"]
        assert solve.section == "TS (Transient Stability)"  # a heading with parentheses
        assert solve.signature == '"ContingencyName", [StartTime, StopTime, StepSize, StepInCycles]'
        assert solve.required == 1  # the times are optional under another name
        assert solve.description == "Solves only the specified contingency."
        assert rows["TimeStepResetRun"].signature == ""
        assert rows["TSSolveContinue"].since == "2025-12"
        assert rows["TSSolveContinue"].description == "Continue a paused run."

    def test_rows_round_trip_through_the_tsv(self, tmp_path: Path):
        rows = gen.extract(DOCUMENT)
        gen.write_rows(rows, tmp_path / "commands.tsv")
        assert gen.read_rows(tmp_path / "commands.tsv") == rows

    @pytest.mark.parametrize(
        ("text", "name"),
        [
            ("CreateIfNotFound1", "create_if_not_found1"),
            ("GEFileType", "ge_file_type"),
            ("transactor seller", "transactor_seller"),
            ("ISO8601StartDateTime", "iso8601_start_date_time"),
            ("Name", "name_"),  # a Command attribute
            ("from", "from_"),
        ],
    )
    def test_python_names(self, text: str, name: str):
        assert gen.snake(text) == name

    @pytest.mark.parametrize(
        ("token", "kind"),
        [
            ('"filename"', ("path", "filename")),
            ('"ContingencyName"', ("str", "contingency_name")),
            ("[fieldlist]", ("fields", "fields")),
            ("[valuelist]", ("list", "values")),
            ("[BUS num]", ("object", "bus_num")),
            ("[ObjectType List]", ("types", "object_type_list")),
            ("[StartTime, StopTime]", ("list", "options")),
            ("objecttype", ("type", "object_type")),
            ("filter", ("filter", "filter")),
            ("SolMethod", ("word", "sol_method")),
        ],
    )
    def test_parameter_kinds(self, token: str, kind: tuple[str, str]):
        names = {"fieldlist": "fields", "valuelist": "values", "objecttype": "object_type"}
        assert gen._kind(token, names) == kind  # pyright: ignore[reportPrivateUsage]


class TestSpecs:
    ROWS = (gen.Row("Probe", "Tests", '"filename", Mode, filter', 1, "2030-01", "Probes."),)

    def test_overrides_rename_and_type(self):
        overrides = gen.Overrides(
            words={"Mode": ("FAST", "SLOW")},
            types={"mode": "Mode"},
            commands={"Probe": {"names": {"filename": "path"}, "required": 2}},
        )
        (spec,) = gen.specs(self.ROWS, overrides)
        assert [(p.name, p.kind, p.required) for p in spec.params] == [
            ("path", "path", True),
            ("mode", "Mode", True),
            ("filter", "filter", False),
        ]
        assert spec.since == "2030-01"

    @pytest.mark.parametrize(
        "fix",
        [
            {"Gone": {}},
            {"Probe": {"names": {"nothing": "x"}}},
            {"Probe": {"types": {"mode": "Unknown"}}},
            {"Probe": {"names": {"mode": "filter"}}},
        ],
        ids=["unknown-command", "unknown-parameter", "unknown-type", "collision"],
    )
    def test_stale_overrides_fail(self, fix: dict[str, dict[str, object]]):
        with pytest.raises(ValueError, match="command"):
            gen.specs(self.ROWS, gen.Overrides(commands=fix))

    def test_overrides_file_is_strict(self, tmp_path: Path):
        path = tmp_path / "commands.toml"
        path.write_text('[types]\nmode = "Mode"\nread_only = ["Log*"]\n', encoding="utf-8")
        with pytest.raises(ValueError, match=r"\[types\]: not names: \['read_only'\]"):
            gen.Overrides.load(path)  # read_only fell into the table above it
        path.write_text('read_only = ["Log*"]\nmistake = 1\n', encoding="utf-8")
        with pytest.raises(ValueError, match="unknown entries"):
            gen.Overrides.load(path)

    def test_simulators_parameter_counts_decide_what_is_required(self, tmp_path: Path):
        gen.write_arity({"Probe": (0, 4)}, tmp_path / "arity.tsv")
        arity = gen.read_arity(tmp_path / "arity.tsv")
        assert arity == {"Probe": (0, 4)}
        (spec,) = gen.specs(self.ROWS, gen.Overrides(), arity)
        assert not any(p.required for p in spec.params)  # the document said one
        assert gen.disagreements([spec], arity) == ["Probe takes up to 4, the document lists 3"]
        fixed = gen.Overrides(commands={"Probe": {"required": 1}})
        assert gen.specs(self.ROWS, fixed, arity)[0].params[0].required  # an override wins
        assert gen.read_arity(tmp_path / "missing.tsv") == {}

    def test_parameter_counts_from_validation_errors(self):
        class Validator(FakeSimAuto):
            REPLIES: ClassVar[dict[str, str]] = {
                "Range": 'Script action "Range" requires between 1 and 3 parameters, but 64',
                "Exact": 'Script action "Exact" requires exactly 2 parameters, but 64 were found',
                "None": 'Script action "None" does not pass any arguments',
                "Vague": 'Incorrect number of arguments for "Vague".',
            }

            def call(self, method: str, /, *args: object) -> object:
                name = str(args[0]).partition("(")[0]
                return (
                    f"RunScriptCommand: Error in script action validation: {self.REPLIES[name]}",
                )

        names = ["Range", "Exact", "None", "Vague"]
        assert command_arity(SimAuto(Validator()), names) == {
            "Range": (1, 3),
            "Exact": (2, 2),
            "None": (0, 0),
        }

    def test_read_only_globs(self):
        (spec,) = gen.specs(self.ROWS, gen.Overrides(read_only=("Pro*",)))
        assert not spec.mutates

    def test_rendered_catalog_runs(self):
        overrides = gen.Overrides(words={"Mode": ("FAST", "SLOW")}, types={"mode": "Mode"})
        files = gen.render(gen.specs(self.ROWS, overrides), overrides, "# header\n")
        namespace: dict[str, object] = {}
        exec(compile(files[gen.CATALOG], "catalog", "exec"), namespace)
        probe = namespace["Probe"]
        assert isinstance(probe, type)
        assert issubclass(probe, Command)
        assert probe.since == "2030-01"
        assert str(probe("a.txt", "FAST", Kw.SELECTED)) == 'Probe("a.txt", FAST, SELECTED);'
        assert "from esapp.commands._catalog import Probe as Probe" in files[gen.STUB]


class TestCatalog:
    @pytest.mark.parametrize(
        ("command", "text"),
        [
            pytest.param(SolvePowerFlow(), "SolvePowerFlow;", id="defaults"),
            pytest.param(SolvePowerFlow("POLARNEWTON"), "SolvePowerFlow(POLARNEWTON);", id="word"),
            pytest.param(
                SolvePowerFlow(on_failure="stop.aux"),
                'SolvePowerFlow(, , "stop.aux");',
                id="empty-slots",
            ),
            pytest.param(
                SetData(Gen, [Gen.GenMW, "GenMVR"], [1.5, 0.0], Kw.SELECTED),
                "SetData(Gen, [GenMW, GenMVR], [1.5, 0.0], SELECTED);",
                id="fields",
            ),
            pytest.param(
                SetData(Bus, [Bus.BusName], ["North"], Bus.BusNum > 2),
                'SetData(Bus, [BusName], ["North"], "BusNum > 2");',
                id="condition",
            ),
            pytest.param(
                CalculatePTDF(Area(1), Area(2), "DC"),
                "CalculatePTDF([AREA 1], [AREA 2], DC);",
                id="objects",
            ),
            pytest.param(
                TSSolve("fault", [0, 10, 0.01, False]),
                'TSSolve("fault", [0, 10, 0.01, NO]);',
                id="options",
            ),
            pytest.param(TSClearResultsFromRAM(Kw.ALL), "TSClearResultsFromRAM(ALL);", id="kw"),
            pytest.param(
                TSResultStorageSetAll(Gen, store=False),
                "TSResultStorageSetAll(Gen, NO);",
                id="bool",
            ),
            pytest.param(
                LogSave(Path("log.txt"), append_file=True), 'LogSave("log.txt", YES);', id="path"
            ),
        ],
    )
    def test_statements(self, command: Command, text: str):
        assert str(command) == text

    def test_metadata(self):
        assert SolvePowerFlow.section == "Power Flow"
        assert SolvePowerFlow.mutates
        assert not LogSave.mutates
        assert not CalculatePTDF.mutates
        assert TSSolveContinue.since == "2025-12"
        assert SetData.filters == {"filter"}

    def test_commands_are_frozen_values(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            SolvePowerFlow().sol_method = "DC"  # pyright: ignore[reportAttributeAccessIssue]

    def test_required_parameters_are_required(self):
        with pytest.raises(TypeError):
            SetData(Gen)  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]
