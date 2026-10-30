"""``pw[...]``: reading and writing grid data by object type, field and selection."""

from __future__ import annotations

import math
import re
import tempfile
import warnings
from collections.abc import Generator, Iterable, Iterator, Sequence
from contextlib import contextmanager
from functools import partial
from numbers import Real
from pathlib import Path
from typing import Any, cast, overload

import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype

from esapp._aux import read_subdata, read_text, write_subdata
from esapp._frames import frame
from esapp.errors import PowerWorldError, ReadOnlySessionError, SchemaWarning
from esapp.schema import Clause, Conditions, Field, GObject, Role, SubData
from esapp.schema._core import filter_text
from esapp.simauto import VT_BSTR, VT_I4, VT_R8, VT_VARIANT, Kw, SimAuto, script

type FieldLike = str | Field[Any]
type FieldSpec = FieldLike | Sequence[FieldLike] | slice
type Where = str | Kw | GObject | Clause | Conditions
"""Which objects: a named filter, SELECTED / AREAZONE / ALL, one object, or conditions."""

_PACKAGE = str(Path(__file__).parent)
_SETTABLE = Role.KEY | Role.ALT_KEY | Role.REQUIRED | Role.ENTERABLE | Role.IDENTIFIER
_VT: dict[type, int] = {int: VT_I4, float: VT_R8, str: VT_BSTR}
_NONSCALAR = re.compile(r"non-scalar field (\S+) to")


class Indexable:
    """``pw[...]``, and the selections it takes. See :class:`esapp.PowerWorld`."""

    simauto: SimAuto
    _read_only: bool = False
    _filters: Iterator[int]
    _learned: set[tuple[str, str]]  # non-scalar fields met at run time: (object type, field)
    _scratch: Path | None

    @property
    def scratch(self) -> Path:
        """A temporary folder for files Simulator writes in this session, deleted on close."""
        if self._scratch is None:
            self._scratch = Path(tempfile.mkdtemp(prefix="esapp-"))
        return self._scratch

    # ------------------------------------------------------------------ reads
    @overload
    def __getitem__(self, key: Field[Any], /) -> pd.Series: ...
    @overload
    def __getitem__(
        self,
        key: type[GObject]
        | GObject
        | tuple[type[GObject] | GObject, FieldSpec]
        | tuple[type[GObject], FieldSpec, Where],
        /,
    ) -> pd.DataFrame: ...
    def __getitem__(self, key: object, /) -> pd.DataFrame | pd.Series:
        match key:
            case Field(owner=gtype):
                field = _field(gtype, key)
                return _as_series(self._read(gtype, [field]), field)
            case type() if issubclass(key, GObject):
                return self._read(key, [])
            case GObject():
                return self._read_one(key, [])
            case (type() as gtype, SubData() as sub, *where) if issubclass(gtype, GObject):
                return self._read_subdata(_list_of(gtype, sub), as_where(where[0]) if where else "")
            case (GObject() as ref, SubData() as sub):
                return self._read_subdata(_list_of(type(ref), sub), ref)
            case (type() as gtype, fields) if issubclass(gtype, GObject):
                return self._read(gtype, select_fields(gtype, fields))
            case (type() as gtype, fields, where) if issubclass(gtype, GObject):
                return self._read(gtype, select_fields(gtype, fields), as_where(where))
            case (GObject() as ref, fields):
                return self._read_one(ref, select_fields(type(ref), fields))
            case _:
                raise TypeError(f"unsupported index {key!r}; see help(esapp.PowerWorld)")

    def _read(
        self, gtype: type[GObject], fields: Sequence[Field[Any]], where: Where = ""
    ) -> pd.DataFrame:
        _warn_schema(gtype, fields, writing=False)
        columns = list(dict.fromkeys([*gtype.schema.keys, *fields]))
        if not columns:
            return pd.DataFrame()
        names = [c.name for c in columns]
        with self._selection(where) as selected:
            text = "" if selected is Kw.ALL else str(selected)
            while True:  # each pass either reads, or learns one more non-scalar field
                try:
                    cols = self.simauto.GetParamsTypedCols(
                        gtype.schema.type, names, text, [_vt(c, self._learned) for c in columns]
                    )
                    break
                except PowerWorldError as err:
                    if not _learn_nonscalar(gtype, err, self._learned):
                        raise
        return frame(cols, columns)

    def _read_one(self, ref: GObject, fields: Sequence[Field[Any]]) -> pd.DataFrame:
        # A device filter selects exactly this object; a missing one raises ObjectNotFound.
        return self._read(type(ref), fields, ref if ref.key else "")

    def _read_subdata(self, sub: SubData, where: Where) -> pd.DataFrame:
        """One row per entry of ``sub`` in each object ``where`` selects; Simulator hands
        SUBDATA over only in auxiliary files, so this writes one and reads it back."""
        gtype, path = sub.owner, self.scratch / "subdata.aux"
        path.unlink(missing_ok=True)  # Simulator writes no file when no object is selected
        with self._selection(where) as selected:
            text = "" if selected is Kw.ALL else str(selected)
            self.simauto.WriteAuxFile(
                str(path), text, gtype.schema.type, False, gtype.schema.key_names
            )
        return read_subdata(read_text(path), gtype, sub)

    def _write_subdata(
        self, sub: SubData, rows: pd.DataFrame, owners: Iterable[GObject] = ()
    ) -> None:
        """Each object in ``rows`` (and each of ``owners``) gets exactly its rows as ``sub``."""
        path = self.scratch / "subdata.aux"
        keys = [ref.key for ref in owners]
        path.write_text(write_subdata(sub.owner, sub, rows, keys), encoding="cp1252", newline="\n")
        self.simauto.ProcessAuxFile(str(path))

    # ------------------------------------------------------------------ selections
    @contextmanager
    def _selection(self, where: Where) -> Generator[str]:
        """Simulator's filter argument for ``where``, with the filter objects it needs."""
        if (text := filter_text(where)) is not None:
            yield text
        else:
            with self.filter(cast("Clause | Conditions", where)) as name:
                yield name

    @contextmanager
    def filter(self, where: Clause | Conditions) -> Generator[str]:
        """A named Simulator filter for ``where``, deleted when the block ends. Use it to
        reuse one filter across many calls::

            with pw.filter(Bus.AreaNum.eq(1) & (Bus.BusNomVolt > 100)) as hv:
                volts = pw[Bus, Bus.BusPUVolt, hv]
                pw.run(SetData(Bus, [Bus.BusVoltSet], [1.02], hv))
        """
        made: list[str] = []
        try:
            yield self._define_filter(where, made)
        finally:
            if made:  # deleting a filter deletes its conditions
                from esapp.grid import Filter

                self.simauto.RunScriptCommand(
                    "".join(script("Delete", Filter, f"FilterName = '{n}'") for n in made)
                )

    def _define_filter(self, where: Clause | Conditions, made: list[str]) -> str:
        group = where if isinstance(where, Conditions) else Conditions("AND", (where,))
        name = f"esapp~f{next(self._filters)}"
        made.append(name)
        rows: list[list[object]] = []
        for number, clause in enumerate(group.clauses, start=1):
            if isinstance(clause, Conditions):  # a nested group is its own filter, used by name
                rows.append(
                    [number, "_UseAnotherFilter", "meets", self._define_filter(clause, made)]
                )
            else:
                text = " ".join(_condition_value(clause.field, v) for v in clause.values)
                rows.append([number, clause.field.name, clause.op, text])
        owner = group.owner.schema.type
        with self._creating():  # written as-is: these exist only for the call: no schema checks
            self.simauto.ChangeParametersMultipleElement(
                "Filter", ["ObjectType", "FilterName", "FilterLogic"], [[owner, name, group.logic]]
            )
            self.simauto.ChangeParametersMultipleElement(
                "Condition",
                [
                    "ObjectType",
                    "FilterName",
                    "ConditionNumber",
                    "VariableName",
                    "ConditionType",
                    "ConditionValue",
                ],
                [[owner, name, *row] for row in rows],
            )
        return name

    @contextmanager
    def _creating(self) -> Generator[None]:
        previous = self.simauto.CreateIfNotFound
        self.simauto.CreateIfNotFound = True
        try:
            yield
        finally:
            self.simauto.CreateIfNotFound = previous

    # ------------------------------------------------------------------ writes
    @overload
    def __setitem__(self, key: Field[Any], value: Any, /) -> None: ...
    @overload
    def __setitem__(self, key: type[GObject], value: pd.DataFrame, /) -> None: ...
    @overload
    def __setitem__(
        self,
        key: tuple[type[GObject] | GObject, FieldSpec] | tuple[type[GObject], FieldSpec, Where],
        value: Any,
        /,
    ) -> None: ...
    def __setitem__(self, key: object, value: Any, /) -> None:
        self._check_writable()
        match key:
            case Field(owner=gtype):
                self._assign(gtype, [_field(gtype, key)], value)
            case type() if issubclass(key, GObject):
                if not isinstance(value, pd.DataFrame):
                    raise TypeError(
                        f"pw[{key.__name__}] = ... takes a DataFrame, not {type(value).__name__}"
                    )
                self._update(key, value)
            case (type() as gtype, SubData() as sub) if issubclass(gtype, GObject):
                self._write_subdata(_list_of(gtype, sub), _rows(value))
            case (GObject() as ref, SubData() as sub):
                keys = dict(zip(ref.schema.key_names, ref.key, strict=True))
                rows = _rows(value).assign(**keys)
                self._write_subdata(_list_of(type(ref), sub), rows, [ref])
            case (type() as gtype, fields) if issubclass(gtype, GObject):
                self._assign(gtype, select_fields(gtype, fields), value)
            case (type() as gtype, fields, where) if issubclass(gtype, GObject):
                self._set(gtype, select_fields(gtype, fields), value, as_where(where))
            case (GObject() as ref, fields) if ref.key:
                self._set(type(ref), select_fields(type(ref), fields), value, ref)
            case (GObject() as ref, fields):  # the one object of a keyless type
                self._assign(type(ref), select_fields(type(ref), fields), value)
            case _:
                raise TypeError(f"unsupported index {key!r}; see help(esapp.PowerWorld)")

    def _check_writable(self) -> None:
        if self._read_only:
            raise ReadOnlySessionError(
                "this session is read-only (pw.read_only()); in tests, ask for "
                "@powerworld(isolation='state') or isolation='fresh' to write"
            )

    def _assign(self, gtype: type[GObject], fields: list[Field[Any]], value: Any) -> None:
        _warn_schema(gtype, fields, writing=True)
        names = [f.name for f in fields]
        if len(fields) == 1 and isinstance(value, list | tuple) and len(value) == 1:  # pyright: ignore[reportUnknownArgumentType]
            value = value[0]  # one value per field, as for several fields
        if not gtype.schema.keys:
            self._send(gtype, _serialize_bools(gtype, _keyless_row(names, value)))
            return
        if isinstance(value, pd.Series):
            self._send(gtype, _serialize_bools(gtype, _from_series(gtype, names, value)))
            return
        if (numbers := _numeric_broadcast(value, len(fields))) is not None:
            try:
                self.simauto.RunScriptCommand(script("SetData", gtype, fields, numbers, Kw.ALL))
            except PowerWorldError as err:
                _hint_edit_mode(gtype, names, err)
                raise
            return
        change = self._read(gtype, [])
        if change.empty:
            return
        if len(names) == 1:
            change[names[0]] = value
        else:
            change[names] = value
        self._send(gtype, _serialize_bools(gtype, change))

    def _set(
        self, gtype: type[GObject], fields: list[Field[Any]], value: Any, where: Where
    ) -> None:
        """SetData: one value per field, for the objects ``where`` selects."""
        _warn_schema(gtype, fields, writing=True)
        one = len(fields) == 1 and not (isinstance(value, list | tuple) and len(value) == 1)  # pyright: ignore[reportUnknownArgumentType]
        values = [value] if one else list(value)  # one value per field, a scalar for one field
        if len(values) != len(fields):
            raise ValueError(f"{len(fields)} fields take {len(fields)} values, not {len(values)}")
        words = [_word(f, v) for f, v in zip(fields, values, strict=True)]
        with self._selection(where) as selected:
            try:
                self.simauto.RunScriptCommand(script("SetData", gtype, fields, words, selected))
            except PowerWorldError as err:
                _hint_edit_mode(gtype, [f.name for f in fields], err)
                raise

    def _update(self, gtype: type[GObject], df: pd.DataFrame) -> None:
        try:
            self._send(gtype, prepare(gtype, df))
        except PowerWorldError as err:
            if "not found" in err.message.lower():
                err.add_note(
                    f"pw[{gtype.__name__}] = df updates existing objects; "
                    f"create new ones with pw.create({gtype.__name__}, df)"
                )
            raise

    def _send(self, gtype: type[GObject], df: pd.DataFrame) -> None:
        names = [str(c) for c in df.columns]
        try:
            self.simauto.ChangeParametersMultipleElementRect(
                gtype.schema.type, names, df.to_numpy().tolist()
            )
        except PowerWorldError as err:
            _hint_edit_mode(gtype, names, err)
            raise


# ---------------------------------------------------------------------- field selection
def select_fields(gtype: type[GObject], spec: object) -> list[Field[Any]]:
    match spec:
        case slice(start=None, stop=None, step=None):
            return list(gtype.schema.fields)
        case slice():
            raise ValueError("only [:] selects fields; bounded slices are not supported")
        case str() | Field():
            return [_field(gtype, spec)]
        case Iterable():
            return [_field(gtype, item) for item in spec]  # pyright: ignore[reportUnknownVariableType]
        case _:
            raise TypeError(f"fields are names, Field constants, or a list of them, not {spec!r}")


def _list_of(gtype: type[GObject], sub: SubData) -> SubData:
    if sub.owner is not gtype:
        raise ValueError(f"{sub!r} is not a SUBDATA of {gtype.__name__}")
    return sub


def _rows(value: object) -> pd.DataFrame:
    """SUBDATA rows to write: a frame, or an empty list to clear."""
    match value:
        case pd.DataFrame():
            return value
        case list() | tuple() if not value:
            return pd.DataFrame()
        case _:
            raise TypeError(f"SUBDATA takes a DataFrame of rows (or [] to clear), not {value!r}")


def _field(gtype: type[GObject], item: object) -> Field[Any]:
    match item:
        case str():
            found = gtype.schema.field(item)
        case Field(owner=owner) if owner is gtype:
            found = item
        case Field():
            raise ValueError(f"{item!r} is not a field of {gtype.__name__}")
        case _:
            raise TypeError(f"fields are names or Field constants, not {type(item).__name__}")
    if found.role & Role.INDEXED:
        name = f"{gtype.__name__}.{found.attr}"
        raise ValueError(f"{name} exists only indexed: use {name}[n]")
    return found


def as_where(value: object) -> Where:
    if isinstance(value, str | Kw | GObject | Clause | Conditions):
        return value
    raise TypeError(
        "a selection is a filter name, Kw.SELECTED / AREAZONE / ALL, an object, or conditions "
        f"such as Bus.BusNomVolt > 100, not {value!r}"
    )


def _as_series(df: pd.DataFrame, field: Field[Any]) -> pd.Series:
    keys = field.owner.schema.key_names
    if not keys:
        return df[field.name]
    return df.set_index(keys, drop=field.name not in keys)[field.name]


def _vt(field: Field[Any], learned: set[tuple[str, str]]) -> int:
    """The VARENUM a typed-column read asks for: the field's type, or untyped if not a scalar."""
    if field.role & Role.NONSCALAR or (field.owner.schema.type, field.name) in learned:
        return VT_VARIANT
    return _VT.get(field.dtype, VT_VARIANT)


def _learn_nonscalar(
    gtype: type[GObject], err: PowerWorldError, learned: set[tuple[str, str]]
) -> bool:
    """Remember a field Simulator could not send as a scalar; False if there is nothing new."""
    found = _NONSCALAR.search(err.message)
    if found is None or (key := (gtype.schema.type, found[1])) in learned:
        return False
    learned.add(key)
    warnings.warn(
        f"{gtype.__name__}.{found[1]} is not a scalar: reading it untyped",
        SchemaWarning,
        skip_file_prefixes=(_PACKAGE,),
    )
    return True


# ---------------------------------------------------------------------- write helpers
def prepare(gtype: type[GObject], df: pd.DataFrame) -> pd.DataFrame:
    """The write funnel: Field columns become names, bools become PowerWorld strings.

    Fields the schema does not know or lists as read-only raise a :class:`SchemaWarning`
    but are still sent, since the installed Simulator may accept them. Identifiers (Label,
    ObjectID) look objects up, so they can stand in for the key but not be set beside it.
    """
    if any(isinstance(c, Field) for c in df.columns):
        df = df.rename(columns=lambda c: c.name if isinstance(c, Field) else c)
    names = {str(c) for c in df.columns}
    _warn_schema(gtype, [gtype.schema.field(n) for n in map(str, df.columns)], writing=True)
    identifiers = sorted(n for n in names if gtype.schema.field(n).role & Role.IDENTIFIER)
    if identifiers and any(key_set <= names for key_set in gtype.schema.key_sets):
        raise ValueError(
            f"{identifiers} identify objects in a write, so they cannot be set beside the key: "
            f"write by {identifiers[0]} instead, or set the primary label with "
            f"{gtype.__name__}.LabelAppend[1]"
        )
    return _serialize_bools(gtype, df)


def _warn_schema(gtype: type[GObject], fields: Iterable[Field[Any]], *, writing: bool) -> None:
    """Warn about fields the generated schema does not know (untyped) or, for writes, read-only.

    PowerWorld answers unknown field names with placeholder values instead of an error,
    so a misspelled name would otherwise pass silently.
    """
    fields = list(fields)
    if unknown := [f.name for f in fields if f.dtype is object]:
        warnings.warn(
            f"{gtype.__name__} has no field(s) {unknown} in the generated schema; PowerWorld "
            "answers unknown fields with placeholder values, not an error",
            SchemaWarning,
            skip_file_prefixes=(_PACKAGE,),
        )
    if writing and (
        read_only := [f.name for f in fields if f.dtype is not object and not f.role & _SETTABLE]
    ):
        warnings.warn(
            f"{gtype.__name__} field(s) {read_only} are read-only according to the generated "
            "schema; sending anyway",
            SchemaWarning,
            skip_file_prefixes=(_PACKAGE,),
        )


def _word(field: Field[Any], value: object) -> object:
    """A value for a script: bools in the field's PowerWorld vocabulary (``True`` -> "Closed")."""
    if not isinstance(value, bool | np.bool_):
        return value
    if field.vocab is None:
        raise ValueError(
            f"no bool vocabulary for {field.owner.__name__}.{field.name}: "
            "pass PowerWorld's strings, such as 'Closed' or 'Open'"
        )
    return field.vocab[0] if value else field.vocab[1]


def _condition_value(field: Field[Any], value: object) -> str:
    word = _word(field, value)
    return repr(word) if isinstance(word, float) else str(word)


def _serialize_bools(gtype: type[GObject], df: pd.DataFrame) -> pd.DataFrame:
    """Map Python bools to each field's PowerWorld vocabulary (``True`` -> ``"Closed"``)."""
    out = df
    for name in df.columns:
        column = df[name]
        if is_bool_dtype(column.dtype) or (
            column.dtype == object and column.map(lambda v: isinstance(v, bool | np.bool_)).any()
        ):
            field = gtype.schema.field(str(name))
            out = out.assign(**{str(name): column.map(partial(_word, field)).astype(object)})
    return out


def _numeric_broadcast(value: object, n: int) -> list[Any] | None:
    """One finite number per field when ``value`` is a numeric broadcast, else None."""

    def finite(v: object) -> bool:
        return isinstance(v, Real) and not isinstance(v, bool | np.bool_) and math.isfinite(v)

    if finite(value):
        return [value] * n
    if isinstance(value, list | tuple) and n > 1 and len(value) == n and all(map(finite, value)):
        return list(value)  # pyright: ignore[reportUnknownArgumentType]
    return None


def _from_series(gtype: type[GObject], names: list[str], series: pd.Series) -> pd.DataFrame:
    keys = gtype.schema.key_names
    if len(names) != 1:
        raise ValueError("a Series assigns exactly one field")
    if list(series.index.names) != keys:
        raise ValueError(
            f"the Series must be indexed by {gtype.__name__}'s keys {keys}, "
            f"as pw[{gtype.__name__}.<field>] returns"
        )
    return series.rename(names[0]).reset_index()


def _keyless_row(names: list[str], value: object) -> pd.DataFrame:
    if len(names) == 1:
        return pd.DataFrame([[value]], columns=names)
    if isinstance(value, list | tuple) and len(value) == len(names):
        return pd.DataFrame([list(value)], columns=names)  # pyright: ignore[reportUnknownArgumentType]
    raise ValueError("writing several fields of a keyless object takes one value per field")


def _hint_edit_mode(gtype: type[GObject], names: Iterable[str], err: PowerWorldError) -> None:
    edit_only = [n for n in names if gtype.schema.field(n).role & Role.EDIT_ONLY]
    if edit_only:
        err.add_note(
            f"{edit_only} can only be set in EDIT mode: write inside `with pw.edit_mode():`"
        )
