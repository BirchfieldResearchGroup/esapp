"""Generate esapp.grid, esapp.ts._catalog and esapp.commands from PowerWorld's references.

    python -m tools.codegen                          # write the files that changed
    python -m tools.codegen --check                  # write nothing; exit 1 if stale (CI)
    python -m tools.codegen --document AUX.pdf       # re-extract the commands and SUBDATA first
    python -m tools.codegen --from-simauto OUT.tsv.gz [--case CASE]
    python -m tools.codegen --compare OTHER.tsv.gz   # what differs from --export

The object schema comes from PowerWorld's Case Object Fields export; the script commands and
each object type's SUBDATA from its "Auxiliary File Format" document (``schema/commands.tsv``
and ``schema/subdata.tsv``, corrected by their ``.toml``). ``--from-simauto`` snapshots the
installed Simulator's own field
lists in the export's layout (generate from it with ``--export``), how many parameters each
command takes into ``schema/command_arity.tsv``, and, given a case, the YES/NO options it
finds into ``schema/option_vocab.toml``.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from tools.codegen import commands, subdata
from tools.codegen.drift import compare
from tools.codegen.emit import MARKER, audit, render, sync, ts_catalog
from tools.codegen.overrides import Overrides
from tools.codegen.sources import FieldExport, SimAutoSource, write_export

ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / "schema" / "case_object_fields.tsv.gz"
OVERRIDES = ROOT / "schema" / "overrides.toml"
OPTION_VOCAB = ROOT / "schema" / "option_vocab.toml"
COMMANDS = ROOT / "schema" / "commands.tsv"
COMMAND_OVERRIDES = ROOT / "schema" / "commands.toml"
COMMAND_ARITY = ROOT / "schema" / "command_arity.tsv"
SUBDATA = ROOT / "schema" / "subdata.tsv"
SUBDATA_OVERRIDES = ROOT / "schema" / "subdata.toml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.codegen", description="Generate esapp's PowerWorld schema code."
    )
    parser.add_argument(
        "--export",
        type=Path,
        default=EXPORT,
        help="Case Object Fields export (default: %(default)s)",
    )
    parser.add_argument(
        "--document",
        type=Path,
        metavar="AUX",
        help="re-extract schema/commands.tsv and schema/subdata.tsv from the Auxiliary File "
        "Format document (.pdf or .txt)",
    )
    parser.add_argument(
        "--from-simauto",
        type=Path,
        metavar="OUT",
        help="snapshot the installed Simulator's field lists to OUT (export layout), then stop",
    )
    parser.add_argument(
        "--case", type=Path, help="with --from-simauto: read option vocabularies from this case"
    )
    parser.add_argument("--compare", type=Path, metavar="OTHER", help="report drift, then stop")
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 if stale")
    args = parser.parse_args(argv)

    if args.compare:
        drift = compare(FieldExport(args.export).objects(), FieldExport(args.compare).objects())
        print(f"{args.export.name} -> {args.compare.name}:")
        print("\n".join(f"  {line}" for line in drift.report()))
        return 0
    if args.from_simauto:
        return snapshot(args.export, args.from_simauto, args.case)
    if args.case:
        parser.error("--case goes with --from-simauto")

    started = time.perf_counter()
    source = FieldExport(args.export)
    exported = list(source.objects())
    if args.document:
        if args.check:
            parser.error("--document rewrites schema/commands.tsv and subdata.tsv: no --check")
        text = commands.read_document(args.document)
        rows = commands.extract(text)
        commands.write_rows(rows, COMMANDS)
        lists = subdata.extract(text, (o.name for o in exported))
        subdata.write_rows(lists, SUBDATA)
        print(f"{args.document.name}: {len(rows)} script commands, {len(lists)} SUBDATA")

    overrides = Overrides.load(OVERRIDES, OPTION_VOCAB)
    objects = overrides.apply(exported)
    kept = {o.name for o in objects}
    lists = subdata.Overrides.load(SUBDATA_OVERRIDES).apply(subdata.read_rows(SUBDATA))
    files = render(objects, overrides, source.label, (r for r in lists if r.owner in kept))
    command_overrides = commands.Overrides.load(COMMAND_OVERRIDES)
    arity = commands.read_arity(COMMAND_ARITY)
    catalog = commands.specs(commands.read_rows(COMMANDS), command_overrides, arity)
    header = f"{MARKER} from {COMMANDS.name} and {COMMAND_OVERRIDES.name}. Do not edit.\n"
    files |= commands.render(catalog, command_overrides, header)
    result = sync(files, ROOT, check=args.check)

    fields = sum(len(o.fields) for o in objects)
    verb = "stale" if args.check else "written"
    print(
        f"{source.label}: {len(objects)} object types, {fields} fields; "
        f"{len(catalog)} script commands -> "
        f"{len(result.changed)} file(s) {verb}, {len(result.stale)} stale module(s) "
        f"{'found' if args.check else 'removed'}, {len(files) - len(result.changed)} unchanged "
        f"({time.perf_counter() - started:.1f}s)"
    )
    if notes := audit(objects):
        print(f"{len(notes)} object type(s) with irregular key positions, e.g. {notes[0]}")
    ts = ts_catalog(objects, overrides)
    recordable = sum(len(g.fields) for g in ts.groups)
    print(
        f"TS catalog: {recordable} fields in {len(ts.groups)} groups; "
        f"{len(ts.unpaired)} numeric TS* fields no flag records (left out)"
    )
    if notes := commands.disagreements(catalog, arity):
        print(f"{len(notes)} command(s) Simulator takes differently than documented, e.g.")
        print("\n".join(f"  {note}" for note in notes[:3]))
    if args.check and (result.changed or result.stale):
        for path in [*result.changed, *result.stale][:20]:
            print(f"  stale: {path}")
        return 1
    return 0


def snapshot(export: Path, out: Path, case: Path | None) -> int:
    """Write the running Simulator's field lists for the export's types to ``out``."""
    from esapp.simauto import ComTransport, SimAuto
    from tools.codegen.sources.simauto import command_arity, option_vocab

    started = time.perf_counter()
    known = list(FieldExport(export).objects())
    transport = ComTransport()
    try:
        simauto = SimAuto(transport)
        simauto.RunScriptCommand("NewCase;")  # field lists need a case, any case
        source = SimAutoSource(simauto, (o.name for o in known))
        objects = list(source.objects())
        write_export(objects, out)
        fields = sum(len(o.fields) for o in objects)
        print(
            f"{source.label}: {len(objects)} object types, {fields} fields -> {out} "
            f"({time.perf_counter() - started:.1f}s)"
        )
        if source.unknown:
            print(f"  {len(source.unknown)} type(s) this Simulator lacks: {source.unknown[:8]}")
        print("\n".join(f"  {line}" for line in compare(known, objects).report()))
        documented = commands.read_rows(COMMANDS)
        arity = command_arity(simauto, (r.name for r in documented if r.signature))
        commands.write_arity(arity, COMMAND_ARITY)
        print(
            f"{len(arity)} of {len(documented)} commands' parameter counts -> {COMMAND_ARITY.name}"
        )
        if case:
            simauto.OpenCase(str(case.resolve()))  # read only; never saved
            vocab = option_vocab(simauto, objects)
            OPTION_VOCAB.write_text(
                f"{MARKER} --from-simauto from {case.name}, {source.label}.\n"
                "# Option fields holding YES or NO; schema/overrides.toml [vocab] wins.\n"
                "[vocab]\n" + "".join(f'"{name}" = ["YES", "NO"]\n' for name in vocab),
                encoding="utf-8",
                newline="\n",
            )
            print(f"{len(vocab)} YES/NO option fields -> {OPTION_VOCAB.name}")
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
