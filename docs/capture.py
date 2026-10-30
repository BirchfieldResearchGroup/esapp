"""Run the documentation's examples against PowerWorld and keep what they print and draw.

    python docs/capture.py [NAME ...]       # every example by default

Needs Simulator with SimAuto and the cases repository beside this one (or at ``$ESAPP_CASES``):
an example opens a copy of each case it names, in a scratch directory, so the cases never
change. Read the Docs cannot run PowerWorld, so the pages show these captures, and a page whose
example changed since its capture fails the build. Each example is executed twice, its figures drawn
light then dark, and must print the same both times.
"""

import contextlib
import io
import json
import runpy
import shutil
import sys
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).parent))
from _examples import CASES, EXAMPLES, OUTPUT, digest
from _examples import cases as cases_repository
from _style import rc

THEMES = (("light", ""), ("dark", "-dark"))


def capture(script: Path, cases: Path, scratch: Path) -> None:
    printed: list[str] = []
    figures = 0
    text = script.read_text(encoding="utf-8")
    for theme, suffix in THEMES:
        here = scratch / f"{script.stem}-{theme}"
        here.mkdir()
        for name in (n for n in CASES if f'"{n}"' in text):
            shutil.copy(cases / CASES[name], here / name)
        with contextlib.chdir(here), plt.rc_context(rc(theme)):
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                runpy.run_path(str(script), run_name="__main__")
            printed.append(stdout.getvalue())
            numbers = plt.get_fignums()
            figures = len(numbers)
            for n, number in enumerate(numbers, 1):
                path = OUTPUT / f"{script.stem}-{n}{suffix}.svg"
                plt.figure(number).savefig(path, bbox_inches="tight", metadata={"Date": None})
            plt.close("all")
    if printed[0] != printed[1]:
        raise SystemExit(f"{script.name} printed differently the second time; it must repeat")
    record = {"sha256": digest(script), "stdout": printed[0], "figures": figures}
    (OUTPUT / f"{script.stem}.json").write_text(
        json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"{script.name}: {len(printed[0].splitlines())} line(s), {figures} figure(s)")


def main(names: list[str]) -> None:
    cases = cases_repository()
    OUTPUT.mkdir(exist_ok=True)
    scripts = sorted(EXAMPLES.rglob("*.py"))
    if unknown := set(names) - {s.stem for s in scripts}:
        raise SystemExit(f"no examples named {sorted(unknown)}")
    # Simulator can hold a run's directory a moment after its session closes: clean up last.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        for script in scripts:
            if not names or script.stem in names:
                capture(script, cases, Path(scratch))


if __name__ == "__main__":
    main(sys.argv[1:])
