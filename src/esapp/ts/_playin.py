"""Play-in signals: time series that a model plays into a transient simulation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(frozen=True, eq=False)
class PlayIn:
    """Time series that a model plays into a transient simulation. ``signals`` has one column
    per signal, indexed by time in seconds.

    Simulator plays ``value * scale + offset``. ``scale`` and ``offset`` are keyed by column
    name and default to 1 and 0. The play-in starts ``delay`` seconds into the simulation. Models
    refer to it by ``name``; ``pw.ts.define(playin)`` saves it in the case.
    """

    name: str
    signals: pd.DataFrame
    scale: Mapping[str, float] = field(default_factory=dict[str, float])
    offset: Mapping[str, float] = field(default_factory=dict[str, float])
    delay: float = 0.0

    def __post_init__(self) -> None:
        signals = self.signals
        if signals.shape[1] == 0 or len(signals) == 0:
            raise ValueError(f"play-in {self.name!r} needs at least one signal and one time")
        time = signals.index.to_numpy(dtype=float)
        if not np.all(np.diff(time) > 0):
            raise ValueError(f"play-in {self.name!r}: times must increase")
        if strays := (set(self.scale) | set(self.offset)) - set(map(str, signals.columns)):
            raise ValueError(f"play-in {self.name!r}: no signals named {sorted(strays)}")

    @property
    def names(self) -> list[str]:
        return [str(c) for c in self.signals.columns]


def signal_field(k: int) -> str:
    """PlayInSignal's field for the ``k``-th signal (1-based): TSSignal, TSSignal:1, ..."""
    return "TSSignal" if k == 1 else f"TSSignal:{k - 1}"
