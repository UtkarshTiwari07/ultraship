"""Deterministic normalization. No model calls here, by design.

Everything in this module is a pure function over strings, which means the
pipeline's judgement calls are unit-testable and auditable. The LLM never
decides whether "3/4/26" is March or April.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal, Optional

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_name) if m})

DateLocale = Literal["MDY", "DMY"]
LocaleBasis = Literal[
    "impossible_component", "corroboration", "ordering_constraint", "assumed_us"
]

# Two-digit years are clamped into a window around today. Rate cons are
# near-future documents; "26" is never 1926.
_YEAR_WINDOW_BACK = 1
_YEAR_WINDOW_FWD = 3

_NUMERIC_DATE = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b")
_NAMED_DATE = re.compile(
    r"\b(\d{1,2})[\-\s]([A-Za-z]{3,9})[\-\s](\d{4})\b|"
    r"\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\b"
)
_MONEY = re.compile(r"-?\$?\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|-?\$?\s?\d+(?:\.\d{1,2})?")

US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
    "VA","WA","WV","WI","WY","DC",
}

# Open-deck family. Mapped to "other", not "flatbed" -- see README.
_OPEN_DECK_VARIANTS = {"step deck", "stepdeck", "conestoga", "rgn", "lowboy", "double drop"}


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------

def parse_money(raw: Optional[str]) -> Optional[float]:
    """'$1,250.50 USD' -> 1250.5. Returns None on anything unparseable."""
    if not raw:
        return None
    cleaned = raw.replace(",", "").replace("$", "")
    cleaned = re.sub(r"\b[A-Z]{3}\b", "", cleaned).strip()
    m = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    if not m:
        return None
    try:
        # Decimal first so 0.1+0.2 style drift never reaches reconciliation.
        return float(Decimal(m.group(0)).quantize(Decimal("0.01")))
    except InvalidOperation:
        return None


def harvest_money(text: str) -> set[float]:
    """Every money-ish number in the source. Used as a hallucination guard."""
    out = set()
    for m in _MONEY.finditer(text):
        v = parse_money(m.group(0))
        if v is not None:
            out.add(v)
    return out


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

def _clamp_year(y: int, today: date) -> int:
    if y >= 100:
        return y
    for century in (2000, 1900):
        cand = century + y
        if today.year - _YEAR_WINDOW_BACK <= cand <= today.year + _YEAR_WINDOW_FWD:
            return cand
    return 2000 + y


def parse_named_date(raw: str, today: Optional[date] = None) -> Optional[date]:
    """Unambiguous forms only: '28-Jul-2026', 'Jul 28, 2026', '2026-07-28'."""
    today = today or date.today()
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", raw)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    m = _NAMED_DATE.search(raw)
    if not m:
        return None
    if m.group(1):
        day, mon_s, year = m.group(1), m.group(2), m.group(3)
    else:
        mon_s, day, year = m.group(4), m.group(5), m.group(6)
    mon = MONTHS.get(mon_s.lower())
    if not mon:
        return None
    try:
        return date(_clamp_year(int(year), today), mon, int(day))
    except ValueError:
        return None


@dataclass
class NumericDate:
    a: int  # first component as printed
    b: int  # second component as printed
    y: int
    text: str

    @property
    def ambiguous(self) -> bool:
        return self.a <= 12 and self.b <= 12

    def resolve(self, locale: DateLocale) -> Optional[date]:
        mon, day = (self.a, self.b) if locale == "MDY" else (self.b, self.a)
        try:
            return date(self.y, mon, day)
        except ValueError:
            return None


def find_numeric_dates(text: str, today: Optional[date] = None) -> list[NumericDate]:
    today = today or date.today()
    out = []
    for m in _NUMERIC_DATE.finditer(text):
        a, b, y = int(m.group(1)), int(m.group(2)), _clamp_year(int(m.group(3)), today)
        out.append(NumericDate(a=a, b=b, y=y, text=m.group(0)))
    return out


@dataclass
class LocaleDecision:
    locale: DateLocale
    basis: LocaleBasis
    detail: str


def infer_date_locale(text: str, today: Optional[date] = None) -> LocaleDecision:
    """Decide MM/DD vs DD/MM for the whole document.

    Cascade, strongest evidence first:

    1. impossible_component -- some date has a component > 12, which fixes
       the order for every numeric date in the document.
    2. corroboration -- the document also prints an unambiguous named-month
       date (e.g. the header's '30-Jul-2026'). If exactly one reading of a
       numeric date matches it, that reading wins. This is what the sample
       documents hand us for free.
    3. assumed_us -- fall back to MM/DD, and say so out loud.

    The ordering constraint (delivery >= pickup) is applied later in
    project.py, where both dates are known.
    """
    today = today or date.today()
    numerics = find_numeric_dates(text, today)

    for nd in numerics:
        if nd.a > 12:
            return LocaleDecision("DMY", "impossible_component",
                                  f"{nd.text}: first component {nd.a} > 12")
        if nd.b > 12:
            return LocaleDecision("MDY", "impossible_component",
                                  f"{nd.text}: second component {nd.b} > 12")

    named = {d for d in (parse_named_date(m) for m in _iter_named(text)) if d}
    for nd in numerics:
        mdy, dmy = nd.resolve("MDY"), nd.resolve("DMY")
        if mdy in named and dmy not in named:
            return LocaleDecision("MDY", "corroboration",
                                  f"{nd.text} matches named-month date {mdy.isoformat()}")
        if dmy in named and mdy not in named:
            return LocaleDecision("DMY", "corroboration",
                                  f"{nd.text} matches named-month date {dmy.isoformat()}")

    return LocaleDecision("MDY", "assumed_us",
                          "no in-document evidence; defaulted to US MM/DD")


def _iter_named(text: str) -> list[str]:
    return [m.group(0) for m in _NAMED_DATE.finditer(text)] + \
           [m.group(0) for m in re.finditer(r"\b\d{4}-\d{2}-\d{2}\b", text)]


@dataclass
class ParsedDate:
    value: Optional[date] = None
    ambiguous: bool = False
    alternate: Optional[date] = None  # the other reading, if ambiguous


def parse_date(raw: Optional[str], locale: DateLocale,
               today: Optional[date] = None) -> ParsedDate:
    if not raw:
        return ParsedDate()
    named = parse_named_date(raw, today)
    if named:
        return ParsedDate(value=named)
    nds = find_numeric_dates(raw, today)
    if not nds:
        return ParsedDate()
    nd = nds[0]
    primary = nd.resolve(locale)
    other = nd.resolve("DMY" if locale == "MDY" else "MDY")
    if primary is None:
        return ParsedDate(value=other)
    return ParsedDate(value=primary, ambiguous=nd.ambiguous,
                      alternate=other if other != primary else None)


# --------------------------------------------------------------------------
# Addresses
# --------------------------------------------------------------------------

@dataclass
class ParsedPlace:
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    notes: list[str] = field(default_factory=list)


def parse_place(raw: Optional[str]) -> ParsedPlace:
    """Pull city/state/zip out of a freeform US address.

    Anchors on the two-letter state code and reads the city as the token
    immediately before it. This survives POI-prefixed addresses like
    'Illinois State Police, 100 W Randolph St, Chicago, IL 60601, USA',
    where naive first-token parsing would return 'Illinois State Police'.
    """
    if not raw:
        return ParsedPlace()
    flat = re.sub(r"\s+", " ", raw.replace("\n", ", ")).strip()
    out = ParsedPlace()

    z = re.search(r"\b(\d{5})(?:-\d{4})?\b", flat)
    if z:
        out.zip = z.group(1)

    parts = [p.strip(" .") for p in flat.split(",") if p.strip(" .")]
    state_idx = None
    for i, p in enumerate(parts):
        tok = p.split()[0].upper() if p.split() else ""
        if tok in US_STATES:
            state_idx = i
            out.state = tok
            break
    if state_idx is None:
        out.notes.append("no US state code found")
        return out
    if state_idx == 0:
        out.notes.append("state code appeared first; no city token before it")
        return out

    city = parts[state_idx - 1]
    city = re.sub(r"\s*\([^)]*\)", "", city).strip()  # drop '(MIA)' style codes
    city = re.sub(r"^\d+\s+", "", city)
    out.city = city or None
    if not out.city:
        out.notes.append("city token empty after cleanup")
    return out


# --------------------------------------------------------------------------
# Equipment
# --------------------------------------------------------------------------

def parse_equipment(raw: Optional[str]) -> tuple[Optional[str], list[str]]:
    """Map printed equipment text onto the four-value enum.

    Open-deck variants (step deck, Conestoga, RGN) map to "other", not
    "flatbed". They are not interchangeable for a broker sourcing capacity,
    and the enum gives us an honest bucket for them.
    """
    if not raw:
        return None, []
    s = re.sub(r"[^a-z0-9 ]", " ", raw.lower())
    s = re.sub(r"\s+", " ", s).strip()
    notes: list[str] = []

    for variant in _OPEN_DECK_VARIANTS:
        if variant in s:
            return "other", [f"open-deck variant '{raw.strip()}' bucketed as other"]
    if re.search(r"\breefer\b|\brefrigerated\b|\btemp\b|\brf\b", s):
        return "reefer", notes
    if re.search(r"\bflat ?bed\b|\bflat\b|\bfb\b|\bopen deck\b", s):
        return "flatbed", notes
    if re.search(r"\bdry van\b|\bvan\b|\bdv\b", s):
        return "van", notes
    return "other", [f"unrecognised equipment '{raw.strip()}'"]


# --------------------------------------------------------------------------
# Weight
# --------------------------------------------------------------------------
# FTL plausibility band. US tractor-trailer gross limit is 80,000 lbs, of
# which payload is roughly 42,000-48,000 lbs.
FTL_WEIGHT_MIN = 1_000.0
FTL_WEIGHT_MAX = 48_000.0


def parse_weight(raw: Optional[str]) -> tuple[Optional[float], list[tuple[str, str]]]:
    """Return weight in lbs, plus (severity, note) pairs for any assumption.

    An unstated unit is *info*, not a risk: US domestic rate confirmations
    denominate weight in pounds by overwhelming convention, so reading a
    bare number as lbs is a documented domain assumption rather than an
    invention. A weight outside the FTL plausibility band is *medium*,
    because that is real evidence the number was misread.
    """
    if not raw or not raw.strip() or raw.strip() in {"-", "--", "N/A", "n/a"}:
        return None, []
    s = raw.strip().lower()
    m = re.search(r"\d+(?:[,\d]*)(?:\.\d+)?", s)
    if not m:
        return None, [("low", f"weight '{raw.strip()}' contains no number")]
    val = float(m.group(0).replace(",", ""))
    notes: list[tuple[str, str]] = []
    if "kg" in s or "kilo" in s:
        val = round(val * 2.20462, 2)
        notes.append(("info", "converted from kg"))
    elif re.search(r"\blb|\blbs|pound", s):
        pass
    else:
        notes.append(("info", "unit not stated; assumed lbs (US domestic convention)"))
    if not (FTL_WEIGHT_MIN <= val <= FTL_WEIGHT_MAX):
        notes.append(("medium", f"{val:g} lbs outside FTL plausibility band "
                                f"{FTL_WEIGHT_MIN:g}-{FTL_WEIGHT_MAX:g}"))
    return val, notes
