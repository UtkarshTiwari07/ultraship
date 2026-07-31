"""The extraction call, its retry ladder, and grounding enforcement.

Ladder, in order. Each rung is cheaper than a wrong answer:

1. Strict-schema call at temperature 0.
2. Validation failure -> re-prompt with the literal Pydantic error text
   appended. Up to ``max_repairs`` attempts. Re-sending an identical prompt
   at temperature 0 would be a wasted call, so the error feedback is the
   thing that changes.
3. Still failing -> return an empty extraction. The pipeline then produces
   a fully-null load at low confidence and the document routes to a human.

There is deliberately no rung that returns a partially-guessed object at
anything above low confidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from . import prompts
from .grounding import money_supported, span_supported
from .llm import LLMClient
from .models import RichExtraction, Warning_
from .normalize import parse_money

SCHEMA_NAME = "rate_confirmation"


@dataclass
class ExtractOutcome:
    rich: RichExtraction
    warnings: list[Warning_] = field(default_factory=list)
    attempts: int = 0
    repaired: bool = False


def extract(text: str, client: LLMClient, max_repairs: int = 2) -> ExtractOutcome:
    system, user = prompts.build(text)
    schema = RichExtraction.model_json_schema()
    warnings: list[Warning_] = []
    attempts = 0
    last_errors: str | None = None

    while attempts <= max_repairs:
        attempts += 1
        prompt = user if last_errors is None else (
            user + "\n\n" + prompts.REPAIR_TEMPLATE.format(errors=last_errors)
        )
        try:
            payload = client.complete_json(system, prompt, schema, SCHEMA_NAME)
        except Exception as exc:  # transport, auth, refusal
            last_errors = f"{type(exc).__name__}: {exc}"
            warnings.append(Warning_(code="LLM_CALL_FAILED", severity="low",
                                     detail=last_errors[:300]))
            continue
        try:
            rich = RichExtraction.model_validate(payload)
        except ValidationError as ve:
            last_errors = ve.json(include_url=False)[:2000]
            warnings.append(Warning_(code="SCHEMA_VALIDATION_FAILED", severity="low",
                                     detail=f"attempt {attempts}: {ve.error_count()} error(s)"))
            continue

        warnings += enforce_grounding(rich, text)
        return ExtractOutcome(rich=rich, warnings=warnings, attempts=attempts,
                              repaired=attempts > 1)

    warnings.append(Warning_(
        code="EXTRACTION_ABANDONED", severity="fatal",
        detail=f"no valid extraction after {attempts} attempt(s); routed to human"))
    return ExtractOutcome(rich=RichExtraction(), warnings=warnings, attempts=attempts,
                          repaired=True)


def enforce_grounding(rich: RichExtraction, source: str) -> list[Warning_]:
    """Null out anything the source text does not actually support.

    Applied to the four header-level values and to every charge. Values are
    discarded rather than downgraded: a field that failed grounding is a
    field the model invented, and an invented value is worse than a null.
    """
    out: list[Warning_] = []

    for attr in ("reference_id", "header_pickup_date", "agreed_amount",
                 "equipment_raw", "total_raw"):
        loc = getattr(rich, attr)
        if loc.value_raw is None:
            continue
        if not span_supported(loc.span, source):
            out.append(Warning_(
                code="SPAN_UNVERIFIED", severity="low",
                detail=f"{attr}: span {loc.span!r} not found in source; value "
                       f"{loc.value_raw!r} discarded"))
            loc.value_raw = None
            loc.span = None

    # Money gets a second, independent check that does not depend on the
    # model having cooperated with the span instruction.
    total = parse_money(rich.total_raw.value_raw)
    if total is not None and not money_supported(total, source):
        out.append(Warning_(code="MONEY_UNGROUNDED", severity="low",
                            detail=f"total {total} does not appear in the source text; "
                                   f"discarded"))
        rich.total_raw.value_raw = None

    kept = []
    for ch in rich.charges:
        amt = parse_money(ch.amount_raw)
        if amt is not None and not money_supported(amt, source):
            out.append(Warning_(code="MONEY_UNGROUNDED", severity="low",
                                detail=f"charge {ch.label!r} amount {amt} not in source; "
                                       f"dropped"))
            continue
        kept.append(ch)
    rich.charges = kept

    return out
