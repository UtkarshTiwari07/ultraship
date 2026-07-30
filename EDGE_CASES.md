# Edge cases for the extraction pipeline

A test checklist that complements the automated suite (`pytest`, 62 tests). Each row is an
input condition and the behaviour the pipeline is expected to produce. Codes in `MONO` are
the machine-readable warnings emitted in `_meta.warnings`; the band column is the confidence
outcome the check drives.

The pipeline's guarantee is not "always right" — it is **"never confidently wrong."** Every
row below either produces a correct answer or degrades to `medium`/`low` with a named reason,
so a wrong value never auto-populates a load silently.

Legend: **band** = resulting confidence contribution · ✅ already covered by a fixture/test ·
🔧 recommended addition.

---

## 1. Schema enforcement / malformed model output

| # | Input condition | Expected behaviour | Band | |
|---|---|---|---|---|
| 1.1 | Model returns syntactically invalid JSON | provider adapter raises → `LLM_CALL_FAILED` → repair rung re-prompts | low if unrecoverable | 🔧 |
| 1.2 | Response missing required keys | Pydantic `ValidationError` → `SCHEMA_VALIDATION_FAILED` → repair with literal error text | low if unrecoverable | 🔧 |
| 1.3 | Response has extra/unknown keys | `extra="forbid"` rejects → repair | low if unrecoverable | 🔧 |
| 1.4 | Wrong types (string where object expected, etc.) | `ValidationError` → repair | low if unrecoverable | 🔧 |
| 1.5 | Transport / auth / refusal error from provider | caught, `LLM_CALL_FAILED`, retried | low if unrecoverable | 🔧 |
| 1.6 | All repair attempts exhausted | `EXTRACTION_ABANDONED` (fatal) → fully-null load, routed to human | **low** | 🔧 |
| 1.7 | Model returns all-null (found nothing) | null load, no invented values | **low** | ✅ (`adv_hallucination_bait` partial) |

> Enum enforcement note: the model's schema (`RichExtraction`) has **no enums**. Equipment,
> dates, and money are raw strings; the enum (`van/reefer/flatbed/other`) is produced by
> `normalize.parse_equipment` in Python, so the model can never emit an out-of-enum value.

## 2. Grounding / hallucination guards

| # | Input condition | Expected behaviour | Band | |
|---|---|---|---|---|
| 2.1 | Located value whose `span` is not in the source | `SPAN_UNVERIFIED` → value discarded (nulled) | low | ✅ |
| 2.2 | Money figure not present anywhere in source | `MONEY_UNGROUNDED` → discarded | low | ✅ |
| 2.3 | Phantom `Fuel Surcharge` line invented | dropped by money grounding | low | ✅ `adv_hallucination_bait` |
| 2.4 | Fabricated agreed amount + bad header date | discarded; load lands low | **low** | ✅ `adv_hallucination_bait` |
| 2.5 | **Known weakness:** fabricated total that coincidentally equals a ZIP/phone number in the doc | passes money grounding (one-sided check) → NOT caught | — | 🔧 document |
| 2.6 | **Known gap:** hallucinated origin/destination **city** | span grounding does not cover reflowed addresses → NOT caught by grounding | — | 🔧 document |

## 3. Missing fields

| # | Input condition | Expected behaviour | Band | |
|---|---|---|---|---|
| 3.1 | No Reference ID | `LOAD_ID_MISSING` (low severity → forces low band) | **low** | ✅ |
| 3.2 | No ZIP in address | `*_ZIP_MISSING` (info), `zip` null | no penalty | ✅ (LD64408) |
| 3.3 | No weight / weight is `-` | `WEIGHT_MISSING` (info), `weight_lbs` null | no penalty | ✅ (LD64408) |
| 3.4 | No pickup stop | `ORIGIN_MISSING` + critical place null | **low** | 🔧 |
| 3.5 | No drop stop | `DESTINATION_MISSING` + critical place null | **low** | 🔧 |
| 3.6 | No pickup date | `CRITICAL_FIELD_NULL:pickup_date` | **low** | 🔧 |
| 3.7 | No equipment | `EQUIPMENT_MISSING` | **low** | 🔧 |
| 3.8 | No commodity | `commodity` null (not critical) | no penalty | 🔧 |
| 3.9 | State parses but city does not | `*_CITY_UNPARSED` + critical place null (city None) | **low** | 🔧 |

## 4. Conflicting totals / reconciliation

| # | Input condition | Expected reconciliation status | Band | |
|---|---|---|---|---|
| 4.1 | line_haul + fuel = total | `balanced` | no penalty | ✅ `adv_real_fsc` |
| 4.2 | Gap fully explained by a labelled non-schema charge | `unmapped_charge`, amount kept in `other_charges`, **not** folded into fuel | medium | ✅ LD64408 ($200 Carrier Charge) |
| 4.3 | Gap unexplained | `arithmetic_mismatch` | **low** | 🔧 |
| 4.4 | Multiple fuel lines / multiple base lines | summed within each bucket | — | 🔧 |
| 4.5 | Total present, no line-haul/fuel breakdown | `missing_components`, `line_haul_rate` stays null (never back-solved) | medium | ✅ `adv_total_no_breakdown` |
| 4.6 | Components present, no document total | `missing_total` | **low** | 🔧 |
| 4.7 | No rate figures at all | `no_rate_data` | **low** | 🔧 |
| 4.8 | Header "Agreed Amount" ≠ breakdown Total | `TOTAL_DISAGREEMENT`, breakdown total kept | **low** | 🔧 |
| 4.9 | Agreed Amount = Total | `TOTAL_CORROBORATED` (positive signal) | no penalty | ✅ |
| 4.10 | Charge amount unparseable | `CHARGE_UNPARSED` | **low** | 🔧 |
| 4.11 | Negative / credit line item | parsed as negative; reflected in delta | — | 🔧 |

## 5. Ambiguous / bad dates

| # | Input condition | Expected behaviour | Band | |
|---|---|---|---|---|
| 5.1 | `3/4/26`, no in-document calibrator | US default MM/DD → `2026-03-04`, `DATE_LOCALE_ASSUMED` | medium | ✅ `adv_ambiguous_date` |
| 5.2 | Ambiguous numeric + unambiguous named-month date | resolved by corroboration | high-eligible | ✅ `adv_date_corroborated` |
| 5.3 | Any date with a component > 12 (e.g. `07/30`) | `impossible_component` fixes locale for the whole doc | no penalty | ✅ (all provided) |
| 5.4 | Genuine DD/MM document (first component > 12) | resolves to DMY | no penalty | 🔧 |
| 5.5 | Assumed MM/DD inverts the trip; DD/MM fixes it | `DATE_LOCALE_FLIPPED` | medium | ✅ `adv_locale_flip` |
| 5.6 | Delivery precedes pickup after resolution | `DATE_ORDER_INVALID` | **low** | 🔧 |
| 5.7 | Two-digit year clamp | `26`→2026 (never 1926); window `today-1 … today+3` | — | ✅ (test_normalize) |
| 5.8 | Header pickup date ≠ first pickup stop | `HEADER_DATE_MISMATCH`, stop date kept | medium | ✅ LD64408 |
| 5.9 | Impossible date (e.g. `02/30/2026`) | primary reading `None` → falls back to alternate reading | — | 🔧 |
| 5.10 | ISO (`2026-07-28`) or named-month (`28-Jul-2026`) forms | parsed directly, unambiguous | no penalty | ✅ |

## 6. Multi-stop / flattening

| # | Input condition | Expected behaviour | Band | |
|---|---|---|---|---|
| 6.1 | More than 2 stops | `MULTI_STOP_FLATTENED`; origin = first `pickup`, destination = last `drop`; middle stops in `_meta.stops` | medium | ✅ LD64408 (3 stops) |
| 6.2 | Multiple pickups | origin = first pickup by printed sequence | — | ✅ LD64408 |
| 6.3 | Multiple drops | destination = last drop | — | 🔧 |
| 6.4 | Stop kind neither pickup nor drop | `STOP_KIND_UNKNOWN` | **low** | 🔧 |
| 6.5 | Weight restated at each stop | counted from **first pickup only** (no double-count) | — | ✅ (project rule) |
| 6.6 | Multiple distinct commodities | `MULTI_COMMODITY_FLATTENED`, joined first-appearance, `"; "` | medium | ✅ LD64408 |
| 6.7 | Pickups only, no drop | `DESTINATION_MISSING` | **low** | 🔧 |

## 7. Address parsing

| # | Input condition | Expected behaviour | |
|---|---|---|---|
| 7.1 | POI-prefixed (`Illinois State Police, …, Chicago, IL`) | city = `Chicago` (anchors on state code, not first token) | ✅ |
| 7.2 | Airport code in city (`… (SJC), San Jose, CA`) | `(SJC)` stripped → `San Jose` | ✅ LD64408 |
| 7.3 | No US state code in address | `no US state code found`, city/state null → **low** | 🔧 |
| 7.4 | State code appears first, no city token before it | note recorded, city null → **low** | 🔧 |
| 7.5 | Leading street number in city token | stripped | ✅ |
| 7.6 | International / non-US address | no state match → **low** | 🔧 |
| 7.7 | ZIP+4 (`95758-7987`) | base 5-digit captured (`95758`) | ✅ |

## 8. Equipment

| # | Input condition | Expected mapping | Band | |
|---|---|---|---|---|
| 8.1 | `Flatbed` / `Reefer`/`Refrigerated` / `Dry Van`/`Van` | flatbed / reefer / van | no penalty | ✅ |
| 8.2 | Open-deck variant (step deck, Conestoga, RGN, lowboy, double drop) | `other` + `EQUIPMENT_BUCKETED` | medium | 🔧 |
| 8.3 | Unrecognised equipment string | `other` + note | medium | 🔧 |
| 8.4 | No equipment | `EQUIPMENT_MISSING` | **low** | 🔧 |

## 9. Weight

| # | Input condition | Expected behaviour | Band | |
|---|---|---|---|---|
| 9.1 | Bare `182`, no unit, **outside** FTL band | assumed lbs (info) **+** `WEIGHT_ASSUMPTION` out-of-band | medium | ✅ LD64392 |
| 9.2 | Value inside FTL band (1,000–48,000) with/without unit | kept, no penalty | no penalty | ✅ `adv_*` |
| 9.3 | `kg` / `kilo` stated | converted ×2.20462 (info) | no penalty | 🔧 |
| 9.4 | `-`, `N/A`, empty | null | no penalty | ✅ LD64408 |
| 9.5 | Junk with no digits (`g`) | null + note | low | 🔧 |
| 9.6 | Over gross limit (> 48,000) | `WEIGHT_ASSUMPTION` out-of-band | medium | 🔧 |

## 10. Preprocessing / infrastructure

| # | Input condition | Expected behaviour | |
|---|---|---|---|
| 10.1 | Repeated page header/footer (`Powered by UltraShip TMS`, `Page 2 / 2`) | stripped before the model sees it, count in `_meta.provenance.noise_lines_removed` | ✅ |
| 10.2 | Ragged PDF whitespace runs | span grounding collapses whitespace on both sides before comparing | ✅ |
| 10.3 | Empty / whitespace-only document | empty extraction → **low** | 🔧 |
| 10.4 | Number-dense document (many phones/POs) | money grounding weakened (more of the number space is "present") — documented limitation | 🔧 document |
| 10.5 | DeepSeek returns no `tool_call` | `RuntimeError` → caught → retry ladder | 🔧 |
| 10.6 | Replay fixture file missing | `FileNotFoundError` (test harness only) | ✅ |
| 10.7 | Provider silently updates the model snapshot | caught downstream by the golden-set CI gate + `template_fingerprint` drift signal | ✅ (mechanism) |

---

## Priority additions

The provided samples are one template and hit none of requirement 4's failure cases, so the
`🔧` rows are the highest-value tests to add before trusting the pipeline on a real corpus.
The already-authored adversarial fixtures cover the headline cases (2.x, 4.2, 4.5, 5.1, 5.2,
5.5); the gaps worth closing first are the **explicit low-confidence routes** (3.4–3.9, 4.3,
4.6–4.8, 6.4, 6.7) — the paths that must reliably *refuse to auto-populate*.
