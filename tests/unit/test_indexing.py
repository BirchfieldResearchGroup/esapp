"""``pw[...]`` reads and writes, end to end over FakeSimAuto."""

from typing import NamedTuple

import numpy as np
import pandas as pd
import pytest

from esapp import (
    Kw,
    ObjectNotFound,
    PowerWorld,
    PowerWorldError,
    ReadOnlySessionError,
    SchemaWarning,
)
from esapp.grid import Bus, Condition, Filter, Gen, Load, Shunt, Sim_Solution_Options
from esapp.schema import Field, GObject, Role
from esapp.schema.roles import E, K, M
from esapp.simauto import VT_BSTR, VT_I4, VT_R8
from esapp.testing import FakeSimAuto


class Widget(GObject, pw="Widget", keys=("WidgetNum",)):
    WidgetNum = Field("WidgetNum", int, K)
    Size = Field("Size", float, E | M)


@pytest.fixture
def pw(fake_pw: PowerWorld, fake_simauto: FakeSimAuto) -> PowerWorld:
    fake_simauto.add(
        Bus,
        {
            "BusNum": [1, 2, 3],
            "BusName": ["One", "Two", "Three"],
            "BusPUVolt": [1.0, 0.98, 1.02],
            "AreaNum": [1, 1, 2],
        },
    )
    fake_simauto.add(
        Gen,
        {"BusNum": [1, 3], "GenID": ["1", "1"], "GenMW": [50.0, 75.0], "GenStatus": ["Closed"] * 2},
    )
    fake_simauto.add(Load, {"BusNum": [2, 3], "LoadID": ["1", "1"], "LoadSMW": [10.0, 20.0]})
    fake_simauto.add(Sim_Solution_Options, {"MaxItr": [250]})
    fake_simauto.add(Widget, {"WidgetNum": [1], "Size": [1.0]})
    return fake_pw


class Read(NamedTuple):
    fields: list[str]
    where: str
    types: list[int]


def last_read(fake: FakeSimAuto) -> Read:
    _, args = next(c for c in reversed(fake.calls) if c[0] == "GetParamsTypedCols")
    return Read(args[1].items, args[2], args[3].items)  # pyright: ignore[reportAttributeAccessIssue]


def table(fake: FakeSimAuto, gtype: type[GObject]) -> pd.DataFrame:
    return fake.tables[gtype.schema.type]


class TestRead:
    def test_type_alone_gives_keys(self, pw: PowerWorld):
        df = pw[Bus]
        assert list(df.columns) == ["BusNum"]
        assert df["BusNum"].dtype == "int64"
        assert df["BusNum"].tolist() == [1, 2, 3]

    def test_keys_first_then_request_order(self, pw: PowerWorld):
        df = pw[Bus, [Bus.BusPUVolt, "BusName"]]
        assert list(df.columns) == ["BusNum", "BusPUVolt", "BusName"]
        assert df["BusPUVolt"].dtype == "float64"

    def test_single_field_by_name_or_constant(self, pw: PowerWorld):
        pd.testing.assert_frame_equal(pw[Bus, "BusPUVolt"], pw[Bus, Bus.BusPUVolt])

    def test_duplicates_and_keys_are_requested_once(
        self, pw: PowerWorld, fake_simauto: FakeSimAuto
    ):
        pw[Bus, [Bus.BusName, Bus.BusNum, "BusName"]]
        assert last_read(fake_simauto).fields == ["BusNum", "BusName"]

    def test_every_field(self, pw: PowerWorld):
        df = pw[Bus, :]
        assert list(df.columns) == [f.name for f in Bus.schema.fields]  # BusNum leads
        assert df["BusPUVolt"].tolist() == [1.0, 0.98, 1.02]

    def test_unknown_field_warns(self, pw: PowerWorld):
        with pytest.warns(SchemaWarning, match="no field.*BusPUVlot"):
            df = pw[Bus, "BusPUVlot"]
        assert df["BusPUVlot"].tolist() == [0.0, 0.0, 0.0]  # PowerWorld's placeholder

    def test_named_filter(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        fake_simauto.add(
            Filter, {"ObjectType": ["Bus"], "FilterName": ["AreaOne"], "FilterLogic": ["AND"]}
        )
        fake_simauto.add(
            Condition,
            {"ObjectType": ["Bus"], "FilterName": ["AreaOne"], "ConditionNumber": [1],
             "VariableName": ["AreaNum"], "ConditionType": ["="], "ConditionValue": ["1"]},
        )  # fmt: skip
        assert pw[Bus, Bus.BusPUVolt, "AreaOne"]["BusNum"].tolist() == [1, 2]
        assert last_read(fake_simauto).where == "AreaOne"
        with pytest.raises(ObjectNotFound, match="cannot be found"):
            pw[Bus, Bus.BusPUVolt, "NoSuchFilter"]

    def test_typed_columns(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        df = pw[Bus, [Bus.BusPUVolt, Bus.BusName, Bus.AreaNum]]
        assert [str(t) for t in df.dtypes] == ["int64", "float64", "str", "int64"]
        assert last_read(fake_simauto).types == [VT_I4, VT_R8, VT_BSTR, VT_I4]

    def test_field_alone_gives_a_series_on_the_keys(self, pw: PowerWorld):
        volts = pw[Bus.BusPUVolt]
        assert volts.index.names == ["BusNum"]
        assert volts.loc[2] == 0.98
        assert pw[Gen.GenMW].index.names == ["BusNum", "GenID"]
        assert pw[Bus.BusNum].tolist() == [1, 2, 3]

    def test_one_object(self, pw: PowerWorld):
        row = pw[Bus(2), Bus.BusPUVolt]
        assert row.to_dict("records") == [{"BusNum": 2, "BusPUVolt": 0.98}]
        assert list(pw[Bus(2)].columns) == ["BusNum"]

    def test_one_object_is_one_read_through_a_device_filter(
        self, pw: PowerWorld, fake_simauto: FakeSimAuto
    ):
        table(fake_simauto, Bus)["Label"] = ["", "North", ""]
        row = pw[Bus(2), [Bus.BusName, Bus.Label, Bus.BusPUVolt]]
        assert row.to_dict("records") == [
            {"BusNum": 2, "BusName": "Two", "Label": "North", "BusPUVolt": 0.98}
        ]
        [(method, args)] = fake_simauto.calls
        assert (method, args[2]) == ("GetParamsTypedCols", "<DEVICE>Bus '2'")

    def test_one_object_every_field(self, pw: PowerWorld):
        row = pw[Gen(3, "1"), :]
        assert list(row.columns) == [f.name for f in Gen.schema.fields]
        assert row["GenMW"].tolist() == [75.0]

    def test_keyless_object(self, pw: PowerWorld):
        assert pw[Sim_Solution_Options(), Sim_Solution_Options.MaxItr]["MaxItr"].tolist() == [250]

    @pytest.mark.parametrize("fields", [[Bus.BusName, Bus.Label], [Bus.Label]])
    def test_one_missing_object(self, pw: PowerWorld, fields: list[Field[str]]):
        with pytest.raises(ObjectNotFound):
            pw[Bus(9), fields]

    def test_no_objects_gives_an_empty_typed_frame(self, pw: PowerWorld):
        df = pw[Shunt, Shunt.SSNMW]
        assert df.empty
        assert list(df.columns) == ["BusNum", "ShuntID", "SSNMW"]
        assert (df["BusNum"].dtype, df["SSNMW"].dtype) == ("int64", "float64")

    def test_keyless_type(self, pw: PowerWorld):
        assert pw[Sim_Solution_Options.MaxItr].tolist() == [250]

    @pytest.mark.parametrize(
        ("index", "error", "message"),
        [
            pytest.param((Bus, slice(0, 2)), ValueError, "only", id="bounded-slice"),
            pytest.param((Bus, Gen.GenMW), ValueError, "not a field of Bus", id="foreign-field"),
            pytest.param((Bus, Bus.TSBusInput), ValueError, r"TSBusInput\[n\]", id="indexed-only"),
            pytest.param((Bus, [3]), TypeError, "names or Field", id="bad-field"),
            pytest.param("Bus", TypeError, "unsupported index", id="string-type"),
        ],
    )
    def test_bad_index(self, pw: PowerWorld, index: object, error: type[Exception], message: str):
        with pytest.raises(error, match=message):
            pw[index]  # pyright: ignore[reportArgumentType, reportCallIssue]


class TestWrite:
    def test_number_broadcast_is_one_setdata(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        pw[Gen, Gen.GenMW] = 100.0
        pw[Gen, [Gen.GenMWMax, Gen.GenMWMin]] = [200, 0]
        assert fake_simauto.scripts == [
            "SetData(Gen, [GenMW], [100.0], ALL);",
            "SetData(Gen, [GenMWMax, GenMWMin], [200, 0], ALL);",
        ]
        assert not any(m == "ChangeParametersMultipleElementRect" for m, _ in fake_simauto.calls)

    def test_series_round_trip(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        pw[Load.LoadSMW] = pw[Load.LoadSMW] * 2
        assert table(fake_simauto, Load)["LoadSMW"].tolist() == [20.0, 40.0]

    def test_series_must_be_indexed_by_the_keys(self, pw: PowerWorld):
        with pytest.raises(ValueError, match="indexed by Load's keys"):
            pw[Load.LoadSMW] = pd.Series([1.0, 2.0])

    def test_one_value_per_object(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        pw[Bus, Bus.BusName] = ["A", "B", "C"]
        assert table(fake_simauto, Bus)["BusName"].tolist() == ["A", "B", "C"]

    def test_frame_update(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        pw[Bus] = pd.DataFrame({Bus.BusNum: [2], "BusName": ["Renamed"]})
        assert table(fake_simauto, Bus)["BusName"].tolist() == ["One", "Renamed", "Three"]

    def test_frame_update_never_creates(self, pw: PowerWorld):
        with pytest.raises(ObjectNotFound) as caught:
            pw[Bus] = pd.DataFrame({"BusNum": [99], "BusName": ["New"]})
        assert "pw.create(Bus, df)" in caught.value.__notes__[0]

    def test_bools_use_the_field_vocabulary(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        pw[Gen, Gen.GenStatus] = False
        assert table(fake_simauto, Gen)["GenStatus"].tolist() == ["Open", "Open"]
        pw[Gen] = pd.DataFrame({"BusNum": [1, 3], "GenID": ["1", "1"], "GenStatus": [True, "Open"]})
        assert table(fake_simauto, Gen)["GenStatus"].tolist() == ["Closed", "Open"]

    def test_bool_without_vocabulary_raises(self, pw: PowerWorld):
        with pytest.raises(ValueError, match=r"no bool vocabulary for Bus\.BusName"):
            pw[Bus, Bus.BusName] = True

    def test_read_only_field_warns_but_is_sent(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        with pytest.warns(SchemaWarning, match=r"\['GenMVR'\] are read-only") as record:
            pw[Gen, Gen.GenMVR] = 1.0
        assert record[0].filename == __file__  # attributed to the caller, not to esapp
        assert fake_simauto.scripts == ["SetData(Gen, [GenMVR], [1.0], ALL);"]

    def test_edit_mode_hint(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        fake_simauto.errors["RunScriptCommand"] = "SetData: cannot change Size in RUN mode"
        with pytest.raises(PowerWorldError) as caught:
            pw[Widget, Widget.Size] = 2.0
        assert "with pw.edit_mode()" in caught.value.__notes__[0]

    def test_keyless_type(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        pw[Sim_Solution_Options.MaxItr] = 100
        assert table(fake_simauto, Sim_Solution_Options)["MaxItr"].tolist() == [100]
        with pytest.raises(ValueError, match="one value per field"):
            pw[Sim_Solution_Options, ["MaxItr", "SBase"]] = 5

    def test_no_objects_means_nothing_to_write(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        pw[Shunt, "ShuntID"] = np.array([], dtype=object)
        assert not any(m.startswith("Change") for m, _ in fake_simauto.calls)

    def test_frame_required(self, pw: PowerWorld):
        with pytest.raises(TypeError, match="takes a DataFrame"):
            pw[Bus] = [1, 2, 3]  # pyright: ignore[reportArgumentType]

    def test_read_only(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        with pw.read_only(), pytest.raises(ReadOnlySessionError):
            pw[Gen, Gen.GenMW] = 1.0
        assert fake_simauto.scripts == []

    def test_one_object(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        pw[Gen(3, "1"), Gen.GenMW] = 80.0
        pw[Gen(3, "1"), [Gen.GenMWMax, Gen.GenStatus]] = [90.0, False]
        assert fake_simauto.scripts == [
            "SetData(Gen, [GenMW], [80.0], \"<DEVICE>Gen '3' '1'\");",
            "SetData(Gen, [GenMWMax, GenStatus], [90.0, \"Open\"], \"<DEVICE>Gen '3' '1'\");",
        ]
        assert table(fake_simauto, Gen)["GenMW"].tolist() == [50.0, 80.0]
        with pytest.raises(ObjectNotFound):
            pw[Gen(9, "9"), Gen.GenMW] = 1.0

    def test_through_a_selection(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        pw[Gen, Gen.GenMW, Gen.GenMW > 60] = 60.0
        assert fake_simauto.scripts == ['SetData(Gen, [GenMW], [60.0], "GenMW > 60");']
        assert table(fake_simauto, Gen)["GenMW"].tolist() == [50.0, 60.0]

    def test_one_item_list_for_one_field(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        pw[Bus, [Bus.BusName]] = ["Same"]
        assert table(fake_simauto, Bus)["BusName"].tolist() == ["Same"] * 3
        pw[Bus(2), [Bus.BusName]] = ["Two"]  # an object
        pw[Bus, [Bus.BusName], Bus.BusNum > 2] = ("Three",)  # a selection
        assert table(fake_simauto, Bus)["BusName"].tolist() == ["Same", "Two", "Three"]
        assert fake_simauto.scripts[-1] == 'SetData(Bus, [BusName], ["Three"], "BusNum > 2");'

    def test_write_by_label(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        table(fake_simauto, Bus)["Label"] = ["", "North", ""]
        pw[Bus] = pd.DataFrame({Bus.Label: ["North"], Bus.BusName: ["By label"]})
        assert table(fake_simauto, Bus)["BusName"].tolist() == ["One", "By label", "Three"]

    def test_label_beside_the_key_raises(self, pw: PowerWorld):
        with pytest.raises(ValueError, match=r"LabelAppend\[1\]"):
            pw[Bus] = pd.DataFrame({Bus.BusNum: [2], Bus.Label: ["North"]})


class TestSelect:
    def test_one_condition_is_inline(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        assert pw[Bus, Bus.BusPUVolt, Bus.BusPUVolt >= 1.0]["BusNum"].tolist() == [1, 3]
        assert last_read(fake_simauto).where == "BusPUVolt >= 1.0"
        assert pw.refs(Bus, Bus.AreaNum.between(2, 3)) == [Bus(3)]

    def test_compound_conditions_use_a_filter_for_the_call(
        self, pw: PowerWorld, fake_simauto: FakeSimAuto
    ):
        where = Bus.AreaNum.eq(1) & (Bus.BusPUVolt < 1.0)
        assert pw[Bus, Bus.BusName, where]["BusName"].tolist() == ["Two"]
        assert last_read(fake_simauto).where == "esapp~f1"
        assert table(fake_simauto, Filter).empty
        assert table(fake_simauto, Condition).empty

    def test_text_values_go_through_a_filter(self, pw: PowerWorld):
        assert pw.refs(Bus, Bus.BusName.eq("Three")) == [Bus(3)]
        assert pw.refs(Bus, Bus.BusName.startswith("T")) == [Bus(2), Bus(3)]

    def test_nested_groups(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        where = (Bus.BusNum.eq(1) | Bus.BusNum.eq(3)) & (Bus.AreaNum.eq(2))
        assert pw.refs(Bus, where) == [Bus(3)]
        assert table(fake_simauto, Filter).empty

    def test_a_filter_kept_for_a_block(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        with pw.filter(Bus.AreaNum.eq(1) & (Bus.BusNum > 1)) as name:
            assert pw.refs(Bus, name) == [Bus(2)]
            assert pw[Bus, Bus.BusName, name]["BusName"].tolist() == ["Two"]
            assert len(table(fake_simauto, Filter)) == 1
        assert table(fake_simauto, Filter).empty

    def test_conditions_cannot_mix_types(self):
        with pytest.raises(TypeError, match="different object types"):
            _ = Bus.AreaNum.eq(1) & Gen.GenMW.eq(1)

    def test_selected(self, pw: PowerWorld):
        pw.select(Bus, Bus.BusNum.eq(3))
        assert pw.refs(Bus, Kw.SELECTED) == [Bus(3)]
        pw.unselect(Bus)
        assert pw.refs(Bus, Kw.SELECTED) == []

    def test_delete(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        pw.delete(Gen, Gen.GenMW > 60)
        assert pw.refs(Gen) == [Gen(1, "1")]
        pw.delete(Gen, Kw.ALL)
        assert pw.refs(Gen) == []
        assert fake_simauto.scripts[-3:] == ["EnterMode(EDIT);", "Delete(Gen);", "EnterMode(RUN);"]

    def test_not_a_selection(self, pw: PowerWorld):
        with pytest.raises(TypeError, match="a selection is"):
            pw[Bus, Bus.BusName, 5]  # pyright: ignore[reportArgumentType, reportCallIssue]


class TestNonScalar:
    def test_a_new_one_is_learned_once(self, pw: PowerWorld, fake_simauto: FakeSimAuto):
        fake_simauto.nonscalar.add(("Bus", "AreaNum"))
        with pytest.warns(SchemaWarning, match=r"Bus\.AreaNum is not a scalar"):
            df = pw[Bus, Bus.AreaNum]
        assert df["AreaNum"].tolist() == [1, 1, 2]
        pw[Bus, Bus.AreaNum]  # learned: no warning, one call
        assert last_read(fake_simauto).types == [VT_I4, 12]

    def test_schema_marks_known_ones(self):
        assert Bus.LabelAppend[2].role & Role.NONSCALAR
