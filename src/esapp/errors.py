"""Exceptions and warnings raised by esapp."""

from __future__ import annotations

from typing import ClassVar


class EsappError(Exception):
    """Base class for every esapp error."""


class SimAutoUnavailable(EsappError):
    """SimAuto could not start: not Windows, no pywin32, not registered, or no free license."""


class ComError(EsappError):
    """The COM call failed: SimAuto crashed or stopped responding, or was called after it
    closed or from another thread."""

    def __init__(self, method: str, detail: str) -> None:
        super().__init__(f"{method}: {detail}")
        self.method = method


class ReadOnlySessionError(EsappError):
    """A write, or a command that changes the case, was run inside ``pw.read_only()``."""


class PowerWorldError(EsappError):
    """PowerWorld rejected a call and returned an error message."""

    patterns: ClassVar[tuple[str, ...]] = ()  # lowercase fragments that select this subclass

    def __init__(self, message: str, *, method: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.method = method

    @staticmethod
    def from_message(message: str, *, method: str = "") -> PowerWorldError:
        """The most specific subclass whose patterns occur in ``message``."""
        lowered = message.lower()
        for kind in _SPECIFIC:
            if any(p in lowered for p in kind.patterns):
                return kind(message, method=method)
        return PowerWorldError(message, method=method)


class SimulatorFault(PowerWorldError):
    """Simulator crashed internally (an access violation) but kept running.

    Simulator 24 does this for TSClearResultsFromRAM, and for TSResultStorageSetAll on some
    object types. esapp doesn't call either.
    """

    patterns = ("access violation",)


class UnknownCommand(PowerWorldError):
    """Simulator doesn't know the script command: a typo, or a command from a newer
    Simulator."""

    patterns = ("unknown script command",)


class NotConverged(PowerWorldError):
    """An iterative solution gave up ("NR PowerFlow - Exceeded maximum number of iterations")."""

    patterns = ("exceeded maximum number of iterations", "did not converge")


class NotRetrievable(PowerWorldError):
    """The object or field exists, but SimAuto cannot read it."""

    patterns = ("cannot be retrieved through simauto",)


class AddonNotLicensed(PowerWorldError):
    """The command needs a PowerWorld add-on that is not registered or licensed."""

    patterns = ("not registered",)


class PrerequisiteError(PowerWorldError):
    """The case isn't set up for the command: it has no contingencies, no ATC directions,
    and so on."""

    patterns = (
        "no active",
        "requires setup",
        "is not online",
        "at least one",
        "no directions set",
        "out-of-range",
        "no available participation points",
        "no area/superarea constraints",  # OPF: no area is set to be controlled
        "island-based agc",  # OPF: the case balances islands by AGC, which OPF cannot
    )


class ObjectNotFound(PrerequisiteError):
    """An object the call refers to does not exist."""

    patterns = ("not found", "could not be found", "cannot be found")  # last: device filters


class ResultsUnavailable(PrerequisiteError):
    """Simulator has no transient results for the request: the contingency wasn't solved,
    or the field wasn't recorded."""


_SPECIFIC: tuple[type[PowerWorldError], ...] = (
    SimulatorFault,
    UnknownCommand,
    NotConverged,
    NotRetrievable,
    AddonNotLicensed,
    ObjectNotFound,
    PrerequisiteError,
)


class SchemaWarning(UserWarning):
    """A field ``esapp.grid`` doesn't list, a write to one it lists as read-only, or a field
    Simulator can't send as one value."""
