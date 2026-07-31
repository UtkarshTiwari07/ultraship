"""Hallucination guards.

Two cheap, deterministic checks that between them catch the failure mode
that matters most here: a confidently-formatted value the document never
contained.

1. Span grounding -- the model must return, for each located value, a
   contiguous substring of the source. If that substring is not actually
   present, the value is discarded. Whitespace is collapsed on both sides
   first, because PDF text extraction produces ragged runs of spaces.

2. Numeric subset -- every money figure the pipeline is about to emit must
   already appear in the source text. Money is the field where a wrong
   value costs real dollars, so it gets a second, independent check that
   does not rely on the model cooperating with the span instruction.
"""

from __future__ import annotations

import re

from .normalize import harvest_money


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def span_supported(span: str | None, source: str) -> bool:
    """True if ``span`` really occurs in ``source``."""
    if not span:
        return False
    needle = _flat(span)
    if len(needle) < 3:
        return False
    return needle in _flat(source)


def money_supported(value: float | None, source: str, tolerance: float = 0.01) -> bool:
    """True if ``value`` appears among the money figures printed in the source."""
    if value is None:
        return True  # nothing asserted, nothing to verify
    for candidate in harvest_money(source):
        if abs(candidate - value) <= tolerance:
            return True
    return False
