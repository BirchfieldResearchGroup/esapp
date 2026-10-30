"""Injection groups: ``pw.groups``."""

from __future__ import annotations

from typing import Literal

import pandas as pd

from esapp._indexing import Where
from esapp.apps._base import Namespace
from esapp.commands._catalog import InjectionGroupCreate
from esapp.grid import InjectionGroup, PartPoint
from esapp.schema import GObject

__all__ = ["Groups", "Share"]

type Share = Literal[
    "PRESENT",
    "MAX GEN INC",
    "MAX GEN DEC",
    "MAX GEN MW",
    "LOAD MW",
    "MAX SHUNT INC",
    "MAX SHUNT DEC",
    "MAX SHUNT MVAR",
]
"""A participation factor Simulator calculates for each member when the group is used: a
generator's output or headroom, a load's MW, or a shunt's Mvar or headroom."""

_POINTS = [PartPoint.PPntParFac, PartPoint.PPntPFInit, PartPoint.PPntUseFixedParFac]


class Groups(Namespace):
    """Injection groups: ``pw.groups``. Use a group as the source or sink of a transfer, a
    PV study, or a sensitivity.

    ::

        west = pw.groups.create("west", Gen, Gen.AreaNum.eq(1), share="MAX GEN INC")
        east = pw.groups.create("east", Load, Load.AreaNum.eq(2), share="LOAD MW")
    """

    __slots__ = ()

    def create(
        self,
        name: str,
        members: type[GObject],
        where: Where = "",
        *,
        share: float | Share = 1.0,
        append: bool = False,
    ) -> InjectionGroup:
        """Create the injection group ``name`` from the ``members`` (Gen, Load, Shunt or Bus)
        that ``where`` selects. Each takes part by ``share``: a number, or a :data:`Share`
        keyword.

        With ``append``, they're added to an existing group of that name; otherwise the group
        is replaced.
        """
        if not append:
            self.delete(InjectionGroup(name))
        self._pw.run(InjectionGroupCreate(name, members, share, where, append))
        return InjectionGroup(name)

    def points(self, group: InjectionGroup) -> pd.DataFrame:
        """``group``'s members, indexed by WhoAmI: their participation factor, how it's set,
        and whether it's fixed."""
        group_name = PartPoint.PPntType[1]
        rows = self._pw[PartPoint, _POINTS, group_name.eq(str(group.key[0]))]
        return rows.drop(columns=group_name.name).set_index("WhoAmI")

    def delete(self, group: InjectionGroup) -> None:
        """Delete ``group`` (does nothing if it doesn't exist)."""
        self._pw.run("Delete", InjectionGroup, f"InjGrpName = '{group.key[0]}'")
