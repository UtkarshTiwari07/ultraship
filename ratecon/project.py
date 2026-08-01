"""Projection: RichExtraction -> LoadSchema.

This is where the contract's lossiness becomes explicit. The 12-key schema
holds one origin, one destination, one commodity, one weight and two rate
components. Real rate confirmations routinely carry more than that. Rather
than silently dropping the excess or quietly widening the schema, every
projection rule below emits a named warning when it discards or assumes
something.

Projection rules, stated once here so they are reviewable:

* origin       = first stop of kind "pickup", by printed sequence
* destination  = last stop of kind "drop", by printed sequence
* pickup_date  = date on that first pickup (not the header field)
* delivery_date= date on that last drop
* weight_lbs   = sum of commodity weights on the *first pickup only*
                 (weights are restated at each stop; summing across stops
                 would double-count the same freight)
* commodity    = distinct commodity descriptions, first-appearance order,
                 joined with "; "
* line_haul    = charge whose label matches base/line-haul/freight
* fuel         = charge whose label matches fuel/FSC
* everything else -> meta.reconciliation.other_charges, never folded into
  fuel_surcharge
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

from .models import (
    LoadSchema,
    Place,
    Reconciliation,
    RichExtraction,
    Warning_,
)
from .normalize import (
    DateLocale,
    LocaleDecision,
    ParsedDate,
    parse_date,
    parse_equipment,
    parse_money,
    parse_place,
    parse_weight,
)

_LINE_HAUL = re.compile(r"base|line ?haul|freight charge|transportation|linehaul", re.I)
_FUEL = re.compile(r"\bfuel\b|\bfsc\b|surcharge", re.I)
# A "Total" / "Amount Due" row is the document total restated as a line item.
# Real models list it among the rate-breakdown lines; it must not be summed
# into other_charges or it double-counts and breaks reconciliation.
_TOTAL = re.compile(r"\btotal\b|\bamount due\b|\bbalance due\b|\bgrand total\b", re.I)

MONEY_TOL = 0.01


def _w(code: str, severity: str, detail: str) -> Warning_:
    return Warning_(code=code, severity=severity, detail=detail)


def project(
    rich: RichExtraction,
    locale: LocaleDecision,
    today: Optional[date] = None,
) -> tuple[LoadSchema, list[Warning_], Reconciliation, dict]:
    load = LoadSchema()
    warns: list[Warning_] = []
    notes: dict = {}
    effective_locale: DateLocale = locale.locale

    # ---------------------------------------------------------------- load id
    load.load_id = (rich.reference_id.value_raw or "").strip() or None
    if not load.load_id:
        warns.append(_w("LOAD_ID_MISSING", "low", "no reference/load identifier found"))

    # ------------------------------------------------------------------ stops
    pickups = sorted([s for s in rich.stops if s.kind == "pickup"], key=lambda s: s.sequence)
    drops = sorted([s for s in rich.stops if s.kind == "drop"], key=lambda s: s.sequence)
    unknown = [s for s in rich.stops if s.kind == "unknown"]
    if unknown:
        warns.append(_w("STOP_KIND_UNKNOWN", "low",
                        f"{len(unknown)} stop(s) could not be classed pickup/drop"))

    if len(rich.stops) > 2:
        warns.append(_w(
            "MULTI_STOP_FLATTENED", "medium",
            f"{len(rich.stops)} stops ({len(pickups)} pickup, {len(drops)} drop) "
            f"flattened into one origin/destination; intermediate stops are "
            f"preserved in meta.stops only"))

    first_pickup = pickups[0] if pickups else None
    last_drop = drops[-1] if drops else None

    if first_pickup is None:
        warns.append(_w("ORIGIN_MISSING", "low", "no pickup stop found"))
    if last_drop is None:
        warns.append(_w("DESTINATION_MISSING", "low", "no drop stop found"))

    load.origin = _place(first_pickup.location_raw if first_pickup else None,
                         "origin", warns)
    load.destination = _place(last_drop.location_raw if last_drop else None,
                              "destination", warns)

    # ------------------------------------------------------------------ dates
    pu = parse_date(first_pickup.date_raw if first_pickup else None, locale.locale, today)
    dl = parse_date(last_drop.date_raw if last_drop else None, locale.locale, today)

    # Ordering constraint: if the primary reading inverts the trip and the
    # alternate reading fixes it, the alternate is better evidence than our
    # locale default. Only applied when the locale was assumed, not when the
    # document itself settled it.
    if (locale.basis == "assumed_us" and pu.value and dl.value
            and dl.value < pu.value):
        alt_pu = pu.alternate or pu.value
        alt_dl = dl.alternate or dl.value
        if alt_dl >= alt_pu:
            pu, dl = ParsedDate(value=alt_pu), ParsedDate(value=alt_dl)
            effective_locale = "DMY" if locale.locale == "MDY" else "MDY"
            warns.append(_w("DATE_LOCALE_FLIPPED", "medium",
                            "MM/DD reading inverted the trip; switched to DD/MM "
                            "on the ordering constraint"))

    load.pickup_date = pu.value.isoformat() if pu.value else None
    load.delivery_date = dl.value.isoformat() if dl.value else None

    if locale.basis == "assumed_us" and (pu.ambiguous or dl.ambiguous):
        warns.append(_w("DATE_LOCALE_ASSUMED", "medium",
                        f"ambiguous numeric date(s) resolved by assumption: {locale.detail}"))
    if pu.value is None and first_pickup is not None:
        warns.append(_w("PICKUP_DATE_UNPARSED", "low",
                        f"could not parse pickup date {first_pickup.date_raw!r}"))
    if dl.value is None and last_drop is not None:
        warns.append(_w("DELIVERY_DATE_UNPARSED", "low",
                        f"could not parse delivery date {last_drop.date_raw!r}"))
    if pu.value and dl.value and dl.value < pu.value:
        warns.append(_w("DATE_ORDER_INVALID", "low",
                        f"delivery {dl.value} precedes pickup {pu.value}"))

    # Header pickup date is an independent restatement. Agreement is a
    # positive signal; disagreement is unresolvable from the document alone.
    hdr = parse_date(rich.header_pickup_date.value_raw, locale.locale, today)
    if hdr.value and pu.value:
        if hdr.value == pu.value:
            warns.append(_w("PICKUP_DATE_CORROBORATED", "info",
                            "header pickup date matches first pickup stop"))
        else:
            warns.append(_w("HEADER_DATE_MISMATCH", "medium",
                            f"header pickup date {hdr.value} != first pickup stop "
                            f"{pu.value}; kept the stop date"))
    notes["header_pickup_date"] = hdr.value.isoformat() if hdr.value else None
    notes["effective_date_locale"] = effective_locale

    # -------------------------------------------------------------- equipment
    load.equipment_type, eq_notes = parse_equipment(rich.equipment_raw.value_raw)
    for n in eq_notes:
        warns.append(_w("EQUIPMENT_BUCKETED", "medium", n))
    if load.equipment_type is None:
        warns.append(_w("EQUIPMENT_MISSING", "low", "no equipment type found"))
    notes["equipment_raw"] = rich.equipment_raw.value_raw

    # ----------------------------------------------------------------- weight
    weight_total = 0.0
    weight_seen = False
    if first_pickup:
        for c in first_pickup.commodities:
            v, wnotes = parse_weight(c.weight_raw)
            for sev, n in wnotes:
                warns.append(_w("WEIGHT_ASSUMPTION", sev, n))
            if v is not None:
                weight_total += v
                weight_seen = True
    load.weight_lbs = round(weight_total, 2) if weight_seen else None
    if not weight_seen:
        warns.append(_w("WEIGHT_MISSING", "info", "no usable weight on the first pickup"))

    # -------------------------------------------------------------- commodity
    seen: list[str] = []
    seen_keys: set[str] = set()
    for s in rich.stops:
        for c in s.commodities:
            d = (c.description or "").strip()
            if not d:
                continue
            # Normalise for dedup so OCR casing/punctuation variants of one
            # commodity ("Plastic Components." vs "plastic Components") collapse
            # instead of firing a spurious MULTI_COMMODITY_FLATTENED.
            key = re.sub(r"[^a-z0-9]+", " ", d.lower()).strip()
            if key and key not in seen_keys:
                seen_keys.add(key)
                seen.append(d)
    load.commodity = "; ".join(seen) or None
    if len(seen) > 1:
        warns.append(_w("MULTI_COMMODITY_FLATTENED", "medium",
                        f"{len(seen)} distinct commodities joined into one string"))

    # ------------------------------------------------------------------ rates
    recon = _rates(rich, load, warns)

    return load, warns, recon, notes


def _place(raw: Optional[str], which: str, warns: list[Warning_]) -> Place:
    p = parse_place(raw)
    if raw and p.city is None:
        warns.append(_w(f"{which.upper()}_CITY_UNPARSED", "low",
                        f"could not read a city from {raw!r}"))
    if raw and p.zip is None:
        warns.append(_w(f"{which.upper()}_ZIP_MISSING", "info",
                        "no ZIP printed in the address"))
    for n in p.notes:
        warns.append(_w(f"{which.upper()}_ADDRESS_NOTE", "info", n))
    return Place(city=p.city, state=p.state, zip=p.zip)


def _rates(rich: RichExtraction, load: LoadSchema,
           warns: list[Warning_]) -> Reconciliation:
    line_haul = fuel = None
    other: list[dict] = []
    total_from_charge = None

    for ch in rich.charges:
        amt = parse_money(ch.amount_raw)
        if amt is None:
            warns.append(_w("CHARGE_UNPARSED", "low",
                            f"could not read amount from {ch.amount_raw!r}"))
            continue
        # Classify on the primary label only. A parenthetical or second-line
        # note like "(... not included in base rate)" must not make a charge
        # match _LINE_HAUL on the stray word "base". Keep the full (collapsed)
        # label for display in other_charges.
        head = re.split(r"[\n(]", ch.label, maxsplit=1)[0].strip()
        label = " ".join(ch.label.split())
        if _FUEL.search(head):
            fuel = amt if fuel is None else fuel + amt
        elif _LINE_HAUL.search(head):
            line_haul = amt if line_haul is None else line_haul + amt
        elif _TOTAL.search(head):
            # The total restated as a line item -- not a separate charge. Keep
            # it out of other_charges; use it as the document total only if the
            # extraction had no explicit total field.
            if total_from_charge is None:
                total_from_charge = amt
        else:
            other.append({"label": label, "amount": amt})

    total = parse_money(rich.total_raw.value_raw)
    if total is None:
        total = total_from_charge
    load.line_haul_rate = line_haul
    load.fuel_surcharge = fuel
    load.total_rate = total

    schema_sum = None
    if line_haul is not None or fuel is not None:
        schema_sum = round((line_haul or 0.0) + (fuel or 0.0), 2)

    other_sum = round(sum(o["amount"] for o in other), 2) if other else 0.0

    # Classify the gap. The three causes have different meanings and
    # therefore different confidence consequences.
    if total is None and schema_sum is None:
        status = "no_rate_data"
        warns.append(_w("NO_RATE_DATA", "low", "no rate figures found at all"))
        delta = None
    elif total is None:
        status = "missing_total"
        delta = None
        warns.append(_w("TOTAL_MISSING", "low",
                        "components present but no document total to check against"))
    elif schema_sum is None:
        status = "missing_components"
        delta = round(total - other_sum, 2)
        warns.append(_w("RATE_BREAKDOWN_MISSING", "medium",
                        f"total {total} present with no line-haul breakdown; "
                        f"line_haul_rate left null rather than back-solved"))
    else:
        delta = round(total - schema_sum, 2)
        if abs(delta) <= MONEY_TOL:
            status = "balanced"
        elif other and abs(delta - other_sum) <= MONEY_TOL:
            status = "unmapped_charge"
            labels = ", ".join(f"{o['label']} {o['amount']:.2f}" for o in other)
            warns.append(_w(
                "UNMAPPED_CHARGE", "medium",
                f"the {delta:.2f} gap between line_haul+fuel ({schema_sum:.2f}) and "
                f"total ({total:.2f}) is fully explained by charge line(s) with no "
                f"schema field: {labels}. Extraction is correct; the schema is "
                f"incomplete. Not folded into fuel_surcharge."))
        else:
            status = "arithmetic_mismatch"
            warns.append(_w(
                "RATE_ARITHMETIC_MISMATCH", "low",
                f"line_haul+fuel ({schema_sum:.2f}) plus other charges "
                f"({other_sum:.2f}) does not reach total ({total:.2f}); "
                f"unexplained delta {round(delta - other_sum, 2):.2f}"))

    # Header "Agreed Amount" is an independent restatement of the total.
    agreed = parse_money(rich.agreed_amount.value_raw)
    if agreed is not None and total is not None:
        if abs(agreed - total) <= MONEY_TOL:
            warns.append(_w("TOTAL_CORROBORATED", "info",
                            f"header agreed amount {agreed:.2f} matches rate "
                            f"breakdown total"))
        else:
            # A review flag, not a hard block: the rate-breakdown total is the
            # authoritative figure and we keep it, so a disagreeing header
            # restatement means "confirm before tendering" (medium), not "do
            # not populate" (low). Money never auto-books at medium anyway.
            warns.append(_w("TOTAL_DISAGREEMENT", "medium",
                            f"header agreed amount {agreed:.2f} != breakdown total "
                            f"{total:.2f}; kept the breakdown total"))

    return Reconciliation(status=status, schema_sum=schema_sum,
                          document_total=total, delta=delta, other_charges=other)
