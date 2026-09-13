"""Tiny hermetic test runner — no pytest, no network, no database.

Discovers tests/test_*.py, runs every `test_*` function, reports pass/fail,
and exits non-zero if anything fails. These are the fast, offline unit tests
over pure logic; the live acceptance checks are the separate test_phase*.py /
verify_phase4.py scripts.

    python run_tests.py
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    files = sorted((ROOT / "tests").glob("test_*.py"))
    passed = failed = 0
    failures: list[str] = []

    for path in files:
        module = _load(path)
        tests = [
            (name, fn)
            for name, fn in vars(module).items()
            if name.startswith("test_") and callable(fn)
        ]
        for name, fn in tests:
            try:
                fn()
                passed += 1
            except Exception:
                failed += 1
                failures.append(f"{path.name}::{name}\n{traceback.format_exc()}")

    print(f"\n{passed} passed, {failed} failed  ({len(files)} files)")
    for f in failures:
        print("\n" + "-" * 70 + f"\nFAIL {f}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
