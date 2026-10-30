from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from esapp._session import PowerWorld

SCRATCH = "esapp~"  # the name prefix of what esapp creates in a case and deletes after


class Namespace:
    """A group of commands reached from the session, such as ``pw.log``."""

    __slots__ = ("_pw",)

    def __init__(self, pw: PowerWorld) -> None:
        self._pw = pw
