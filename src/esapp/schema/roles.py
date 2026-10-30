"""One-letter role names used by the generated modules in :mod:`esapp.grid`."""

from esapp.schema._core import Role

K = Role.KEY
A = Role.ALT_KEY
R = Role.REQUIRED
E = Role.ENTERABLE
M = Role.EDIT_ONLY
V = Role.INDEXED
I = Role.IDENTIFIER  # noqa: E741 - one letter, like its siblings
N = Role.NONSCALAR

__all__ = ["A", "E", "I", "K", "M", "N", "R", "V"]
