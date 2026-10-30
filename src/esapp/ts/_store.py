"""Results on disk: one memory-mapped file pair per simulation, plus a JSON manifest."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from importlib import import_module
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from numpy.lib.format import open_memmap

from esapp.schema import Field
from esapp.simauto._script import StrPath
from esapp.ts._plan import Plan
from esapp.ts._result import Result
from esapp.ts._transfer import Allocate, Buffers, memory

MANIFEST = "manifest.json"
_VERSION = 1


class Store(Mapping[str, Result]):
    """Simulations saved in a directory by :meth:`Sweep.save <esapp.apps.ts.Sweep.save>`.
    ``store[name]`` opens one simulation without reading the others.

    The files are memory-mapped: data is read from disk only as you use it, so a store can be
    much larger than RAM. On Windows, a file can't be deleted while a result read from it is
    still in use.
    """

    def __init__(self, path: StrPath) -> None:
        self.path = Path(path)
        if not (self.path / MANIFEST).is_file():
            raise FileNotFoundError(f"no esapp result store at {self.path}")
        self._manifest = _read(self.path)

    def __getitem__(self, name: str) -> Result:
        sim = self._manifest["simulations"][name]
        stem = self.path / sim["stem"]
        return Result(
            name=name,
            time=_load(f"{stem}.time.npy"),
            values=_load(f"{stem}.values.npy"),
            plan=Plan.from_json(self._manifest["plans"][sim["plan"]]),
            summary=pd.Series(**sim["summary"]),
            events=_frame(sim["events"]),
            stats={_field(key): _stat(table) for key, table in sim["stats"].items()},
        )

    def __iter__(self) -> Iterator[str]:
        return iter(self._manifest["simulations"])

    def __len__(self) -> int:
        return len(self._manifest["simulations"])

    def __repr__(self) -> str:
        return f"Store({str(self.path)!r}: {len(self)} simulations)"


class Writer:
    """Gives each simulation memory-mapped buffers, then adds it to the manifest when finished.

    The manifest is replaced atomically after every simulation, so an interrupted sweep keeps
    every simulation that finished before it stopped.
    """

    def __init__(self, path: StrPath) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self._manifest = _read(self.path)
        self._stem = ""

    def __contains__(self, name: object) -> bool:
        return name in self._manifest["simulations"]

    def allocate(self, dtype: npt.DTypeLike = np.float32) -> Allocate:
        """Buffers for the next simulation, as files: the transfer writes straight into them."""
        taken = [int(sim["stem"]) for sim in self._manifest["simulations"].values()]
        self._stem = stem = f"{max(taken, default=0) + 1:05d}"
        base = self.path / stem

        def allocate(rows: int, cols: int) -> Buffers:
            if not rows or not cols:  # nothing to map; commit saves these
                return memory(dtype)(rows, cols)
            return Buffers(
                open_memmap(f"{base}.time.npy", "w+", np.float64, (rows,)),
                open_memmap(f"{base}.values.npy", "w+", np.dtype(dtype), (rows, cols), True),
            )

        return allocate

    def commit(self, result: Result) -> None:
        """Record ``result``, whose buffers came from the latest :meth:`allocate`."""
        for part, array in (("time", result.time), ("values", result.values)):
            if isinstance(array, np.memmap):
                array.flush()
            else:
                np.save(self.path / f"{self._stem}.{part}.npy", array)
        plan = result.plan.to_json()
        plan_id = hashlib.sha1(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:12]
        self._manifest["plans"].setdefault(plan_id, plan)
        summary = result.summary
        self._manifest["simulations"][result.name] = {
            "stem": self._stem,
            "plan": plan_id,
            "summary": {
                "data": summary.tolist(),
                "index": summary.index.tolist(),
                "name": summary.name,
            },
            "events": _table(result.events),
            "stats": {
                f"{f.owner.__name__}.{f.name}": _table(s.reset_index())
                for f, s in result.stats.items()
            },
        }
        temporary = self.path / f"{MANIFEST}.tmp"  # Python's json writes floats exactly
        temporary.write_text(json.dumps(self._manifest, default=_scalar), encoding="utf-8")
        os.replace(temporary, self.path / MANIFEST)


def _read(path: Path) -> dict[str, Any]:
    manifest = path / MANIFEST
    if not manifest.is_file():
        return {"version": _VERSION, "plans": {}, "simulations": {}}
    data: dict[str, Any] = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("version") != _VERSION:
        raise ValueError(f"{manifest}: store version {data.get('version')!r}, not {_VERSION}")
    return data


def _load(path: str) -> Any:
    try:
        return np.load(path, mmap_mode="r")
    except ValueError:  # an empty array cannot be mapped
        return np.load(path)


def _table(frame: pd.DataFrame) -> dict[str, list[Any]]:
    return {"columns": frame.columns.tolist(), "data": frame.to_numpy(dtype=object).tolist()}


def _frame(table: dict[str, list[Any]]) -> pd.DataFrame:
    return pd.DataFrame(table["data"], columns=table["columns"])


def _scalar(value: object) -> object:
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _field(key: str) -> Field[Any]:
    owner, _, name = key.partition(".")
    return getattr(import_module("esapp.grid"), owner).schema.field(name)


def _stat(table: dict[str, list[Any]]) -> pd.Series:
    frame = _frame(table)
    *keys, value = frame.columns
    return frame.set_index(keys)[value] if keys else frame[value]
