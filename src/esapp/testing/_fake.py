from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from importlib import import_module
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from esapp.schema import GObject, Role
from esapp.simauto._api import VT_BSTR, VT_I4, VT_R8
from esapp.simauto._script import Bare, is_yes
from esapp.simauto._transport import Array
from esapp.ts._recordable import flags

type Reply = tuple[Any, ...]
type Trajectories = tuple[Sequence[float], Mapping[tuple[GObject, str], Sequence[float]]]
type Dynamics = Callable[[str, pd.DataFrame], Trajectories]
type _Stored = tuple[str, tuple[str, ...], str]  # (object type, key values as text, field)

_REQUEST = re.compile(r"^(\S+)((?:\s+'[^']*')*)\s*\|\s*(\S+)\s*$")
_DEVICE = re.compile(r"^<DEVICE>(\S+)((?:\s+'[^']*')*)\s*$")
_CONDITION = re.compile(r"^\s*(\S+)\s+(=|<>|>=|<=|>|<|[a-z]+)\s*(.*?)\s*$")
_KEYWORDS = {"ALL", "SELECTED", "AREAZONE"}


class FakeSimAuto:
    """An in-memory SimAuto for tests without PowerWorld. Give it objects with :meth:`add`.

    It records every call, applies filters (saved filters, single objects, conditions,
    SELECTED), and stops a batch of commands at the first error. It carries out the log
    commands, ``SetData``, ``Delete``, ``SelectAll``/``UnSelectAll`` and the transient
    commands; other commands are only recorded.

    For transient simulations, set ``dynamics(contingency, its elements)`` to return
    ``(time, {(object, field): values})``. Only fields whose recording is on come back.
    ``nonscalar`` lists the (type, field) pairs that can't be read as single values.
    """

    def __init__(self) -> None:
        self.tables: dict[str, pd.DataFrame] = {}
        self.keys: dict[str, list[str]] = {}
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.scripts: list[str] = []
        self.log: list[str] = []
        self.properties: dict[str, object] = {
            "CreateIfNotFound": False,
            "UIVisible": False,
            "CurrentDir": "",
            "ProcessID": 0,
        }
        self.errors: dict[str, str] = {}  # method -> error message for its next call
        self.case: str | None = None
        self.closed = False
        self.dynamics: Dynamics | None = None
        self.results: dict[str, tuple[list[float], dict[_Stored, list[float]]]] = {}
        self.nonscalar: set[tuple[str, str]] = set()
        # SUBDATA: (object type, name) -> an object's keys as text -> its entries, as written
        self.subdata: dict[tuple[str, str], dict[tuple[str, ...], list[list[object]]]] = {}
        self._saved: dict[str, pd.DataFrame] | None = None

    def add(self, gtype: type[GObject], table: pd.DataFrame | dict[str, Sequence[object]]) -> None:
        """Give the fake the objects of ``gtype`` in ``table``, one row per object, with
        field names as columns."""
        self.tables[gtype.schema.type] = pd.DataFrame(table).astype(object)
        self.keys[gtype.schema.type] = gtype.schema.key_names

    def add_subdata(
        self,
        gtype: type[GObject],
        name: str,
        lists: Mapping[tuple[object, ...], Sequence[Sequence[object]]],
    ) -> None:
        """Give objects of ``gtype`` their ``name`` subdata. ``lists`` maps each object's key
        to its rows of values."""
        held = self.subdata.setdefault((gtype.schema.type, name), {})
        for key, rows in lists.items():
            held[tuple(map(str, key))] = [list(row) for row in rows]

    # ------------------------------------------------------------------ Transport
    def call(self, method: str, /, *args: object) -> object:
        self.calls.append((method, args))
        if (error := self.errors.pop(method, None)) is not None:
            return (error,)
        handler = getattr(self, f"_{method}", None)
        if handler is None:
            return (f"{method}: not emulated by FakeSimAuto",)
        return handler(*map(_plain, args))

    def get(self, prop: str, /) -> object:
        return self.properties[prop]

    def set(self, prop: str, value: object, /) -> None:
        self.properties[prop] = value

    def close(self) -> None:
        self.closed = True

    # ------------------------------------------------------------------ case files
    def _OpenCase(self, path: str) -> Reply:
        self.case = path
        return ("",)

    def _CloseCase(self) -> Reply:
        self.case = None
        return ("",)

    def _SaveState(self) -> Reply:
        self._saved = {name: table.copy() for name, table in self.tables.items()}
        return ("",)

    def _LoadState(self) -> Reply:
        if self._saved is None:
            return ("LoadState: no state has been saved",)
        self.tables = {name: table.copy() for name, table in self._saved.items()}
        return ("",)

    # ------------------------------------------------------------------ scripts
    def _RunScriptCommand(self, text: str) -> Reply:
        for statement in _statements(text):  # in order, stopping at the first error
            self.scripts.append(statement)
            name, args = _parse(statement)
            if error := self._execute(name, args):
                return (f"RunScriptCommand: {error}",)
        return ("",)

    def _execute(self, name: str, args: list[Any]) -> str:
        match name, args:
            case "LogAdd", [message]:
                self.log.append(str(message))
            case "LogClear", []:
                self.log.clear()
            case "LogSave", [path, *_]:
                Path(path).write_text("".join(f"{line}\n" for line in self.log))
            case "Delete", [obj, *where]:
                return self._delete(obj, where[0] if where else "")
            case "SelectAll" | "UnSelectAll", [obj, *where]:
                return self._mark(obj, where[0] if where else "", name == "SelectAll")
            case "SetData", [obj, list() as fields, list() as values, *where]:
                return self._set_data(obj, fields, values, where[0] if where else None)
            case "TSResultStorageSetAll", [obj, value]:
                self._store_all(obj, value)
            case "TSSolve", [ctg, *_]:
                return self._solve(ctg, None)
            case "TSRunUntilSpecifiedTime", [ctg, list() as times, *_]:
                return self._solve(ctg, float(times[0]))
            case _:
                pass
        return ""

    # ------------------------------------------------------------------ reads
    def _GetParamsTypedCols(
        self, obj: str, fields: list[str], where: str, types: int | list[int]
    ) -> Reply:
        per_field = types if isinstance(types, list) else [types] * len(fields)
        for f, vt in zip(fields, per_field, strict=True):
            if (obj, f) in self.nonscalar and vt in (VT_I4, VT_R8, VT_BSTR):
                return (
                    f"GetParamsTypedCols: Unsupported conversion of non-scalar field {f} to VT",
                )
        rows = self._rows(obj, where)
        if isinstance(rows, str):
            return (f"GetParamsTypedCols: {rows}", None)
        if rows is None or rows.empty:
            return (f"GetParamsTypedCols: No data returned for type{obj} and filter {where}", None)
        table = rows.reindex(columns=fields, fill_value=0.0)
        return (
            "",
            tuple(
                tuple(_typed(v, vt) for v in table[f])
                for f, vt in zip(fields, per_field, strict=True)
            ),
        )

    # ------------------------------------------------------------------ writes
    def _ChangeParametersMultipleElementRect(
        self, obj: str, fields: list[str], rows: list[list[Any]]
    ) -> Reply:
        return self._write("ChangeParametersMultipleElementRect", obj, fields, rows, create=False)

    def _ChangeParametersMultipleElement(
        self, obj: str, fields: list[str], rows: list[list[Any]]
    ) -> Reply:
        create = bool(self.properties["CreateIfNotFound"])
        return self._write("ChangeParametersMultipleElement", obj, fields, rows, create=create)

    def _WriteAuxFile(
        self, path: str, where: str, obj: str, append: bool, fields: list[str] | str
    ) -> Reply:
        rows = self._rows(obj, where)
        if isinstance(rows, str):
            return (f"WriteAuxFile: {rows}",)
        if fields == "ALL":
            names = [] if rows is None else [str(c) for c in rows.columns]
        else:
            names = list(fields)
        lines = [f"DATA ({obj}, [{','.join(names)}])", "{"]
        if rows is not None:
            table = rows.reindex(columns=names, fill_value="")
            keys = self._keys(obj)
            for key, row in zip(
                rows[keys].itertuples(index=False), table.itertuples(index=False), strict=True
            ):
                lines.append(" ".join(_aux(v) for v in row))
                for (owner, name), held in self.subdata.items():  # every list the object holds
                    if owner == obj and (entries := held.get(tuple(map(str, key)))):
                        lines += [f"<SUBDATA {name}>", *(" ".join(map(_aux, e)) for e in entries)]
                        lines.append("</SUBDATA>")
        with Path(path).open("a" if append else "w") as out:
            out.write("\n".join([*lines, "}"]) + "\n")
        return ("",)

    def _ProcessAuxFile(self, path: str) -> Reply:
        """DATA sections' SUBDATA blocks, each replacing its object's list, as Simulator does."""
        from esapp._aux import tokens

        obj, keys, owner, block = "", 0, (), None
        for raw in Path(path).read_text(encoding="cp1252").splitlines():
            line = raw.strip()
            if found := re.match(r"DATA\s*\((\w+)\s*,\s*\[([^\]]*)\]", line):
                obj, keys = found[1], len(self._keys(found[1]))
            elif not line or line.startswith("//") or line in ("{", "}"):
                continue
            elif line.startswith("<SUBDATA"):
                block = line.removeprefix("<SUBDATA").rstrip(">").strip()
                self.subdata.setdefault((obj, block), {})[owner] = []
            elif line.startswith("</SUBDATA"):
                block = None
            elif block is None:
                owner = tuple(tokens(line)[:keys])
            else:
                self.subdata[(obj, block)][owner].append(list(map(_parsed, tokens(line))))
        return ("",)

    # ------------------------------------------------------------------ transient
    def _TSGetContingencyResults(
        self, ctg: str, requests: list[str], start: float | None, stop: float | None
    ) -> Reply:
        held = self.results.get(ctg)
        header: list[tuple[str, ...]] = []
        columns: list[list[float] | None] = []
        seen: set[_Stored] = set()
        for request in requests:
            found = _REQUEST.match(request)
            if found is None:
                continue
            obj, keys, field = found[1], tuple(re.findall(r"'([^']*)'", found[2])), found[3]
            if not self._exists(obj, keys) or (obj, keys, field) in seen:
                continue  # like Simulator: unknown objects and repeats are left out, silently
            seen.add((obj, keys, field))
            header.append((f"{obj} ", f"{' '.join(keys)} ", " ", " ", f"{field} ", field))
            columns.append(None if held is None else held[1].get((obj, keys, field)))
        while columns and columns[-1] is None:  # trailing fields without data are dropped
            columns.pop()
        if held is None or not columns:
            return ("", tuple(header), (None,))
        rows = [
            (_time(t), *(_value(0.0 if c is None else c[i]) for c in columns))
            for i, t in enumerate(held[0])
            if (start is None or t >= start) and (stop is None or t <= stop)
        ]
        return ("", tuple(header), tuple(rows) or (None,))

    def _solve(self, name: str, until: float | None) -> str:
        ctgs = self.tables.get("TSContingency")
        if ctgs is None or not (ctgs["TSCTGName"] == name).any():
            return f"Please provide valid contingency {name!r}"
        elements = self.tables.get("TSContingencyElement")
        mine = (
            pd.DataFrame(columns=["TSCTGName"])
            if elements is None
            else elements[elements["TSCTGName"] == name].reset_index(drop=True)
        )
        time, series = self.dynamics(name, mine) if self.dynamics else ([], {})
        options = self.tables.get("Transient_Options")
        stored = options is None or is_yes(options.iloc[0].get("TSStoreResultsInRAM", "YES"))
        every = (
            1 if options is None else int(options.iloc[0].get("TSOSaveResultsTimeStepsPerSave", 1))
        )
        kept = [i for i, t in enumerate(time) if (until is None or t <= until) and i % every == 0]
        recorded = {
            (ref.schema.type, tuple(str(k) for k in ref.key), field): [values[i] for i in kept]
            for (ref, field), values in series.items()
            if stored and self._recorded(ref, field)
        }
        self.results[name] = ([time[i] for i in kept], recorded)
        if "CTGSolved" not in ctgs.columns:
            ctgs["CTGSolved"] = pd.Series("NO", index=ctgs.index, dtype=object)
        ctgs.loc[ctgs["TSCTGName"] == name, "CTGSolved"] = "YES"
        return ""

    def _recorded(self, ref: GObject, field: str) -> bool:
        obj = ref.schema.type
        hits = self._match(obj, dict(zip(ref.schema.key_names, ref.key, strict=True)))
        if not hits:
            return False
        row = self.tables[obj].loc[hits[0]]
        names = ["TSSaveAll", *(f.name for f in flags(type(ref).schema.field(field)))]
        return any(is_yes(row[n]) for n in names if n in row.index)

    def _store_all(self, obj: str, value: str) -> None:
        names = list(self.tables) if obj == "ALL" else [obj]
        for name in names:
            table = self.tables.get(name)
            gtype = getattr(import_module("esapp.grid"), name, None)
            if table is None or gtype is None:
                continue
            for f in gtype.schema.attributes:
                if f.name.startswith("TSSave") and f.role & Role.ENTERABLE:
                    table[f.name] = pd.Series(value, index=table.index, dtype=object)

    # ------------------------------------------------------------------ selections
    def _rows(self, obj: str, where: str) -> pd.DataFrame | str | None:
        """The rows ``where`` selects, None when there are none, or an error message."""
        table = self.tables.get(obj)
        if table is None:
            if _DEVICE.match(where or ""):
                return f'Filter "{where}" cannot be found.'
            return None
        mask = self._select(obj, table, where)
        return mask if isinstance(mask, str) else table[mask]

    def _select(self, obj: str, table: pd.DataFrame, where: str) -> pd.Series | str:
        where = (where or "").strip()
        everything = pd.Series(True, index=table.index)
        if where in ("", "ALL", "AREAZONE"):
            return everything
        if where == "SELECTED":
            return (
                table.get("Selected", pd.Series("NO", index=table.index)).map(is_yes).astype(bool)
            )
        if device := _DEVICE.match(where):
            keys = tuple(re.findall(r"'([^']*)'", device[2]))
            mask = everything
            for name, value in zip(self._keys(obj), keys, strict=False):
                mask = mask & (table[name].astype(str) == value)
            if len(keys) != len(self._keys(obj)) or not mask.any():
                return f'Filter "{where}" cannot be found.'
            return mask
        named = self._named_filter(obj, where)
        if named is not None:
            return self._evaluate(obj, table, *named)
        if (found := _CONDITION.match(where)) and found[1] in table.columns:
            words = [q or b for q, b in re.findall(r"'([^']*)'|(\S+)", found[3])]
            return _compare(table[found[1]], found[2], words)
        return f'Filter "{where}" cannot be found.'

    def _named_filter(self, obj: str, name: str) -> tuple[str, list[tuple[str, str, str]]] | None:
        filters = self.tables.get("Filter")
        if filters is None:
            return None
        hit = filters[(filters["ObjectType"] == obj) & (filters["FilterName"] == name)]
        if hit.empty:
            return None
        conditions = self.tables.get(
            "Condition", pd.DataFrame(columns=["ObjectType", "FilterName"])
        )
        mine = conditions[(conditions["ObjectType"] == obj) & (conditions["FilterName"] == name)]
        rows = [
            (str(r["VariableName"]), str(r["ConditionType"]), str(r.get("ConditionValue", "")))
            for _, r in mine.sort_values("ConditionNumber").iterrows()
        ]
        return str(hit.iloc[0].get("FilterLogic", "AND")), rows

    def _evaluate(
        self, obj: str, table: pd.DataFrame, logic: str, rows: list[tuple[str, str, str]]
    ) -> pd.Series | str:
        masks: list[pd.Series] = []
        for field, op, value in rows:
            if field == "_UseAnotherFilter":
                inner = self._select(obj, table, value)
                if isinstance(inner, str):
                    return inner
                masks.append(inner)
            else:
                column = (
                    table[field] if field in table.columns else pd.Series("", index=table.index)
                )
                masks.append(_compare(column, op, value.split()))
        if not masks:
            return pd.Series(True, index=table.index)
        combined = masks[0]
        for mask in masks[1:]:
            combined = (combined & mask) if logic.upper() == "AND" else (combined | mask)
        return combined

    def _delete(self, obj: str, where: str) -> str:
        table = self.tables.get(obj)
        if table is None:
            return ""
        mask = self._select(obj, table, where)
        if isinstance(mask, str):
            return mask
        gone = table[mask]
        self.tables[obj] = table[~mask].reset_index(drop=True)
        if obj == "Filter":  # a filter takes its conditions with it
            for _, row in gone.iterrows():
                self._drop("Condition", ObjectType=row["ObjectType"], FilterName=row["FilterName"])
        if obj == "TSContingency":  # and a contingency its elements and results
            for name in gone["TSCTGName"]:
                self._drop("TSContingencyElement", TSCTGName=name)
                self.results.pop(name, None)
        if obj == "Contingency":
            for name in gone["CTGLabel"]:
                self._drop("ContingencyElement", CTGLabel=name)
        return ""

    def _drop(self, obj: str, **match: object) -> None:
        table = self.tables.get(obj)
        if table is None:
            return
        mask = pd.Series(True, index=table.index)
        for field, value in match.items():
            mask &= table[field] == value
        self.tables[obj] = table[~mask].reset_index(drop=True)

    def _mark(self, obj: str, where: str, selected: bool) -> str:
        table = self.tables.get(obj)
        if table is None:
            return ""
        mask = self._select(obj, table, where)
        if isinstance(mask, str):
            return mask
        if "Selected" not in table.columns:
            table["Selected"] = pd.Series("NO", index=table.index, dtype=object)
        word: str = "YES" if selected else "NO"
        table.loc[mask, "Selected"] = word
        return ""

    def _set_data(self, obj: str, fields: list[Any], values: list[Any], where: str | None) -> str:
        if any(isinstance(v, list) for v in values):  # as Simulator refuses [[1.0]]
            return 'Error: invalid identifier character found: "[".'
        if where is None:  # no filter: the key fields are in the list
            row = [_number_or_text(v) for v in values]
            return self._write("SetData", obj, list(map(str, fields)), [row], create=False)[0]
        table = self.tables.get(obj)
        if table is None:
            return f'Filter "{where}" cannot be found.' if _DEVICE.match(where) else ""
        mask = self._select(obj, table, where)
        if isinstance(mask, str):
            return mask
        for field, value in zip(fields, values, strict=True):
            if str(field) not in table.columns:
                table[str(field)] = pd.Series(dtype=object)
            table.loc[mask, str(field)] = _number_or_text(value)
        return ""

    # ------------------------------------------------------------------ helpers
    def _exists(self, obj: str, keys: tuple[str, ...]) -> bool:
        names = self._keys(obj)
        if not names:
            return True  # a keyless object always exists
        table = self.tables.get(obj)
        if table is None or len(keys) != len(names):
            return False
        mask = pd.Series(True, index=table.index)
        for name, value in zip(names, keys, strict=True):
            mask &= table[name].astype(str) == value
        return bool(mask.any())

    def _keys(self, obj: str) -> list[str]:
        if obj not in self.keys:  # tables created by writes: the schema knows their keys
            gtype = getattr(import_module("esapp.grid"), obj, None)
            self.keys[obj] = list(gtype.schema.key_names) if gtype is not None else []
        return self.keys[obj]

    def _match(self, obj: str, values: dict[str, object]) -> list[Any]:
        table = self.tables.get(obj)
        keys = self._keys(obj)
        if table is None or table.empty:
            return []
        mask = pd.Series(True, index=table.index)
        for k in keys:
            mask &= table[k].astype(str) == str(values[k])
        return list(table.index[mask])

    def _write(
        self, method: str, obj: str, fields: list[str], rows: list[list[Any]], *, create: bool
    ) -> Reply:
        keys = self._keys(obj)
        table = self.tables.setdefault(obj, pd.DataFrame(columns=fields, dtype=object))
        gtype = getattr(import_module("esapp.grid"), obj, None)
        ids = [
            f for f in fields if gtype is not None and gtype.schema.field(f).role & Role.IDENTIFIER
        ]
        if missing := [k for k in keys if k not in fields] if not ids else []:
            return (f"{method}: key field(s) {missing} missing for {obj}",)
        updates: list[tuple[list[Any], dict[str, Any]]] = []
        for row in rows:
            values = dict(zip(fields, row, strict=True))
            if ids:  # like Simulator: an identifier in the write is how the object is found
                hits = (
                    [i for i in table.index if table.at[i, ids[0]] == values[ids[0]]]
                    if ids[0] in table.columns
                    else []
                )
            else:
                hits = self._match(obj, values) if keys else list(table.index[:1])
            if not hits and (ids or (not create and keys)):  # a keyless object always exists
                return (f"{method}: {len(rows)} objects not found",)
            updates.append((hits, values))
        for hits, values in updates:
            for name in values:
                if name not in table.columns:
                    table[name] = pd.Series(dtype=object)
            if hits:
                for name, value in values.items():
                    if name not in ids:
                        table.loc[hits, name] = value
            else:
                table.loc[len(table)] = pd.Series(values, dtype=object)
        return ("",)


# ---------------------------------------------------------------------- script parsing
def _statements(text: str) -> list[str]:
    """Split a batch on the semicolons that end statements (not those inside quotes)."""
    out, start, quoted = [], 0, False
    for i, ch in enumerate(text):
        if ch == '"':
            quoted = not quoted
        elif ch == ";" and not quoted:
            if text[start:i].strip():
                out.append(text[start : i + 1].strip())
            start = i + 1
    if text[start:].strip():
        out.append(text[start:].strip())
    return out


def _parse(statement: str) -> tuple[str, list[Any]]:
    """``Name(a, "b", [c, 1]);`` -> ``("Name", [Bare("a"), "b", [Bare("c"), Bare("1")]])``."""
    body = statement.strip().rstrip(";").strip()
    name, _, rest = body.partition("(")
    if not rest:
        return name.strip(), []
    values, _ = _values(rest, 0, ")")
    return name.strip(), values


def _values(text: str, i: int, close: str) -> tuple[list[Any], int]:
    """Comma-separated values up to ``close``: quoted strings, [lists], bare tokens, empty slots."""
    values: list[Any] = []
    while i < len(text):
        while i < len(text) and text[i] == " ":
            i += 1
        if i >= len(text):
            break
        ch = text[i]
        if ch == close:
            return values, i + 1
        if ch == ",":  # an empty slot
            values.append(Bare(""))
            i += 1
            continue
        if ch == '"':
            end = text.index('"', i + 1)
            values.append(text[i + 1 : end])
            i = end + 1
        elif ch == "[":
            inner, i = _values(text, i + 1, "]")
            values.append(inner)
        else:
            j = i
            while j < len(text) and text[j] not in ",)]":
                j += 1
            values.append(Bare(text[i:j].strip()))
            i = j
        while i < len(text) and text[i] == " ":
            i += 1
        if i < len(text) and text[i] == ",":
            i += 1
        elif i < len(text) and text[i] == close:
            return values, i + 1
    return values, i


def _plain(value: object) -> object:
    match value:
        case Array(items):
            return [_plain(i) for i in items]
        case list():
            return [_plain(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
        case _:
            return value


def _compare(column: pd.Series, op: str, values: list[str]) -> pd.Series:
    text = column.astype(str).str.strip()
    numbers = pd.to_numeric(column, errors="coerce")
    first = values[0] if values else ""
    try:
        a = float(first)
        b = float(values[1]) if len(values) > 1 else a
        numeric = True
    except ValueError:
        a = b = 0.0
        numeric = False
    match op:
        case "=" | "equal":
            return numbers == a if numeric else text == first
        case "<>" | "notequal":
            return numbers != a if numeric else text != first
        case ">":
            return numbers > a
        case "<":
            return numbers < a
        case ">=":
            return numbers >= a
        case "<=":
            return numbers <= a
        case "between":
            return (numbers >= a) & (numbers <= b)
        case "notbetween":
            return ~((numbers >= a) & (numbers <= b))
        case "contains":
            return text.str.contains(first, regex=False)
        case "notcontains":
            return ~text.str.contains(first, regex=False)
        case "startswith":
            return text.str.startswith(first)
        case "notstartswith":
            return ~text.str.startswith(first)
        case "isblank":
            return text == ""
        case "notisblank":
            return text != ""
        case _:
            raise ValueError(f"FakeSimAuto does not evaluate the condition {op!r}")


def _typed(value: object, vt: int) -> object:
    try:
        match vt:
            case 5:  # VT_R8
                return float(value)  # pyright: ignore[reportArgumentType]
            case 3:  # VT_I4
                return int(float(value))  # pyright: ignore[reportArgumentType]
            case 8:  # VT_BSTR
                return str(value)
            case _:
                return value
    except (TypeError, ValueError):
        return 0 if vt == 3 else float("nan")


def _number_or_text(value: object) -> Any:
    if isinstance(value, Bare):
        try:
            return float(value) if "." in value or "e" in value.lower() else int(value)
        except ValueError:
            return str(value)
    return value


def _aux(value: object) -> str:
    return f'"{value}"' if isinstance(value, str) else str(value)


def _parsed(token: str) -> int | float | str:
    """A value from an auxiliary file: a number if it reads as one."""
    for kind in (int, float):
        try:
            return kind(token)
        except ValueError:
            pass
    return token


def _value(v: float) -> str:
    return format(float(np.float32(v)), ".15g")  # Simulator: single precision, 15 digits


def _time(t: float) -> str:
    return format(t, ".6f").rstrip("0").rstrip(".")
