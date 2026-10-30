"""Where the examples, their captures and their cases live, and how a capture names its script."""

import hashlib
import os
from pathlib import Path

EXAMPLES = Path(__file__).parent / "examples"
OUTPUT = EXAMPLES / "_output"
CASES = {  # what an example opens, and where it is in the cases repository
    "Hawaii40.pwb": "Synthetic/Hawaii40/variants/Hawaii40.SimpleDynamics.pwb",
    "WECC240.pwb": "Other/WECC240/variants/WECC240.SimpleDynamics.pwb",
    "Texas7k.pwb": "Synthetic/Texas7k/Texas7k.pwb",
}


def cases() -> Path:
    """The cases repository: ``$ESAPP_CASES``, else beside this one."""
    root = Path(os.environ.get("ESAPP_CASES", Path(__file__).parents[2] / "cases"))
    if missing := [n for n, path in CASES.items() if not (root / path).is_file()]:
        raise SystemExit(f"{missing} not found in {root}: set ESAPP_CASES to the cases repository")
    return root


def digest(script: Path) -> str:
    """The script's text, line endings aside: what its capture records."""
    return hashlib.sha256(script.read_text(encoding="utf-8").encode()).hexdigest()
