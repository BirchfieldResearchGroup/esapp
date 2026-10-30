"""SUBDATA as it travels in an auxiliary file's DATA section: one reader and one writer.

Simulator writes an object type's lists after each object's keys (``WriteAuxFile``) and takes
them back the same way (``ProcessAuxFile``); a block it reads replaces that object's list::

    DATA (Gen, [BusNum,GenID])
    {
         2 "1"
       <SUBDATA BidCurve>
         //MW   Price[$/MWhr]
             1.00     4.58
       </SUBDATA>
    }
"""

from __future__ import annotations

import math
import numbers
import re
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

import pandas as pd

from esapp.schema import GObject, SubData

_TOKEN = re.compile(r'"([^"]*)"|(\S+)')


def read_text(path: Path) -> str:
    """A file Simulator wrote: UTF-8, else the Windows code page it uses; "" if it wrote none
    (as when nothing was selected)."""
    if not path.exists():
        return ""
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def tokens(line: str) -> list[str]:
    """A line's values: quoted text whole (quotes dropped), else words."""
    return [bare or quoted for quoted, bare in _TOKEN.findall(line)]


def entries(text: str, keys: int, name: str) -> Iterator[tuple[list[str], int, str]]:
    """Each ``<SUBDATA name>`` line in ``text``, stripped: its owner's first ``keys`` values,
    its place in the owner's list (0, 1, ...), and the line."""
    owner: list[str] = []
    inside: str | None = None
    point = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("//", "DATA")) or line in ("{", "}"):
            continue
        if line.startswith("<SUBDATA"):
            inside, point = line.removeprefix("<SUBDATA").rstrip(">").strip(), 0
        elif line.startswith("</SUBDATA"):
            inside = None
        elif inside is None:
            owner = [t.strip() for t in tokens(line)[:keys]]
        elif inside == name:
            yield owner, point, line
            point += 1


def read_subdata(text: str, gtype: type[GObject], sub: SubData) -> pd.DataFrame:
    """Every ``<SUBDATA sub>`` entry in ``text``: the owner's keys, ``point`` (0, 1, ... in
    order), then the entry's values, named as ``sub.columns`` says and by position after."""
    keys = gtype.schema.key_names
    rows = [
        [*owner, point, *tokens(line)] for owner, point, line in entries(text, len(keys), sub.name)
    ]
    width = max((len(r) - len(keys) - 1 for r in rows), default=len(sub.columns))
    names = [*sub.columns, *(str(n) for n in range(len(sub.columns) + 1, width + 1))]
    columns = [*keys, "point", *names]
    frame = pd.DataFrame([r + [None] * (len(columns) - len(r)) for r in rows], columns=columns)
    for key in gtype.schema.keys:
        frame[key.name] = frame[key.name].astype(key.dtype if key.dtype in (int, float) else str)
    for name in names:
        frame[name] = _numbers(frame[name])
    return frame.astype({"point": "int64"})


def write_subdata(
    gtype: type[GObject],
    sub: SubData,
    frame: pd.DataFrame,
    owners: Iterable[Sequence[object]] = (),
) -> str:
    """A DATA section giving each object in ``frame`` (by its keys, in ``point`` order) exactly
    its rows as ``sub``; each of ``owners`` missing from ``frame`` gets an empty list."""
    keys = gtype.schema.key_names
    if missing := [k for k in keys if k not in frame.columns]:
        raise ValueError(f"{sub!r} rows need their owner's keys: {missing}")
    values = [str(c) for c in frame.columns if c not in (*keys, "point")]
    if sub.columns and (strays := set(values) - set(sub.columns)):
        raise ValueError(f"{sub!r} has no columns {sorted(strays)}; it has {list(sub.columns)}")
    order = [c for c in sub.columns if c in values] if sub.columns else sorted(values, key=int)
    if "point" in frame.columns:
        frame = frame.sort_values("point", kind="stable")
    lines = [f"DATA ({gtype.schema.type}, [{','.join(keys)}])", "{"]
    written: set[tuple[str, ...]] = set()
    for key, rows in frame.groupby(keys, sort=False):  # a list of keys: tuples, even of one
        written.add(tuple(map(str, key)))
        lines += _object(key, sub, (list(r) for r in rows[order].itertuples(index=False)))
    for key in owners:
        if tuple(map(str, key)) not in written:
            lines += _object(tuple(key), sub, ())
    return "\n".join([*lines, "}"]) + "\n"


def _object(key: Sequence[object], sub: SubData, rows: Iterable[list[object]]) -> list[str]:
    body: list[str] = []
    for row in rows:
        while row and _blank(row[-1]):
            row.pop()  # trailing blanks take Simulator's defaults
        body.append(" ".join(map(_token, row)))
    return [" ".join(map(_token, key)), f"<SUBDATA {sub.name}>", *body, "</SUBDATA>"]


def _token(value: object) -> str:
    match value:
        case bool():
            return "YES" if value else "NO"
        case numbers.Integral():
            return str(int(value))
        case numbers.Real():
            return repr(float(value))
        case _ if _blank(value):
            return '""'
        case _:
            text = str(value)
            if '"' in text:
                raise ValueError(f"PowerWorld auxiliary files cannot hold a double quote: {text!r}")
            return f'"{text}"'


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _numbers(column: pd.Series) -> pd.Series:
    """Numbers where every value is one, text (stripped) otherwise."""
    present = column.dropna()
    numeric = pd.to_numeric(present, errors="coerce")
    if len(present) and not numeric.isna().any():
        return pd.to_numeric(column)
    return column.map(lambda v: v.strip() if isinstance(v, str) else v).astype(object)
