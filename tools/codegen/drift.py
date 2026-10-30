"""What differs between two schema sources: types and fields added, removed, or re-roled."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, fields

from tools.codegen.model import FieldSpec, ObjectSpec

_COMPARED = tuple(
    f.name
    for f in fields(FieldSpec)
    if f.name not in ("name", "description", "identifier", "nonscalar")
)


@dataclass(frozen=True, slots=True)
class Drift:
    added_types: tuple[str, ...]
    removed_types: tuple[str, ...]
    added_fields: tuple[str, ...]  # "Type.Field"
    removed_fields: tuple[str, ...]
    changed: tuple[str, ...]  # "Type.Field: enterable False -> True"

    def __bool__(self) -> bool:
        return any(
            (
                self.added_types,
                self.removed_types,
                self.added_fields,
                self.removed_fields,
                self.changed,
            )
        )

    def report(self, examples: int = 5) -> list[str]:
        lines: list[str] = []
        for title, items in (
            ("types added", self.added_types),
            ("types removed", self.removed_types),
            ("fields added", self.added_fields),
            ("fields removed", self.removed_fields),
            ("fields changed", self.changed),
        ):
            if items:
                more = f" (+{len(items) - examples} more)" if len(items) > examples else ""
                lines.append(f"{len(items)} {title}: {', '.join(items[:examples])}{more}")
        return lines or ["no differences"]


def compare(old: Iterable[ObjectSpec], new: Iterable[ObjectSpec]) -> Drift:
    before = {o.name: {f.name: f for f in o.fields} for o in old}
    after = {o.name: {f.name: f for f in o.fields} for o in new}
    added: list[str] = []
    removed: list[str] = []
    changed: list[str] = []
    for name in sorted(before.keys() & after.keys()):
        a, b = before[name], after[name]
        added += [f"{name}.{f}" for f in b.keys() - a.keys()]
        removed += [f"{name}.{f}" for f in a.keys() - b.keys()]
        for f in sorted(a.keys() & b.keys()):
            for attr in _COMPARED:
                was, now = getattr(a[f], attr), getattr(b[f], attr)
                if was != now:
                    changed.append(f"{name}.{f}: {attr} {was} -> {now}")
    return Drift(
        tuple(sorted(after.keys() - before.keys())),
        tuple(sorted(before.keys() - after.keys())),
        tuple(sorted(added)),
        tuple(sorted(removed)),
        tuple(changed),
    )
