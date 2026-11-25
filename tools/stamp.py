"""Remember what each setup step was built from, so updates only redo what changed.

    python tools/stamp.py changed NAME [--requires PATH ...] -- INPUT ...   exit 10 = redo, 0 = up to date
    python tools/stamp.py save NAME -- INPUT ...

INPUTs are files or folders; folders are fingerprinted by the content of every file in them,
skipping build output and installed packages. Fingerprints live in .setup-state.json (not in git).
A ``--requires`` path that is missing (e.g. node_modules deleted) also means "redo".
Standard library only: it runs before the virtual environment exists.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".setup-state.json"
SKIP = {"node_modules", ".next", ".next-build", "dist", "__pycache__", ".turbo", "coverage", "test-results",
        "playwright-report", ".venv", ".git", "data", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def fingerprint(inputs: list[str]) -> str:
    h = hashlib.sha256()
    for raw in sorted(inputs):
        p = (ROOT / raw).resolve()
        if p.is_file():
            files = [p]
        elif p.is_dir():
            files = []
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = sorted(d for d in dirnames if d not in SKIP)
                files += [Path(dirpath) / f for f in sorted(filenames) if not f.endswith((".pyc", ".log", ".tsbuildinfo"))]
        else:
            h.update(f"missing:{raw}".encode())
            continue
        for f in files:
            h.update(f.relative_to(ROOT).as_posix().encode())
            try:
                h.update(hashlib.sha256(f.read_bytes()).digest())
            except OSError:
                h.update(b"unreadable")
    return h.hexdigest()


def load() -> dict[str, str]:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[0] not in ("changed", "save") or "--" not in argv:
        print(__doc__)
        return 2
    cmd, name = argv[0], argv[1]
    sep = argv.index("--")
    opts, inputs = argv[2:sep], argv[sep + 1:]
    requires = [opts[i + 1] for i, o in enumerate(opts) if o == "--requires" and i + 1 < len(opts)]
    fp = fingerprint(inputs)
    state = load()
    if cmd == "save":
        state[name] = fp
        STATE.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
        return 0
    if any(not (ROOT / r).exists() for r in requires) or state.get(name) != fp:
        return 10
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
