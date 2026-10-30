"""The runtime schema (Field, GObject, Schema) and the generated esapp.grid package."""

import subprocess
import sys

import numpy as np
import pytest

import esapp.grid as grid
from esapp.grid import Branch, Bus, Gen
from esapp.schema import Field, GObject, Role
from esapp.schema.roles import A, E, K, M, R, V


class Widget(
    GObject, pw="Widget", keys=("WidgetNum", "WidgetNum:1", "Tag"), alt_key=("Name", "Tag")
):
    WidgetNum = Field("WidgetNum", int, K | E, {1: K | E, 2: (E, str)})
    Tag = Field("Tag", str, K | A | E)
    Name = Field("Name", str, A | E)
    Size = Field("Size", float, R | E | M)
    Status = Field("Status", str, E, vocab=("Closed", "Open"))
    Input = Field("Input", float, V, {1: 0, 2: E})


class Options(GObject, pw="Widget_Options"):
    MaxIter = Field("MaxIter", int, E)


class TestField:
    def test_knows_owner_name_and_dtype(self):
        assert Widget.Size.owner is Widget
        assert (Widget.Size.attr, str(Widget.Size), Widget.Size.dtype) == ("Size", "Size", float)
        assert repr(Widget.Size) == "<Field Widget.Size: float>"
        assert Widget.Size.role == Role.REQUIRED | Role.ENTERABLE | Role.EDIT_ONLY

    def test_indexed_variant(self):
        variant = Widget.WidgetNum[1]
        assert variant is Widget.WidgetNum[1]
        assert (variant.name, variant.attr, variant.dtype) == ("WidgetNum:1", "WidgetNum", int)
        assert variant.role == K | E

    def test_variant_can_have_its_own_dtype(self):
        assert (Widget.WidgetNum[2].dtype, Widget.WidgetNum[2].role) == (str, E)

    def test_unlisted_variant_inherits_all_but_key_roles(self):
        extra = Widget.WidgetNum[7]
        assert (extra.dtype, extra.role) == (int, E)

    def test_variants_share_the_bool_vocabulary(self):
        assert Widget.Status[1].vocab == ("Closed", "Open")

    def test_unknown_name_is_an_untyped_field(self):
        unknown = Widget.schema.field("NotInSchema")
        assert (unknown.dtype, unknown.role, unknown.owner) == (object, Role.NONE, Widget)
        assert Widget.schema.field("NotInSchema") is unknown


class TestSchema:
    def test_keys_in_powerworld_order(self):
        assert Widget.schema.key_names == ["WidgetNum", "WidgetNum:1", "Tag"]
        assert [f.name for f in Widget.schema.alt_key] == ["Name", "Tag"]
        assert Widget.schema.key_sets == (
            frozenset({"WidgetNum", "WidgetNum:1", "Tag"}),
            frozenset({"Name", "Tag"}),
        )

    def test_fields_expand_variants_but_not_indexed_only_bases(self):
        assert [f.name for f in Widget.schema.fields] == [
            "WidgetNum", "WidgetNum:1", "WidgetNum:2", "Tag", "Name", "Size", "Status",
            "Input:1", "Input:2",
        ]  # fmt: skip

    def test_keyless_type(self):
        assert Options.schema.keys == ()
        assert Options.schema.key_sets == ()


class TestReferences:
    def test_by_key(self):
        ref = Widget(5, 1, "a")
        assert ref.key == (5, 1, "a")
        assert str(ref) == '[WIDGET 5 1 "a"]'
        assert repr(ref) == "Widget(5, 1, 'a')"

    def test_wrong_number_of_keys(self):
        with pytest.raises(TypeError, match=r"Widget\(WidgetNum, WidgetNum:1, Tag\) takes 3"):
            Widget(5)

    def test_value_semantics(self):
        assert Widget(5, 1, "a") == Widget(5, 1, "a")
        assert len({Widget(5, 1, "a"), Widget(5, 1, "a"), Widget(6, 1, "a")}) == 2
        assert Widget(5, 1, "a") != (5, 1, "a")

    def test_keys_as_written(self):
        read = Gen(np.int64(4131), "H ")  # as a frame hands them over: NumPy, padded
        assert read == Gen(4131, "H")
        assert read.key == (4131, "H")
        assert type(read.key[0]) is int

    @pytest.mark.parametrize(
        ("text", "ref"),
        [
            pytest.param("Gen '2' '1'", Gen(2, "1"), id="whoami"),
            pytest.param("GEN 2 1", Gen(2, "1"), id="element"),
            pytest.param("BRANCH 1 5 '1'", Branch(1, 5, "1"), id="quoted-key"),
            pytest.param("Bus '7'", Bus(7), id="one-key"),
        ],
    )
    def test_parse_either_spelling(self, text: str, ref: GObject):
        assert GObject.parse(text) == ref
        assert type(ref).parse(text) == ref

    @pytest.mark.parametrize("text", ["Nothing '1'", "Gen '2'", "", "BRANCH 1 5"])
    def test_parse_rejects(self, text: str):
        with pytest.raises(ValueError, match=r"name|key value"):
            GObject.parse(text)

    def test_parse_through_a_type_takes_only_that_type(self):
        with pytest.raises(ValueError, match="a Gen"):
            Gen.parse("Bus '7'")


class TestGeneratedGrid:
    def test_importing_the_package_loads_no_types(self):
        code = (
            "import sys, esapp.grid; print(sum(m.startswith('esapp.grid._') for m in sys.modules))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        assert out.stdout.strip() == "0"

    def test_every_type_loads_under_its_name(self):
        for name in grid.__all__:
            gtype = getattr(grid, name)
            assert gtype.__name__ == name
            assert issubclass(gtype, GObject)

    def test_unknown_type(self):
        with pytest.raises(AttributeError, match="NotAType"):
            grid.NotAType  # noqa: B018

    def test_keys_follow_powerworld_key_symbols(self):
        assert Branch.schema.key_names == ["BusNum", "BusNum:1", "LineCircuit"]
        assert [f.name for f in Branch.schema.alt_key] == [
            "BusName_NomVolt", "BusName_NomVolt:1", "LineCircuit",
        ]  # fmt: skip
        assert Gen.schema.key_names == ["BusNum", "GenID"]

    def test_vocabulary_and_indexed_only_fields(self):
        assert Gen.GenStatus.vocab == ("Closed", "Open")
        assert Bus.TSBusInput.role & Role.INDEXED
        assert Bus.TSBusInput[3].name == "TSBusInput:3"
