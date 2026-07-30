"""Golden-set regression gate.

Every fixture is scored field by field against ``golden.json``. This is the
CI job that should block a deploy on any prompt, model or schema change --
the mechanism described in Part 2, running here at n=9.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from ratecon.llm import get_client
from ratecon.pipeline import run
from ratecon.score import Report

TODAY = date(2026, 7, 31)
ROOT = Path(__file__).parent / "fixtures"
GOLDEN = json.loads((ROOT / "golden.json").read_text())


def _text(name: str) -> str:
    for sub in ("provided", "adversarial"):
        p = ROOT / sub / f"{name}.txt"
        if p.exists():
            return p.read_text()
    raise FileNotFoundError(name)


def _result(name: str):
    return run(_text(name), get_client("replay", directory=ROOT / "replay", key=name),
               today=TODAY)


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_document_matches_golden(name):
    res = _result(name)
    rep = Report()
    rep.add(name, res.load.model_dump(), GOLDEN[name])
    assert not rep.mismatches, "\n".join(
        f"{d}: {k} got={g!r} want={w!r}" for d, k, g, w in rep.mismatches)


def test_no_critical_field_errors_at_high_confidence(capsys):
    """The metric that matters: nothing wrong on a load we would auto-book."""
    rep = Report()
    high = 0
    for name, want in GOLDEN.items():
        res = _result(name)
        rep.add(name, res.load.model_dump(), want)
        if res.load.confidence == "high":
            high += 1
    with capsys.disabled():
        print("\n" + rep.table())
        print(f"\nauto-populate coverage: {high}/{len(GOLDEN)} at high confidence")
        print(f"critical-field errors:  {rep.critical_error_count}")
    assert rep.critical_error_count == 0
