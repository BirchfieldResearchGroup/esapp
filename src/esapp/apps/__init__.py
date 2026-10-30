"""Command namespaces reached from the session: ``pw.log``, ``pw.pf``, ``pw.ctg``,
``pw.sens``, ``pw.opf``, ``pw.atc``, ``pw.qv``, ``pw.gic``, ``pw.fault``, ``pw.files`` and
``pw.ts``. Each but the log lives in its own module here, loaded on first use with the
object types and commands it needs.
"""

from esapp.apps._base import Namespace
from esapp.apps.log import Log

__all__ = ["Log", "Namespace"]
