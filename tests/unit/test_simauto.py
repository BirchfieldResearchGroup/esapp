"""The 1:1 SimAuto API: envelope unwrapping, error mapping, argument marshalling."""

import sys

import pytest

from esapp.errors import (
    AddonNotLicensed,
    NotConverged,
    NotRetrievable,
    ObjectNotFound,
    PowerWorldError,
    PrerequisiteError,
    SimAutoUnavailable,
    SimulatorFault,
    UnknownCommand,
)
from esapp.simauto import Array, ComTransport, SimAuto, unwrap
from esapp.testing import FakeSimAuto


class Recorder(FakeSimAuto):
    """A transport that records calls and returns one canned reply."""

    def __init__(self, reply: object = ("",)) -> None:
        super().__init__()
        self.reply = reply

    def call(self, method: str, /, *args: object) -> object:
        self.calls.append((method, args))
        return self.reply


@pytest.mark.parametrize(
    ("out", "result"),
    [
        pytest.param(("",), None, id="ok"),
        pytest.param(("", 5), 5, id="one-result"),
        pytest.param(("", 1, 2), (1, 2), id="several-results"),
        pytest.param(("No data returned",), None, id="no-data"),
        pytest.param(("GetParamsRectTyped: No data", None), None, id="no-data-with-result"),
        pytest.param(None, None, id="none"),
        pytest.param(7, 7, id="bare-int"),
    ],
)
def test_unwrap(out: object, result: object):
    assert unwrap("Method", out) == result


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        pytest.param("Get: Bus 99 not found", ObjectNotFound, id="not-found"),
        pytest.param("Get: field could not be found", ObjectNotFound, id="could-not-be-found"),
        pytest.param("CTGSolve: no active contingencies", PrerequisiteError, id="prerequisite"),
        pytest.param("Calc: TransLineCalc is not registered", AddonNotLicensed, id="addon"),
        pytest.param(
            "Get: cannot be retrieved through SimAuto", NotRetrievable, id="not-retrievable"
        ),
        pytest.param(
            "NR PowerFlow - Exceeded maximum number of iterations", NotConverged, id="not-converged"
        ),
        pytest.param('Unknown script command "X".', UnknownCommand, id="unknown-command"),
        pytest.param(
            "Filter \"<DEVICE>Gen '2' '9'\" cannot be found.", ObjectNotFound, id="device-filter"
        ),
        pytest.param(
            "Exception: Access violation at address 0447C023", SimulatorFault, id="access-violation"
        ),
        pytest.param("No area/superarea constraints set.", PrerequisiteError, id="opf-areas"),
        pytest.param("cannot solve OPF with Island-based AGC", PrerequisiteError, id="opf-agc"),
        pytest.param("Something else went wrong", PowerWorldError, id="generic"),
    ],
)
def test_errors_map_to_specific_types(message: str, kind: type[PowerWorldError]):
    with pytest.raises(PowerWorldError) as caught:
        unwrap("Method", (message,))
    assert type(caught.value) is kind
    assert (caught.value.method, caught.value.message) == ("Method", message)


def test_arrays_are_marked_for_marshalling():
    transport = Recorder(("", None))
    api = SimAuto(transport)
    api.GetParamsTypedCols("Bus", ["BusNum"], "MyFilter")
    api.ChangeParametersMultipleElement("Bus", ["BusNum", "BusName"], [[1, "a"], [2, "b"]])
    api.ChangeParametersMultipleElementRect("Bus", ["BusNum"], [[1], [2]])
    assert transport.calls == [
        ("GetParamsTypedCols", ("Bus", Array(["BusNum"]), "MyFilter", 12)),
        (
            "ChangeParametersMultipleElement",
            ("Bus", Array(["BusNum", "BusName"]), [Array([1, "a"]), Array([2, "b"])]),
        ),
        ("ChangeParametersMultipleElementRect", ("Bus", Array(["BusNum"]), Array([[1], [2]]))),
    ]


def test_properties():
    transport = Recorder()
    api = SimAuto(transport)
    api.CreateIfNotFound = True
    assert transport.properties["CreateIfNotFound"] is True
    assert api.CreateIfNotFound is True


def test_com_transport_needs_pywin32(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "pythoncom", None)
    with pytest.raises(SimAutoUnavailable, match="pywin32"):
        ComTransport()
