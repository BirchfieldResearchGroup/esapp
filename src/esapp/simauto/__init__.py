"""The SimAuto layer: transports, script encoding, and the COM functions esapp uses."""

from esapp.simauto._api import VT_BSTR as VT_BSTR
from esapp.simauto._api import VT_I4 as VT_I4
from esapp.simauto._api import VT_R8 as VT_R8
from esapp.simauto._api import VT_VARIANT as VT_VARIANT
from esapp.simauto._api import SimAuto
from esapp.simauto._api import unwrap as unwrap
from esapp.simauto._script import Bare, script
from esapp.simauto._script import Kw as Kw
from esapp.simauto._script import arg as arg
from esapp.simauto._script import quote as quote
from esapp.simauto._transport import Array, ComTransport, Transport

__all__ = ["Array", "Bare", "ComTransport", "SimAuto", "Transport", "script"]
