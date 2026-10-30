"""Writing PowerWorld script commands and their arguments as text."""

from __future__ import annotations

import math
import numbers
import os
from collections.abc import Sequence
from enum import StrEnum
from functools import singledispatch
from typing import Any

from esapp.schema import Field, GObject

type StrPath = str | os.PathLike[str]


class Kw(StrEnum):
    """Script keywords, sent to Simulator without quotes."""

    ALL = "ALL"
    SELECTED = "SELECTED"
    AREAZONE = "AREAZONE"
    EDIT = "EDIT"
    RUN = "RUN"
    PRESENT = "PRESENT"


class Bare(str):
    """Text written as is, unquoted: a keyword or a field name."""

    __slots__ = ()


def is_yes(value: object) -> bool:
    """Whether a field holds YES, however Simulator pads or cases it."""
    return str(value).strip().upper() == "YES"


def script(command: str, *args: object) -> str:
    """The script text for one command. Trailing ``None`` arguments are left out.

    >>> script("SetData", Gen, [Gen.GenMW], [100.0], Kw.ALL)
    'SetData(Gen, [GenMW], [100.0], ALL);'
    """
    trimmed = list(args)
    while trimmed and trimmed[-1] is None:
        trimmed.pop()
    if not trimmed:
        return f"{command};"
    return f"{command}({', '.join(map(arg, trimmed))});"


def quote(text: str) -> str:
    """``text`` as a script string literal."""
    if '"' in text:
        raise ValueError(f"PowerWorld script strings cannot contain double quotes: {text!r}")
    return f'"{text}"'


@singledispatch
def arg(value: object) -> str:
    """Encode one script argument."""
    raise TypeError(f"cannot encode {type(value).__name__} as a PowerWorld script argument")


@arg.register(str)
def _(value: str) -> str:
    return quote(value)


@arg.register(StrEnum)
def _(value: StrEnum) -> str:
    return value.value


@arg.register(Bare)
def _(value: Bare) -> str:
    return str.__str__(value)


@arg.register(bool)
def _(value: bool) -> str:
    return "YES" if value else "NO"


@arg.register(numbers.Integral)
def _(value: numbers.Integral) -> str:
    return str(int(value))


@arg.register(numbers.Real)
def _(value: numbers.Real) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"PowerWorld scripts cannot express {number}")
    return repr(number)


@arg.register(os.PathLike)
def _(value: os.PathLike[str]) -> str:
    return quote(os.fspath(value))


@arg.register(GObject)
def _(value: GObject) -> str:
    return str(value)  # [BUS 5]


@arg.register(Field)
def _(value: Field[Any]) -> str:
    return value.name


@arg.register(type)
def _(value: type) -> str:
    if issubclass(value, GObject):
        return value.schema.type
    raise TypeError(f"cannot encode class {value.__name__} as a PowerWorld script argument")


@arg.register(list)
@arg.register(tuple)
def _(value: Sequence[object]) -> str:
    if any(item is None for item in value):  # Simulator rejects blank items ("invalid , ...")
        raise ValueError(f"PowerWorld script lists cannot have blank items: {value!r}")
    return f"[{', '.join(map(arg, value))}]"


@arg.register(type(None))
def _(value: None) -> str:
    return ""
