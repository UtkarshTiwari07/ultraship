"""Field-level scoring.

Doc-level accuracy hides which field is broken, so everything here is
per-field. Comparison happens *after* normalisation -- money to cents,
dates to ISO, state to USPS code, equipment to enum -- so "50" and "50.00"
are the same answer and a string diff is never the arbiter.

This is the harness Part 2 describes, running at n=9 instead of n=300. The
mechanism is what matters; the sample size is what a labelling pass buys.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CRITICAL = {"total_rate", "pickup_date", "origin.city", "origin.state",
            "destination.city", "destination.state"}

MONEY_FIELDS = {"total_rate", "line_haul_rate", "fuel_surcharge", "weight_lbs"}
TOL = 0.01


def flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, f"{key}."))
        else:
            out[key] = v
    return out


def equal(field_name: str, got, want) -> bool:
    if got is None or want is None:
        return got is None and want is None
    if field_name in MONEY_FIELDS:
        return abs(float(got) - float(want)) <= TOL
    if isinstance(want, str) and isinstance(got, str):
        return got.strip().lower() == want.strip().lower()
    return got == want


@dataclass
class FieldScore:
    correct: int = 0
    total: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 1.0


@dataclass
class Report:
    per_field: dict[str, FieldScore] = field(default_factory=dict)
    mismatches: list[tuple[str, str, object, object]] = field(default_factory=list)

    def add(self, doc: str, got: dict, want: dict) -> None:
        g, w = flatten(got), flatten(want)
        for key, expected in w.items():
            fs = self.per_field.setdefault(key, FieldScore())
            fs.total += 1
            actual = g.get(key)
            if equal(key, actual, expected):
                fs.correct += 1
            else:
                self.mismatches.append((doc, key, actual, expected))

    @property
    def critical_error_count(self) -> int:
        return sum(1 for _, k, _, _ in self.mismatches if k in CRITICAL)

    def table(self) -> str:
        rows = [f"{'field':<22} {'acc':>6}  {'n':>3}"]
        for k in sorted(self.per_field):
            fs = self.per_field[k]
            rows.append(f"{k:<22} {fs.accuracy:>6.1%}  {fs.total:>3}")
        return "\n".join(rows)
