"""Transports for SimAuto calls. ``ComTransport`` is the only code that uses pywin32."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from esapp.errors import ComError, SimAutoUnavailable

# HRESULTs meaning the SimAuto process died or stopped answering.
_RPC_FAILURES = {0x800706BA, 0x800706BE, 0x800706B5}


@dataclass(frozen=True, slots=True)
class Array:
    """An argument SimAuto expects as a VARIANT array: a 1-D list, or 2-D rows."""

    items: Sequence[Any]


class Transport(Protocol):
    """Sends calls to SimAuto: ``ComTransport`` for the real thing, a fake in tests.
    ``call`` returns SimAuto's raw ``(error, result...)`` tuple."""

    def call(self, method: str, /, *args: object) -> object: ...
    def get(self, prop: str, /) -> object: ...
    def set(self, prop: str, value: object, /) -> None: ...
    def close(self) -> None: ...


class ComTransport:
    """SimAuto over COM. Each instance starts one Simulator process, using one license.
    Calls must come from the thread that created it; others raise :class:`ComError`."""

    def __init__(self, *, visible: bool = False) -> None:
        try:
            import pythoncom
            import win32com.client
            import win32com.client.dynamic
            import win32com.client.gencache
        except ImportError as err:
            raise SimAutoUnavailable("SimAuto needs Windows with pywin32 installed") from err

        pythoncom.CoInitialize()
        self._pythoncom = pythoncom
        self._variant = win32com.client.VARIANT
        self._array_type = pythoncom.VT_ARRAY | pythoncom.VT_VARIANT
        self._thread = threading.get_ident()
        try:
            try:
                com = win32com.client.gencache.EnsureDispatch("pwrworld.SimulatorAuto")
            except AttributeError:  # stale gen_py cache: fall back to late binding
                com = win32com.client.dynamic.Dispatch("pwrworld.SimulatorAuto")
        except pythoncom.com_error as err:
            pythoncom.CoUninitialize()
            raise SimAutoUnavailable(
                f"could not start SimAuto (is it registered, with a free license?): {err}"
            ) from err
        self._com: Any = com
        self._com.UIVisible = visible

    def call(self, method: str, /, *args: object) -> object:
        com = self._live(method)
        try:
            function = getattr(com, method)
        except AttributeError:
            raise ComError(method, "not a SimAuto function") from None
        try:
            return function(*map(self._marshal, args))
        except self._pythoncom.com_error as err:
            raise _com_error(method, err) from err

    def get(self, prop: str, /) -> object:
        return getattr(self._live(prop), prop)

    def set(self, prop: str, value: object, /) -> None:
        setattr(self._live(prop), prop, value)

    def close(self) -> None:
        """Release SimAuto, which ends its process. Safe to call twice."""
        if self._com is None:
            return
        self._com = None
        if threading.get_ident() == self._thread:
            self._pythoncom.CoUninitialize()

    def _live(self, method: str) -> Any:
        if self._com is None:
            raise ComError(method, "the SimAuto session is closed")
        if threading.get_ident() != self._thread:
            raise ComError(
                method, "SimAuto was called from a thread other than the one that started it"
            )
        return self._com

    def _marshal(self, value: object) -> object:
        match value:
            case Array(items):
                return self._variant(self._array_type, list(items))
            case list():
                return [self._marshal(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
            case _:
                return value


def _com_error(method: str, err: Exception) -> ComError:
    code = getattr(err, "hresult", None)
    if code is None and err.args and isinstance(err.args[0], int):
        code = err.args[0]
    if isinstance(code, int) and code & 0xFFFFFFFF in _RPC_FAILURES:
        return ComError(method, f"SimAuto stopped responding (it may have crashed): {err}")
    return ComError(method, str(err))
