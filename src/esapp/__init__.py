"""esa++: Python for PowerWorld.

::

    from esapp import PowerWorld
    from esapp.grid import Bus, Gen

    with PowerWorld("case.pwb") as pw:
        df = pw[Bus, [Bus.BusPUVolt, Bus.BusAngle], Bus.BusNomVolt > 100]
        pw[Gen, Gen.GenMW] = 100.0
        pw.pf.solve()
"""

from esapp._session import PowerWorld, Where
from esapp.errors import (
    AddonNotLicensed,
    ComError,
    EsappError,
    NotConverged,
    NotRetrievable,
    ObjectNotFound,
    PowerWorldError,
    PrerequisiteError,
    ReadOnlySessionError,
    ResultsUnavailable,
    SchemaWarning,
    SimAutoUnavailable,
    SimulatorFault,
    UnknownCommand,
)
from esapp.simauto import Kw

__all__ = [
    "AddonNotLicensed",
    "ComError",
    "EsappError",
    "Kw",
    "NotConverged",
    "NotRetrievable",
    "ObjectNotFound",
    "PowerWorld",
    "PowerWorldError",
    "PrerequisiteError",
    "ReadOnlySessionError",
    "ResultsUnavailable",
    "SchemaWarning",
    "SimAutoUnavailable",
    "SimulatorFault",
    "UnknownCommand",
    "Where",
]
