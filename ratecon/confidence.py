"""Confidence as a rule engine, not a model opinion.

The model is never asked how sure it is. Confidence is derived from checks
that either pass or fail, so a broker can be shown *why* a load was held
for review rather than a number they have to take on faith.

    high    every check passed. Safe to auto-populate a load.
    medium  the read is probably right but something was assumed, discarded
            or restated inconsistently. Auto-populate as a draft; require a
            human to confirm before tendering.
    low     a critical field is missing or a check that guards money failed.
            Do not auto-populate.

Two things this deliberately does not do:

* It does not use the model's self-reported confidence, or token logprobs.
  Logprobs are not exposed by every provider and aggregating them across a
  JSON payload is unreliable; at best it is a weak future signal.
* It does not claim to be calibrated. With three sample documents from one
  template there is no way to establish that "high" empirically means
  "<0.5% critical-field error". The mechanism is here; the thresholds need
  a labelled eval set. See README.
"""

from __future__ import annotations

from .models import Confidence, LoadSchema, Warning_

# Fields where being wrong costs money or moves freight to the wrong place.
CRITICAL_FIELDS = ("total_rate", "pickup_date")
CRITICAL_PLACES = ("origin", "destination")


def score(load: LoadSchema, warnings: list[Warning_]) -> tuple[Confidence, list[str]]:
    """Return the confidence band and the list of reasons that set it."""
    reasons: list[str] = []
    severities = {w.severity for w in warnings}

    if "fatal" in severities:
        reasons += [f"{w.code} (fatal)" for w in warnings if w.severity == "fatal"]
    if "low" in severities:
        reasons += [w.code for w in warnings if w.severity == "low"]

    for f in CRITICAL_FIELDS:
        if getattr(load, f) is None:
            reasons.append(f"CRITICAL_FIELD_NULL:{f}")
    for p in CRITICAL_PLACES:
        place = getattr(load, p)
        if place.city is None or place.state is None:
            reasons.append(f"CRITICAL_FIELD_NULL:{p}")

    if reasons:
        return "low", reasons

    medium = [w.code for w in warnings if w.severity == "medium"]
    if medium:
        return "medium", medium

    return "high", []
