"""The code generator: key notation, the field-export source, overrides, emission, and sync."""

import gzip
from dataclasses import replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import ClassVar

import pytest
from tools.codegen.drift import compare
from tools.codegen.emit import CATALOG, GRID, audit, render, sync, ts_catalog
from tools.codegen.model import FieldSpec, ObjectSpec, parse_key_symbol
from tools.codegen.overrides import Overrides, TSGroup
from tools.codegen.sources import FieldExport, SimAutoSource, write_export
from tools.codegen.sources.simauto import option_vocab
from tools.codegen.subdata import Overrides as SubOverrides
from tools.codegen.subdata import Row as SubRow
from tools.codegen.subdata import extract as extract_subdata
from tools.codegen.subdata import read_rows as read_subdata_rows
from tools.codegen.subdata import write_rows as write_subdata_rows

from esapp.schema import Role
from esapp.simauto import SimAuto
from esapp.testing import FakeSimAuto

EXPORT = (
    "Object Type\tSUBDATA Allowed\tKey/Required Fields\tVariable Name\tConcise\tType\t"
    "Description\tField List\tEnterable\tMaintainer\tInheritance\n"
    "Widget\tNo\t\t\t\t\t\t\t\tYes\t\n"
    "\t\t*1*<\tWidgetNum\tNum\tInteger\t'Widget number'\t'Widget\\Number'\t'Yes'\n"
    "\t\t*2*\tWidgetNum:1\tNum2\tInteger\t'Partner number'\t''\t'No'\n"
    "\t\t*A*\tName\tName\tString\t'A long\n"
    "description'\t''\t'Edit Mode Only'\n"
    "\t\t**<\tSize\tSize\tReal\t'Size'\t''\t'Yes'\n"
    "\t\t\tTSWidgetSpeed:1\tSpd\tReal\t'Speed'\t''\t'No'\n"
    "\t\t\tTSSaveWidgetSpeed\tSave\tString\t'Save speed'\t''\t'Yes'\n"
    "Bare\tYes\n"
)

WIDGET = ObjectSpec(
    "Widget",
    (
        FieldSpec("WidgetNum", "int", "Widget number", key=1, enterable=True),
        FieldSpec("WidgetNum:1", "int", "Partner number", key=2),
        FieldSpec("Name", "str", "A long description", alt_key=1, enterable=True, edit_only=True),
        FieldSpec("Size", "float", "Size", required=True, enterable=True),
        FieldSpec("TSWidgetSpeed:1", "float", "Speed"),
        FieldSpec("TSSaveWidgetSpeed", "str", "Save speed", enterable=True),
    ),
)


@pytest.mark.parametrize(
    ("symbol", "decoded"),
    [
        ("*1*", (1, None, False)),
        ("*1*<", (1, None, False)),
        ("*2B*", (2, 2, False)),
        ("*3C*<", (3, 3, False)),
        ("*A*", (None, 1, False)),
        ("*B*<", (None, 2, False)),
        ("**<", (None, None, True)),
        ("<", (None, None, False)),
        ("", (None, None, False)),
    ],
)
def test_key_symbols(symbol: str, decoded: tuple[int | None, int | None, bool]):
    assert parse_key_symbol(symbol) == decoded


@pytest.mark.parametrize("compressed", [False, True], ids=["tsv", "gz"])
def test_field_export_source(tmp_path: Path, compressed: bool):
    path = tmp_path / ("schema.tsv.gz" if compressed else "schema.tsv")
    if compressed:
        path.write_bytes(gzip.compress(EXPORT.encode()))
    else:
        path.write_text(EXPORT, encoding="utf-8")
    source = FieldExport(path)
    assert source.label == path.name
    assert list(source.objects()) == [WIDGET, ObjectSpec("Bare", ())]


def test_overrides(tmp_path: Path):
    path = tmp_path / "overrides.toml"
    path.write_text(
        """
        [exclude]
        objects = ["Bare"]
        fields = ["Size"]

        [[add]]
        object = "Widget"
        name = "Extra"
        dtype = "float"
        enterable = true

        [vocab]
        Status = ["Closed", "Open"]

        [identifiers]
        fields = ["Name"]

        [ts]
        suffixes = ["State"]
        unrecordable = ["Mirror"]
        [ts.groups]
        Widget = { prefix = "TSWidget", owner = "Widget" }
        [ts.save]
        "Widget.Size" = ["TSSaveWidgetSpeed"]
        """
    )
    overrides = Overrides.load(path)
    slash = FieldSpec("A/B", "str")
    objects = overrides.apply([ObjectSpec("Bare", ()), WIDGET, ObjectSpec("Alpha", (slash,))])
    assert [o.name for o in objects] == ["Alpha", "Widget"]
    assert objects[0].fields == ()
    assert [f.name for f in objects[1].fields][-2:] == ["TSSaveWidgetSpeed", "Extra"]
    assert "Size" not in {f.name for f in objects[1].fields}
    assert next(f for f in objects[1].fields if f.name == "Name").identifier
    assert overrides.vocab == (("Status", ("Closed", "Open")),)
    assert overrides.ts_groups == (("Widget", TSGroup("TSWidget", "Widget")),)
    assert overrides.ts_suffixes == ("State",)
    assert overrides.ts_unrecordable == {"Mirror"}
    assert overrides.ts_save == (("Widget.Size", ("TSSaveWidgetSpeed",)),)


OVERRIDES = Overrides(
    vocab=(("Size", ("Big", "Small")),),
    ts_groups=(("Widget", TSGroup("TSWidget", "Widget")),),
)


def _thing(*fields: FieldSpec) -> ObjectSpec:
    flags = [
        "TSSaveAll",
        "TSSaveThingP",
        "TSSaveThingMWRef",
        "TSSaveThingExciter",
        "TSSaveThingFreq",
    ]
    return ObjectSpec("Thing", (*fields, *(FieldSpec(f, "str", enterable=True) for f in flags)))


THING_OVERRIDES = Overrides(
    ts_groups=(("Thing", TSGroup("TSThing", "Thing")),),
    ts_suffixes=("State", "inPU"),
    ts_unrecordable=frozenset({"Mirror"}),
    ts_save=(("Thing.Frequency", ("TSSaveThingFreq",)),),
)


def test_ts_catalog_pairs_fields_with_their_flags():
    thing = _thing(
        FieldSpec("TSThingP", "float", "MW"),  # named like its flag
        FieldSpec("TSThingPinPU", "float", "MW in pu"),  # the flag's name plus a suffix
        FieldSpec("TSThingMWREf", "float", "Reference"),  # case aside
        FieldSpec("TSThingExciterState:1", "float", "Exciter state 1"),  # indexed, suffix
        FieldSpec("Frequency", "float", "Hz"),  # [ts.save]
        FieldSpec("TSThingName", "str", "Model name"),  # text: never recorded
        FieldSpec("TSH", "float", "Inertia"),  # a parameter no flag records
        FieldSpec("TSThingPAirGap", "float", "Air gap MW"),  # a prefix, but no known suffix
    )
    mirror = ObjectSpec("Mirror", (FieldSpec("TSSaveAll", "str"),))
    catalog = ts_catalog([mirror, thing], THING_OVERRIDES)
    [group] = catalog.groups
    assert {c.attr: (c.name, c.flags) for c in group.fields} == {
        "P": ("TSThingP", ("TSSaveThingP",)),
        "PinPU": ("TSThingPinPU", ("TSSaveThingP",)),
        "MWREf": ("TSThingMWREf", ("TSSaveThingMWRef",)),
        "ExciterState": ("TSThingExciterState", ("TSSaveThingExciter",)),
        "Frequency": ("Frequency", ("TSSaveThingFreq",)),
    }
    assert next(c.doc for c in group.fields if c.attr == "ExciterState").startswith(
        "Indexed: ``TS.Thing.ExciterState[n]``."
    )
    assert catalog.unpaired == ("Thing.TSH", "Thing.TSThingPAirGap")
    assert catalog.recordable == ("Thing",)


@pytest.mark.parametrize(
    "save",
    [
        pytest.param({"Thing.Missing": ("TSSaveThingP",)}, id="unknown-field"),
        pytest.param({"Thing.TSThingP": ("TSSaveNope",)}, id="unknown-flag"),
        pytest.param({"Nowhere.X": ("TSSaveThingP",)}, id="unknown-type"),
    ],
)
def test_ts_catalog_rejects_bad_overrides(save: dict[str, tuple[str, ...]]):
    overrides = Overrides(ts_groups=THING_OVERRIDES.ts_groups, ts_save=tuple(save.items()))
    with pytest.raises(ValueError, match=r"\[ts\.save\]"):
        ts_catalog([_thing(FieldSpec("TSThingP", "float"))], overrides)


def test_identifiers_get_their_role():
    labelled = ObjectSpec("Thing", (FieldSpec("Num", "int", key=1), FieldSpec("Label", "str")))
    [thing] = Overrides(identifiers=frozenset({"Label"})).apply([labelled])
    text = render([thing], Overrides(), "test.tsv")[GRID / "_thing.py"]
    assert '    Label = Field("Label", str, I)' in text
    assert "from esapp.schema.roles import I, K" in text


def test_flags_get_the_yes_no_vocabulary():
    text = render([WIDGET], OVERRIDES, "test.tsv")[GRID / "_widget.py"]
    assert 'TSSaveWidgetSpeed = Field("TSSaveWidgetSpeed", str, E, vocab=("YES", "NO"))' in text


def test_rendered_files():
    files = render([WIDGET, ObjectSpec("Bare", ())], OVERRIDES, "test.tsv")
    assert sorted(map(str, files)) == sorted(
        map(
            str,
            [
                GRID / "_widget.py",
                GRID / "_bare.py",
                GRID / "__init__.py",
                GRID / "__init__.pyi",
                CATALOG,
            ],
        )
    )


def test_rendered_module_builds_the_schema():
    text = render([WIDGET], OVERRIDES, "test.tsv")[GRID / "_widget.py"]
    module = ModuleType("rendered")
    exec(compile(text, "rendered.py", "exec"), module.__dict__)
    widget = module.Widget
    assert widget.schema.key_names == ["WidgetNum", "WidgetNum:1"]
    assert [f.name for f in widget.schema.alt_key] == ["Name"]
    assert widget.Name.role == Role.ALT_KEY | Role.ENTERABLE | Role.EDIT_ONLY
    assert widget.Size.vocab == ("Big", "Small")
    assert widget.TSWidgetSpeed.role & Role.INDEXED
    assert "TSWidgetSpeed:1" in [f.name for f in widget.schema.fields]


@pytest.mark.parametrize(
    ("objects", "classes"),
    [
        pytest.param(["3WXFormer"], {"ThreeWXFormer": "_threewxformer"}, id="leading-3"),
        pytest.param(["Model Generic Object"], {"Model": "_model"}, id="spaces"),
        pytest.param(["AB", "Ab"], {"AB": "_ab", "Ab": "_ab_"}, id="case-collision"),
    ],
)
def test_class_and_module_names(objects: list[str], classes: dict[str, str]):
    files = render([ObjectSpec(name, ()) for name in objects], Overrides(), "test.tsv")
    stub = files[GRID / "__init__.pyi"]
    assert {line.split()[3]: line.split()[1][1:] for line in stub.splitlines()[1:]} == classes


def test_reserved_and_keyword_field_names():
    spec = ObjectSpec("Thing", (FieldSpec("schema", "str"), FieldSpec("from", "str")))
    text = render([spec], Overrides(), "test.tsv")[GRID / "_thing.py"]
    assert '    schema_ = Field("schema"' in text
    assert '    from_ = Field("from"' in text


def test_sync_writes_only_changes_and_removes_stale_modules(tmp_path: Path):
    files = render([WIDGET], OVERRIDES, "test.tsv")
    grid = tmp_path / GRID
    grid.mkdir(parents=True)
    (grid / "_gone.py").write_text("# GENERATED by tools/codegen from another.tsv. Do not edit.\n")
    (grid / "_handwritten.py").write_text("# not generated\n")

    preview = sync(files, tmp_path, check=True)
    assert set(preview.changed) == set(files)
    assert preview.stale == [GRID / "_gone.py"]
    assert not (tmp_path / CATALOG).exists()

    sync(files, tmp_path, check=False)
    assert not (grid / "_gone.py").exists()
    assert (grid / "_handwritten.py").exists()
    assert sync(files, tmp_path, check=True).changed == []

    (tmp_path / GRID / "_widget.py").write_text("edited\n")
    assert sync(files, tmp_path, check=True).changed == [PurePosixPath(GRID / "_widget.py")]


def test_audit_reports_irregular_key_positions():
    gap = ObjectSpec("Gap", (FieldSpec("A", "int", key=1), FieldSpec("B", "int", key=3)))
    assert audit([WIDGET, gap]) == ["Gap: key positions [1, 3]"]


def test_vocabulary_for_one_type():
    gadget = ObjectSpec("Gadget", (FieldSpec("Size", "str"),))
    overrides = Overrides(vocab=(("Gadget.Size", ("YES", "NO")),))
    files = render([WIDGET, gadget], overrides, "test.tsv")
    assert 'Size = Field("Size", str, 0, vocab=("YES", "NO"))' in files[GRID / "_gadget.py"]
    assert "vocab" not in files[GRID / "_widget.py"].split("Size = ")[1].splitlines()[0]


def test_vocabularies_load_beneath_the_overrides(tmp_path: Path):
    (tmp_path / "overrides.toml").write_text('[vocab]\nSize = ["Big", "Small"]\n')
    (tmp_path / "option_vocab.toml").write_text(
        '[vocab]\nSize = ["YES", "NO"]\n"Gadget.Flag" = ["YES", "NO"]\n'
    )
    overrides = Overrides.load(tmp_path / "overrides.toml", tmp_path / "option_vocab.toml")
    assert dict(overrides.vocab) == {"Size": ("Big", "Small"), "Gadget.Flag": ("YES", "NO")}


# ---------------------------------------------------------------------- SUBDATA
DOCUMENT = (
    "Contents\n"
    "SubData Sections ......................... 150\n"
    "   Widget ................................ 151\n"
    "      Points ............................. 151\n"
    "      Notes .............................. 151\n"
    "   OneLineDisplay ........................ 152\n"
    "      Picture ............................ 152\n"
    "\f   Gadget .............................. 153\n"
    "      Parts .............................. 153\n"
    "SCRIPT Section ........................... 200\n"
    "\n"
    "SubData Sections\n"
    "Widget\n"
    "Points\n"
    "Points hold a widget's corners. Each line is one.\n"
    "<SUBDATA Points>\n"
    "  // X   Y[m]\n"
    "     1.0 2.0\n"
    "</SUBDATA>\n"
    "Notes\n"
    "A widget's notes, one per line.\n"
    "OneLineDisplay\n"
    "Picture\n"
    "What a display draws.\n"
    "Gadget\n"
    "Parts\n"
    "The parts a gadget is made of.\n"
    "   Name    : the part's name\n"
    "   Count   : how many\n"
    "      Note : not a column\n"
    "   Example : not one either\n"
)


def test_subdata_extraction():
    rows = extract_subdata(DOCUMENT, ["Widget", "Gadget"])
    assert rows == [
        SubRow("Widget", "Points", ("X", "Y"), "Points hold a widget's corners."),
        SubRow("Widget", "Notes", (), "A widget's notes, one per line."),
        SubRow("Gadget", "Parts", ("Name", "Count"), "The parts a gadget is made of."),
    ]  # OneLineDisplay is not an object type: its Picture is left out


def test_subdata_overrides_and_table(tmp_path: Path):
    path = tmp_path / "subdata.toml"
    path.write_text(
        '[subdata."Widget.Notes"]\ncolumns = ["Text"]\n[subdata."Gadget.Parts"]\nskip = true\n'
    )
    rows = SubOverrides.load(path).apply(extract_subdata(DOCUMENT, ["Widget", "Gadget"]))
    assert [(r.name, r.columns) for r in rows] == [("Points", ("X", "Y")), ("Notes", ("Text",))]
    write_subdata_rows(rows, tmp_path / "subdata.tsv")
    assert read_subdata_rows(tmp_path / "subdata.tsv") == rows
    path.write_text('[subdata."Widget.Gone"]\nskip = true\n')
    with pytest.raises(ValueError, match="does not list"):
        SubOverrides.load(path).apply(rows)


def test_rendered_subdata():
    lists = [SubRow("Widget", "Points", ("X", "Y"), "Corners."), SubRow("Widget", "Size", (), "")]
    text = render([WIDGET], OVERRIDES, "test.tsv", lists)[GRID / "_widget.py"]
    assert '    Points = SubData("Points", ("X", "Y"))\n    """Corners."""' in text
    module = ModuleType("rendered")
    exec(compile(text, "rendered.py", "exec"), module.__dict__)
    widget = module.Widget
    assert widget.Size_.name == "Size"  # the field keeps the plain name
    assert [(s.attr, s.columns) for s in widget.schema.subdata] == [
        ("Points", ("X", "Y")),
        ("Size_", ()),
    ]
    assert repr(widget.Points) == "<SubData Widget.Points>"
    with pytest.raises(ValueError, match="schema lacks"):
        render([WIDGET], OVERRIDES, "test.tsv", [SubRow("Gone", "Points", (), "")])


# ---------------------------------------------------------------------- live sources
def test_export_writer_round_trips(tmp_path: Path):
    other = ObjectSpec("Other", (FieldSpec("Num", "int", "It's a number", key=1, alt_key=2),))
    for name in ("snapshot.tsv", "snapshot.tsv.gz"):
        write_export([WIDGET, other, ObjectSpec("Bare", ())], tmp_path / name)
        assert list(FieldExport(tmp_path / name).objects()) == [
            WIDGET,
            other,
            ObjectSpec("Bare", ()),
        ]


class FieldLists(FakeSimAuto):
    """A transport answering GetFieldList, GetParamsTypedCols and ProgramInformation."""

    LISTS: ClassVar[dict[str, list[tuple[str, ...]]]] = {
        "Widget": [
            ("*1*", "WidgetNum", "Integer", "Widget number", "Num", "Yes"),
            ("", "Spin", "Real", "Spin  rate", "Spin", "AUX/Paste"),
            ("", "Mode", "String", "Mode", "Mode", "Depends: Only for some widgets"),
            ("*A*", "Name", "String", "Name", "Name", "Edit Mode Only"),
        ],
        "Widget_Options": [
            ("", "Fast", "String", "", "", "Yes"),
            ("", "Color", "String", "", "", "Yes"),
            ("", "Level", "Integer", "", "", "Yes"),
        ],
    }

    def call(self, method: str, /, *args: object) -> object:
        if method == "GetFieldList":
            if (rows := self.LISTS.get(str(args[0]))) is None:
                return (f"GetFieldList: object type {args[0]} not found",)
            return ("", tuple(rows))
        if method == "GetParamsTypedCols":
            return ("", (("YES",), ("Blue",)))
        raise AssertionError(method)

    def get(self, prop: str, /) -> object:
        assert prop == "ProgramInformation"
        return (("version", 24, datetime(2025, 12, 11), "24"),)


def test_simauto_source():
    source = SimAutoSource(SimAuto(FieldLists()), ["Widget", "Missing"])
    [widget] = source.objects()
    assert source.unknown == ["Missing"]
    assert source.label == "SimAuto GetFieldList (Simulator 24 (2025-12-11))"
    assert widget.fields == (
        FieldSpec("WidgetNum", "int", "Widget number", key=1, enterable=True),
        FieldSpec("Spin", "float", "Spin rate", enterable=True),  # AUX/Paste takes SimAuto writes
        FieldSpec("Mode", "str", "Mode"),  # only sometimes enterable: not enterable
        FieldSpec("Name", "str", "Name", alt_key=1, enterable=True, edit_only=True),
    )


def test_option_vocabularies_come_from_yes_no_values():
    objects = list(SimAutoSource(SimAuto(FieldLists()), ["Widget", "Widget_Options"]).objects())
    assert option_vocab(SimAuto(FieldLists()), objects) == {"Widget_Options.Fast": ("YES", "NO")}


def test_drift_report():
    changed = ObjectSpec("Widget", (*WIDGET.fields[1:], FieldSpec("New", "str")))
    drift = compare([WIDGET, ObjectSpec("Gone", ())], [_with(changed, "Size", enterable=False)])
    assert drift.removed_types == ("Gone",)
    assert drift.added_fields == ("Widget.New",)
    assert drift.removed_fields == ("Widget.WidgetNum",)
    assert drift.changed == ("Widget.Size: enterable True -> False",)
    assert drift.report(examples=1)[0] == "1 types removed: Gone"
    assert not compare([WIDGET], [WIDGET])
    assert compare([WIDGET], [WIDGET]).report() == ["no differences"]


def _with(obj: ObjectSpec, name: str, **changes: object) -> ObjectSpec:
    return ObjectSpec(
        obj.name,
        tuple(replace(f, **changes) if f.name == name else f for f in obj.fields),  # pyright: ignore[reportArgumentType]
    )
