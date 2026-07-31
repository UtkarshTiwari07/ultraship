"""Orchestrator. Text in, ExtractionResult out."""

from __future__ import annotations

import hashlib
import re
import time
from datetime import date

from . import confidence as conf
from .extract import extract
from .llm import LLMClient
from .models import ExtractionResult
from .normalize import infer_date_locale
from .project import project

# Footer/header noise that PDF extraction repeats on every page. Stripped
# deterministically and logged, so the model never sees it as data.
_NOISE = [
    re.compile(r"^\s*Powered by UltraShip TMS\s*$", re.M),
    re.compile(r"^\s*Page \d+\s*/\s*\d+\s*$", re.M),
]


def preprocess(text: str) -> tuple[str, int]:
    removed = 0
    for pat in _NOISE:
        text, n = pat.subn("", text)
        removed += n
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), removed


def template_fingerprint(text: str) -> str:
    """Stable hash of the document's structural skeleton.

    Alphanumerics are stripped so two documents from the same template
    hash identically regardless of their values. In production this is the
    input-side drift signal: a rising share of unseen fingerprints means a
    new shipper format has arrived. See README, drift section.
    """
    skeleton = re.sub(r"[0-9]+", "#", text)
    skeleton = re.sub(r"\s+", " ", skeleton)
    lines = sorted({l.strip() for l in skeleton.split(".") if len(l.strip()) > 12})
    return hashlib.sha256(" ".join(lines).encode()).hexdigest()[:16]


def run(text: str, client: LLMClient, today: date | None = None) -> ExtractionResult:
    t0 = time.perf_counter()
    clean, noise_removed = preprocess(text)

    outcome = extract(clean, client)
    locale = infer_date_locale(clean, today)
    load, warns, recon, notes = project(outcome.rich, locale, today)
    warns = outcome.warnings + warns

    band, reasons = conf.score(load, warns)
    load.confidence = band

    return ExtractionResult(
        load=load,
        meta={
            "confidence_reasons": reasons,
            "warnings": [w.model_dump() for w in warns],
            "reconciliation": recon.model_dump(),
            "date_locale": {"locale": locale.locale, "basis": locale.basis,
                            "detail": locale.detail},
            "stops": [s.model_dump() for s in outcome.rich.stops],
            "charges": [c.model_dump() for c in outcome.rich.charges],
            "notes": notes,
            "provenance": {
                "provider": client.name,
                "attempts": outcome.attempts,
                "repaired": outcome.repaired,
                "template_fingerprint": template_fingerprint(clean),
                "noise_lines_removed": noise_removed,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
            },
        },
    )
