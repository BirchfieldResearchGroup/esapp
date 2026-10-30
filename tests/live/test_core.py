"""The core against a live SimAuto session (run with ESAPP_TEST_CASE set)."""

import pandas as pd
import pytest

from esapp import ObjectNotFound, PowerWorld, ReadOnlySessionError, SchemaWarning
from esapp.grid import Branch, Bus, Filter, Gen, Load, Sim_Solution_Options
from esapp.schema import GObject
from esapp.testing import powerworld


@powerworld
class TestReads:
    def test_keys(self, pw: PowerWorld):
        df = pw[Bus]
        assert list(df.columns) == ["BusNum"]
        assert df["BusNum"].dtype == "int64"
        assert len(df) > 0
        assert list(pw[Branch].columns) == ["BusNum", "BusNum:1", "LineCircuit"]

    def test_fields_in_request_order_with_schema_dtypes(self, pw: PowerWorld):
        df = pw[Bus, [Bus.BusPUVolt, Bus.BusName]]
        assert list(df.columns) == ["BusNum", "BusPUVolt", "BusName"]
        assert df["BusPUVolt"].dtype == "float64"

    def test_field_series(self, pw: PowerWorld):
        volts = pw[Bus.BusPUVolt]
        assert volts.index.names == ["BusNum"]
        assert volts.between(0.5, 1.5).all()

    def test_every_field(self, pw: PowerWorld):
        df = pw[Gen, :]
        assert {"BusNum", "GenID", "GenMW", "GenStatus"} <= set(df.columns)

    def test_one_object(self, pw: PowerWorld):
        first = pw.refs(Bus)[0]
        row = pw[first, Bus.BusPUVolt]
        assert len(row) == 1
        assert row["BusNum"].iloc[0] == first.key[0]
        assert row["BusPUVolt"].iloc[0] == pytest.approx(pw[Bus.BusPUVolt].loc[first.key[0]])

    @pytest.mark.parametrize("gtype", [Bus, Gen, Load, Branch])
    def test_one_object_every_field(self, pw: PowerWorld, gtype: type[GObject]):
        ref = pw.refs(gtype)[-1]
        row = pw[ref, :]
        names = [f.name for f in gtype.schema.fields]
        assert list(row.columns) == list(dict.fromkeys([*gtype.schema.key_names, *names]))
        # Identifiers (Label, ObjectID) come from a bulk read; they must agree with one.
        ids = ["Label", "ObjectID"]
        bulk = pw[gtype, ids]
        mine = bulk[(bulk[gtype.schema.key_names] == list(ref.key)).all(axis=1)]
        assert row[ids].values.tolist() == mine[ids].values.tolist()

    def test_keyless_type(self, pw: PowerWorld):
        assert pw[Sim_Solution_Options.MaxItr].iloc[0] > 0

    def test_keys_come_back_as_written(self, pw: PowerWorld):
        ids = pw[Gen]["GenID"]
        assert (ids == ids.str.strip()).all()  # Simulator pads IDs; reads do not
        first = pw.refs(Gen)[0]
        assert Gen(*pw[Gen].iloc[0]) == first
        assert pw[Gen(first.key[0], f"{first.key[1]} "), Gen.GenMW].shape[0] == 1  # either way

    def test_writes_are_blocked(self, pw: PowerWorld):
        with pytest.raises(ReadOnlySessionError):
            pw[Gen, Gen.GenMW] = 0.0

    def test_missing_object(self, pw: PowerWorld):
        with pytest.raises(ObjectNotFound):
            pw[Bus(999_999), Bus.BusPUVolt]

    def test_unknown_field_warns(self, pw: PowerWorld):
        with pytest.warns(SchemaWarning, match="DefinitelyNotAField"):
            df = pw[Bus, "DefinitelyNotAField"]
        assert (df["DefinitelyNotAField"] == 0).all()  # PowerWorld's placeholder, not an error


@powerworld(isolation="state")
class TestWrites:
    def test_number_broadcast(self, pw: PowerWorld):
        pw[Gen, Gen.GenMW] = 12.5
        df = pw[Gen, [Gen.GenMW, Gen.GenStatus]]
        in_service = df["GenStatus"] == "Closed"
        assert (df.loc[in_service, "GenMW"] == 12.5).all()
        assert (df.loc[~in_service, "GenMW"] == 0).all()  # PowerWorld zeroes open units

    def test_series_round_trip(self, pw: PowerWorld):
        before = pw[Load.LoadSMW]
        pw[Load.LoadSMW] = before * 1.1
        pd.testing.assert_series_equal(pw[Load.LoadSMW], before * 1.1)

    def test_bools_use_the_vocabulary(self, pw: PowerWorld):
        pw[Gen, Gen.GenStatus] = False
        assert set(pw[Gen.GenStatus]) == {"Open"}

    def test_frame_update_never_creates(self, pw: PowerWorld):
        missing = int(pw[Bus]["BusNum"].max()) + 1000
        with pytest.raises(ObjectNotFound):
            pw[Bus] = pd.DataFrame({"BusNum": [missing], "BusName": ["nope"]})


@powerworld
class TestSelections:
    """Conditions select inside Simulator, through temporary filter objects when needed."""

    def test_one_condition(self, pw: PowerWorld):
        kv = pw[Bus.BusNomVolt]
        threshold = float(kv.median())
        picked = pw[Bus, Bus.BusNomVolt, Bus.BusNomVolt >= threshold]["BusNum"].tolist()
        assert picked == kv.index[kv >= threshold].tolist()

    def test_compound_and_text(self, pw: PowerWorld):
        df = pw[Bus, [Bus.BusNomVolt, Bus.BusName]]
        prefix = str(df["BusName"].iloc[0])[:2]
        kv = float(df["BusNomVolt"].min())
        where = (Bus.BusNomVolt > kv) | Bus.BusName.startswith(prefix)
        picked = pw[Bus, Bus.BusName, where]["BusNum"].tolist()
        expected = df[(df["BusNomVolt"] > kv) | df["BusName"].str.startswith(prefix)]
        assert picked == expected["BusNum"].tolist()
        assert not any(str(n).startswith("esapp~") for n in pw[Filter]["FilterName"])  # cleaned up

    def test_one_object_through_its_device_filter(self, pw: PowerWorld):
        gen = pw.refs(Gen)[-1]
        assert pw[Gen, Gen.GenMW, gen][Gen.schema.key_names].values.tolist() == [list(gen.key)]


@powerworld(isolation="state")
class TestTargetedWrites:
    def test_one_object(self, pw: PowerWorld):
        bus = pw.refs(Bus)[-1]
        pw[bus, Bus.BusName] = "esapp one"
        names = pw[Bus.BusName]
        assert names.loc[bus.key[0]] == "esapp one"
        assert (names == "esapp one").sum() == 1

    def test_through_a_selection(self, pw: PowerWorld):
        numbers = pw[Bus]["BusNum"].tolist()
        pw[Bus, [Bus.BusName], Bus.BusNum <= numbers[2]] = ["esapp some"]
        names = pw[Bus.BusName]
        assert names[names == "esapp some"].index.tolist() == [
            n for n in numbers if n <= numbers[2]
        ]

    def test_write_by_label(self, pw: PowerWorld):
        bus = pw.refs(Bus)[0]
        pw[bus, Bus.LabelAppend[1]] = "esapp_label"  # a label is set by appending one
        assert "esapp_label" in str(pw[bus, Bus.Label]["Label"].iloc[0])
        pw[Bus] = pd.DataFrame({Bus.Label: ["esapp_label"], Bus.BusName: ["by label"]})
        assert pw[Bus.BusName].loc[bus.key[0]] == "by label"

    def test_options_are_put_back(self, pw: PowerWorld):
        before = int(pw[Sim_Solution_Options.MaxItr].iloc[0])
        with pw.options({Sim_Solution_Options.MaxItr: before + 7}):
            assert pw[Sim_Solution_Options.MaxItr].iloc[0] == before + 7
        assert pw[Sim_Solution_Options.MaxItr].iloc[0] == before


@powerworld(isolation="fresh")
def test_subdata_round_trip(pw: PowerWorld):
    gen = pw.refs(Gen)[0]
    curve = pd.DataFrame({"MW": [0.0, 10.0, 20.0], "Price": [5.0, 7.5, 12.0]})
    pw[gen, Gen.BidCurve] = curve
    back = pw[gen, Gen.BidCurve]
    assert back["point"].tolist() == [0, 1, 2]
    assert back[["MW", "Price"]].to_numpy().tolist() == curve.to_numpy().tolist()
    everyone = pw[Gen, Gen.BidCurve]
    assert set(everyone.columns[:2]) == set(Gen.schema.key_names)
    pw[gen, Gen.BidCurve] = []
    assert pw[gen, Gen.BidCurve].empty


@powerworld(isolation="fresh")
def test_delete_through_a_selection(pw: PowerWorld):
    gens = pw.refs(Gen)
    pw.delete(Gen, gens[0])
    assert pw.refs(Gen) == gens[1:]


@powerworld(isolation="fresh")
def test_create(pw: PowerWorld):
    template = pw[Bus, [Bus.AreaNum, Bus.ZoneNum, Bus.BusNomVolt]].iloc[0]
    number = int(pw[Bus]["BusNum"].max()) + 1000
    new = pd.DataFrame(
        {
            Bus.BusNum: [number],
            Bus.BusName: ["esapp_new"],
            Bus.BusNomVolt: [template["BusNomVolt"]],
            Bus.AreaNum: [template["AreaNum"]],
            Bus.ZoneNum: [template["ZoneNum"]],
        }
    )
    pw.create(Bus, new)
    assert pw[Bus(number), Bus.BusName]["BusName"].iloc[0] == "esapp_new"
