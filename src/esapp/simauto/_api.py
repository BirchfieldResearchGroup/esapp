"""SimAuto's COM functions, 1:1 with PowerWorld's documentation.

Arguments are plain Python; results come back with SimAuto's ``(error, result)``
envelope removed, and errors raise :class:`~esapp.errors.PowerWorldError` subclasses.
Most code should use :class:`esapp.PowerWorld` instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from esapp.errors import PowerWorldError
from esapp.simauto._transport import Array, Transport

VT_I4, VT_R8, VT_BSTR, VT_VARIANT = 3, 5, 8, 12  # pythoncom's VARENUM values

type Rows = Sequence[Sequence[Any]]
type Cols = Sequence[Sequence[Any]]


def unwrap(method: str, out: object) -> Any:
    """Strip SimAuto's ``(error, result...)`` envelope, raising on a non-empty error."""
    match out:
        case (str() as error, *rest):
            if error and "No data" not in error:
                raise PowerWorldError.from_message(error, method=method)
            match rest:
                case []:
                    return None
                case [result]:
                    return result
                case _:
                    return tuple(rest)
        case _:
            return out


class SimAuto:
    """The SimAuto functions esapp uses. :meth:`call` reaches any other, by its documented name."""

    __slots__ = ("transport",)

    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def call(self, method: str, /, *args: object) -> Any:
        """Call a SimAuto function. Wrap array arguments in :class:`Array`."""
        return unwrap(method, self.transport.call(method, *args))

    # ------------------------------------------------------------------ case files
    def OpenCase(self, FileName: str) -> None:
        self.call("OpenCase", FileName)

    def CloseCase(self) -> None:
        self.call("CloseCase")

    def SaveState(self) -> None:
        self.call("SaveState")

    def LoadState(self) -> None:
        self.call("LoadState")

    # ------------------------------------------------------------------ scripts
    def RunScriptCommand(self, Statements: str) -> None:
        self.call("RunScriptCommand", Statements)

    def ProcessAuxFile(self, FileName: str) -> None:
        self.call("ProcessAuxFile", FileName)

    # ------------------------------------------------------------------ reading
    def GetParamsTypedCols(
        self,
        ObjectType: str,
        ParamList: Sequence[str],
        FilterName: str = "",
        ColTypes: Sequence[int] | int = VT_VARIANT,
    ) -> Cols | None:
        """One sequence per field, each in the VARENUM type ``ColTypes`` gives it (or one type
        for all); ``None`` when there are no objects. A field Simulator cannot send as the
        scalar type asked for fails the whole call."""
        types = ColTypes if isinstance(ColTypes, int) else Array(list(ColTypes))
        return self.call(
            "GetParamsTypedCols", ObjectType, Array(list(ParamList)), FilterName, types
        )

    def GetFieldList(self, ObjectType: str) -> Rows:
        return self.call("GetFieldList", ObjectType)

    def TSGetContingencyResults(
        self,
        CtgName: str,
        ObjFieldList: Sequence[str],
        StartTime: float | None = None,
        StopTime: float | None = None,
    ) -> tuple[Any, ...] | None:
        """Transient results held in RAM for one contingency: ``(header, data)``.

        ``ObjFieldList`` holds ``"Gen '2' '1' | TSGenW"``-style requests. ``header`` has one
        6-tuple per request Simulator resolved (unresolved ones are left out silently);
        ``data`` rows start with the time, and every value is a decimal string. Fields
        without data read 0, or are dropped when they come last.
        """
        return self.call(
            "TSGetContingencyResults", CtgName, Array(list(ObjFieldList)), StartTime, StopTime
        )

    # ------------------------------------------------------------------ writing
    def ChangeParametersMultipleElement(
        self, ObjectType: str, ParamList: Sequence[str], ValueList: Rows
    ) -> None:
        """One value list per object. Creates missing objects when CreateIfNotFound is on."""
        self.call(
            "ChangeParametersMultipleElement",
            ObjectType,
            Array(list(ParamList)),
            [Array(list(values)) for values in ValueList],
        )

    def ChangeParametersMultipleElementRect(
        self, ObjectType: str, ParamList: Sequence[str], Rows: Rows
    ) -> None:
        """Rows of values, one per existing object: the fast bulk-update path."""
        self.call(
            "ChangeParametersMultipleElementRect", ObjectType, Array(list(ParamList)), Array(Rows)
        )

    def WriteAuxFile(
        self,
        FileName: str,
        FilterName: str,
        ObjectType: str,
        ToAppend: bool,
        FieldList: Sequence[str] | str,
    ) -> None:
        """Write the objects through ``FilterName`` to an AUX file; ``FieldList`` may be "ALL"."""
        fields = FieldList if isinstance(FieldList, str) else Array(list(FieldList))
        self.call("WriteAuxFile", FileName, FilterName, ObjectType, ToAppend, fields)

    # ------------------------------------------------------------------ properties
    @property
    def CreateIfNotFound(self) -> bool:
        return bool(self.transport.get("CreateIfNotFound"))

    @CreateIfNotFound.setter
    def CreateIfNotFound(self, value: bool) -> None:
        self.transport.set("CreateIfNotFound", value)

    @property
    def ProgramInformation(self) -> tuple[Any, ...]:
        return tuple(self.transport.get("ProgramInformation"))  # type: ignore[arg-type]
