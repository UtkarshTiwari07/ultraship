# Rate confirmation extraction — UltraShip AI Engineer skill test, Part 1

Takes the raw text of a carrier rate confirmation and returns the specified
12-key JSON object, plus an out-of-contract `_meta` block explaining every
assumption it made and everything the schema could not hold.

**The design decision this repo is built on:** the LLM locates values and
quotes its source. Python does everything else — date parsing, unit
handling, charge mapping, multi-stop flattening, arithmetic reconciliation,
confidence. Every judgement call is therefore a pure function with a unit
test, and every one of them can be explained to a VP of Ops without
appealing to what the model felt.

---

## Run it

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=...                       # or ANTHROPIC_API_KEY
python -m ratecon.cli tests/fixtures/provided/LD64408.txt
python -m ratecon.cli path/to/doc.txt --provider anthropic
pytest                                          # no API key needed
```

The test suite runs offline against recorded extractions, so `pytest` costs
nothing and needs no credentials. See *Testing without the model* below.

---

## The output that matters

Sample 2 (`LD64408`) is the document that decides whether this pipeline is
trustworthy, because it contains three stops, an unmapped charge line, and a
header that disagrees with its own stop data.

```json
{
  "load_id": "LD64408",
  "origin":      { "city": "Miami",    "state": "FL", "zip": null },
  "destination": { "city": "San Jose", "state": "CA", "zip": null },
  "pickup_date": "2026-07-28",
  "delivery_date": "2026-08-05",
  "equipment_type": "flatbed",
  "line_haul_rate": 500.0,
  "fuel_surcharge": null,
  "total_rate": 700.0,
  "weight_lbs": null,
  "commodity": "Ceramics; Commodity_t",
  "confidence": "medium"
}
```

`fuel_surcharge` is `null`, not `200.0`. The document's second charge line is
labelled **Carrier Charge**, which is not a fuel surcharge. Putting it there
would produce a schema-clean object that misstates the rate structure, and it
is the single most expensive mistake available on this sample set. The $200
is preserved instead:

```json
"reconciliation": {
  "status": "unmapped_charge",
  "schema_sum": 500.0,          // what a consumer reading only the contract computes
  "document_total": 700.0,
  "delta": 200.0,
  "other_charges": [{ "label": "Carrier Charge", "amount": 200.0 }]
}
```

`schema_sum` is the number that should worry a broker: anything downstream
that adds `line_haul_rate + fuel_surcharge` underpays this carrier by $200.
That is a schema gap, not an extraction error, and the pipeline says which.

---

## How it works

```
raw text
  │
  ├─ preprocess          strip repeated page headers/footers, log what was removed
  │
  ├─ extract             one strict-schema LLM call → RichExtraction
  │                      (verbatim strings + source spans, no typing, no math)
  │
  ├─ grounding           discard any value whose span is not in the source;
  │                      discard any money figure the source never printed
  │
  ├─ infer_date_locale   decide MM/DD vs DD/MM for the whole document
  │
  ├─ project             RichExtraction → the 12 keys, emitting a named
  │                      warning for every discard and every assumption
  │
  └─ confidence          rule engine over the warnings → high / medium / low
```

Two model layers, deliberately separate (`ratecon/models.py`):

- `RichExtraction` — lossless. All stops, all charges, everything verbatim.
- `LoadSchema` — exactly the 12 keys asked for. Nothing added, nothing renamed.

The projection between them is ordinary Python, which is the point. I did
not extend the delivered schema with `stops[]` or `other_charges[]`: a
downstream TMS consumer depends on that shape, and unilaterally widening an
integration contract is the wrong instinct. Everything the schema cannot
hold goes in `_meta`, which is prefixed and documented as non-contract.

### Prompt design

The prompt (`ratecon/prompts.py`) asks for one thing: locate values, quote
their source. It explicitly forbids normalising dates, converting units,
summing charges, renaming charge labels, and merging stops. Four of the five
rules are about not inferring — "a null field is a correct answer; a
plausible invention is not."

Concretely, the model returns `"07/28/2026"`, not `"2026-07-28"`. It returns
a charge labelled `"Carrier Charge"`, not a guess about which charge is fuel.
Whether `07/28` is July 28 or the 7th of the 28th month is decided in
`normalize.py`, where the decision is visible and testable.

### Schema enforcement

Default is OpenAI `json_schema` with `strict: true` — a grammar-level
guarantee that the response is parseable and carries no unknown keys.
Anthropic tool-use with a single forced tool is an equivalent alternative,
and DeepSeek is supported through its OpenAI-compatible function-calling
endpoint (`ratecon/llm.py`); all three sit behind a two-method protocol so
swapping providers is a flag.

The three providers do not offer the same guarantee. OpenAI constrains the
output at the grammar level; Anthropic's forced tool is close to it;
DeepSeek offers only a looser JSON mode plus a narrower beta strict mode.
This pipeline is deliberately indifferent to that difference: the schema is
re-validated client-side with Pydantic, the retry ladder repairs malformed
output, and grounding discards anything the source did not contain. The
provider's guarantee is a convenience that saves a retry, not the thing
keeping bad data out — so the design degrades gracefully onto a weaker
provider instead of breaking.

That guarantee is about shape, not truth. **Structured outputs solve
parsing, not correctness** — which is why grounding, reconciliation and the
confidence engine all exist downstream of it.

Retry ladder (`ratecon/extract.py`):

1. Strict-schema call, temperature 0.
2. Validation failure → re-prompt with the literal Pydantic error text
   appended, up to 2 repairs. Re-sending an identical prompt at temperature 0
   would be a wasted call, so the error feedback is what changes.
3. Still failing → return an empty extraction. The load comes out fully null
   at `low` confidence and routes to a human.

There is no rung that returns a partially-guessed object above `low`.

---

## Confidence

Confidence is a deterministic rule engine (`ratecon/confidence.py`), not the
model's opinion of itself. Asking the model how confident it is would be the
"vibes" the brief rules out; it is also unfalsifiable, so a broker could
never be told *why* a load was held.

| Band | Meaning | Action |
|---|---|---|
| `high` | every check passed | auto-populate the load |
| `medium` | probably right, but something was assumed, discarded or inconsistently restated | populate as a draft, require confirmation before tendering |
| `low` | a critical field is null, or a check guarding money failed | do not auto-populate |

Critical fields are `total_rate`, `pickup_date`, and city+state on both
`origin` and `destination` — the fields that cost money or send freight to
the wrong place.

### Checks, strongest first

| Check | On failure |
|---|---|
| **Span grounding** — every located value must quote a substring that really occurs in the source (whitespace-collapsed, since PDF text is ragged) | value discarded, `low` |
| **Money grounding** — every emitted money figure must appear among the figures printed in the document | value discarded, `low` |
| **Schema validation** — enums, ISO dates, non-negative money | `low` |
| **Charge reconciliation** — `line_haul + fuel` vs document total | `medium` if the gap is a labelled unmapped charge, `low` if unexplained |
| **Total corroboration** — header *Agreed Amount* vs rate-breakdown *Total* | `low` on disagreement; agreement is a positive signal |
| **Pickup-date corroboration** — header *Pickup Date* vs first pickup stop | `medium` on disagreement |
| **Date locale resolved from evidence** rather than assumed | `medium` |
| **Stop count ≤ 2** — above that the schema is provably lossy | `medium` |
| **Weight plausibility** — inside the 1,000–48,000 lb FTL band | `medium` |
| Critical field null | `low` |

Span grounding is the strongest of these and costs nothing: an exact
substring check catches the failure mode that matters most here, a
confidently-formatted value the document never contained. The
`adv_hallucination_bait` fixture returns a phantom `Fuel Surcharge` line, a
fabricated agreed amount and a header date that isn't in the document; all
three are discarded and the load lands at `low`.

### What confidence deliberately is not

- **Not the model's self-report.** See above.
- **Not logprob-based.** Anthropic doesn't expose logprobs, and aggregating
  them across a JSON payload is unreliable. At best a weak future signal,
  never a primary one.
- **Not calibrated.** With 3 documents from 1 template there is no way to
  establish that `high` empirically means "under 0.5% critical-field error
  rate". The mechanism is built; the thresholds need a labelled set. That
  gap is the honest answer, and it is where Part 2 starts.

---

## Failure cases

### Missing fields

Nulls propagate; nothing is back-solved. `LD64408` has no ZIP on either the
Miami or San Jose address and no weight on any stop, so `zip` and
`weight_lbs` are null with `ORIGIN_ZIP_MISSING` / `WEIGHT_MISSING` recorded.
The model is instructed never to infer a ZIP from a city, and grounding
enforces it if the instruction is ignored.

### Conflicting totals

The gap between components and total is classified, not just flagged,
because the three causes mean different things:

| Status | Condition | Confidence |
|---|---|---|
| `balanced` | components reach the total | no penalty |
| `unmapped_charge` | the gap is fully explained by labelled line items with no schema field | `medium` — extraction correct, schema incomplete |
| `arithmetic_mismatch` | the gap is unexplained | `low` — probably an extraction error |
| `missing_components` | a total with no breakdown | `medium`, `line_haul_rate` stays null |
| `missing_total` | components with nothing to check against | `low` |

`line_haul_rate` is never back-solved from the total, and the total is never
recomputed from the components. Either would silently produce a number the
document does not contain, in the one field where being wrong costs money.

### Ambiguous dates

`3/4/26` is resolved by a cascade, strongest evidence first
(`normalize.infer_date_locale`):

1. **Impossible component** — any date in the document with a component
   above 12 fixes the order for all of them. All three provided samples
   resolve here: `07/30/2026` has a second component of 30, so the document
   is MM/DD, and every other numeric date inherits that.
2. **Corroboration** — the document also prints an unambiguous named-month
   date. If exactly one reading of a numeric date matches it, that reading
   wins. The UltraShip template hands this over for free: the header prints
   `30-Jul-2026` while the stops print `07/30/2026`.
3. **Ordering constraint** — if the assumed reading inverts the trip and the
   alternate reading doesn't, the alternate is better evidence than the
   default. Applied only when the locale was assumed, never when the
   document itself settled it. Emits `DATE_LOCALE_FLIPPED`.
4. **US default**, with `DATE_LOCALE_ASSUMED` recorded and confidence capped
   at `medium`. Defaulting is fine; defaulting silently is not.

Two-digit years are clamped into a window around today, since rate
confirmations are near-future documents. `26` is 2026, never 1926.

Delivery preceding pickup after resolution emits `DATE_ORDER_INVALID` at
`low`.

---

## What the schema loses

Findings from the three samples, offered as integration feedback rather than
complaint. All are handled without breaking the contract.

**Multi-stop loads.** `LD64408` has two pickups and one drop; the schema
holds one origin and one destination. Rule applied: origin is the first stop
of kind `pickup` by printed sequence, destination is the last `drop`. Stop
sequence is the route, which is the operational truth on a rate con — sorting
on dates instead would key the decision to the dirtiest field. The
intermediate Chicago pickup survives only in `_meta.stops`, flagged
`MULTI_STOP_FLATTENED`. Anything that dispatches from the contract fields
alone will miss a stop, which is worth knowing before it happens.

**The header disagrees with the stops.** `LD64408`'s header says
`Pickup Date: 03-Aug-2026`, which matches stop 2, while stop 1 is dated
07/28. Either the header names a primary pickup or the stop data is dirty,
and the document cannot settle it. The pipeline keeps the stop-1 date,
records the header value in `_meta.notes.header_pickup_date`, emits
`HEADER_DATE_MISMATCH` and caps confidence. A human resolves it in seconds;
a pipeline that picked silently would be wrong on some unknown fraction of
loads forever.

**No fuel surcharge exists in this template.** Covered above. `fuel_surcharge`
is null on all three samples, correctly.

**Weight has no unit, and is sometimes absent or junk.** `LD64392` prints
`182` with no unit; `LD64408` prints `-` for weight and `g` for quantity.
Reading a bare number as pounds is a documented domain convention rather
than an invention, so the value is kept with an `info` note — but 182 lbs is
outside the FTL plausibility band, which is real evidence of a misread, so
that fires `medium`. Weights are read from the first pickup only: they are
restated at every stop, and summing across stops would report `LD64392` as
364 lbs of ceramics.

**Multiple commodities per load.** `LD64408`'s drop lists two. Joined into
the single `commodity` string in first-appearance order with
`MULTI_COMMODITY_FLATTENED`.

**Equipment enum is narrower than real equipment.** Step deck, Conestoga and
RGN map to `other`, not `flatbed`. They are open-deck family but not
interchangeable when sourcing capacity, and `other` with the raw string
preserved in `_meta` is more honest than a wrong specific answer.

**Load-ID decoys.** `Reference ID` is the load identifier. `PO/Container No`,
the MC number, and truck/trailer numbers are not, and the prompt names them
as such.

---

## Testing without the model

`pytest` runs 62 tests with no API key and no spend. Extraction is served by
`ReplayClient`, which reads recorded `RichExtraction` payloads from
`tests/fixtures/replay/`.

Being precise about what that measures: **the replay fixtures are expected
model output, hand-authored from the source documents, not recordings of
live calls.** They exercise the deterministic two-thirds of the pipeline —
normalisation, grounding, projection, confidence — which is where all the
judgement lives and where a regression would be silent. They say nothing
about how often the model locates values correctly. Measuring *that* needs
labelled real documents, which is Part 2.

`tests/test_golden.py` scores every fixture field by field against
`golden.json` and prints per-field accuracy plus auto-populate coverage.
That is the CI gate that should block a deploy on any prompt, model or
schema change.

### Fixtures

The three provided samples are one template, generated by UltraShip's own
test automation (`test automation pickup`, `Playwright Driver`), and a
Chicago–New York flatbed does not move for $50. They contain none of the
failure cases the brief asks about in requirement 4 — no ambiguous date, no
real fuel surcharge, no missing total. So I wrote fixtures that do:

| Fixture | Exercises | Result |
|---|---|---|
| `adv_ambiguous_date` | `3/4/26` with no in-document calibrator | US default, `DATE_LOCALE_ASSUMED`, `medium` |
| `adv_date_corroborated` | ambiguous numeric plus a named-month header date | resolved by corroboration, `high` |
| `adv_real_fsc` | a genuine `Fuel Surcharge` line | mapped, balanced, `high` |
| `adv_total_no_breakdown` | total with no components | `missing_components`, `line_haul_rate` null |
| `adv_locale_flip` | assumed locale inverts the trip | flipped on the ordering constraint |
| `adv_hallucination_bait` | model returns a phantom charge, a fabricated total and a bad span | all discarded, `low` |

Only 2 of 9 fixtures reach `high`. On a real corpus I would expect that rate
to be much higher — most of the `medium` results here come from documents
authored specifically to trip a check.

---

## Cost and latency

One call per document. A rate confirmation is roughly 1–2k input tokens and
the response is a few hundred, so this runs at well under a cent per
document on a mid-tier model. There is no per-field call, no default
self-consistency pass, and no re-read of the document; the retry ladder only
spends more when validation actually fails.

Self-consistency (k=2–3 extractions, field-level agreement) is real signal
and is the obvious next confidence input, but running it on every document
triples cost for no gain on the ~90% that already pass every cheap check.
It belongs behind a failed check, not in front of one.

---

## Limitations I would fix next

- **Grounding does not cover stop addresses.** Layout-preserving PDF
  extraction interleaves the address column with the date and commodity
  columns, so a reflowed address is not a contiguous substring of the source
  and the span check cannot apply. Addresses are parsed defensively instead
  (anchor on the USPS state code, read the city as the token before it, so
  `Illinois State Police, ..., Chicago, IL` yields Chicago). A proper fix
  uses geometry from the PDF rather than reflowed text.
- **The money-grounding check is one-sided.** It can only catch figures
  absent from the document. A page dense with phone numbers and PO numbers
  weakens it, because more of the number space is "present".
- **No calibration.** Thresholds are reasoned, not measured. Needs a
  labelled set.
- **No template fingerprint registry.** `pipeline.template_fingerprint`
  computes a layout hash on every document but nothing consumes it yet. In
  production it is the input-side drift signal: a rising share of unseen
  fingerprints means a new shipper format has arrived, and it alerts without
  needing any labels.
- **Provenance is recorded but not persisted.** Every result carries provider,
  attempt count, template fingerprint and elapsed time. Those should be
  stamped on a durable extraction record alongside the prompt hash and a
  pinned model snapshot, so a regression can be attributed rather than
  guessed at.

## Repo layout

```
ratecon/
  models.py      RichExtraction (lossless) and LoadSchema (the 12 keys)
  prompts.py     locate-and-cite prompt
  llm.py         OpenAI strict schema / Anthropic tool-use / offline replay
  extract.py     retry ladder + grounding enforcement
  normalize.py   money, dates + locale cascade, addresses, equipment, weight
  grounding.py   span and money guards
  project.py     RichExtraction -> LoadSchema, all flattening rules
  confidence.py  the rule engine
  score.py       field-level scorer
  pipeline.py    orchestrator
  cli.py
tests/
  fixtures/provided/      3 supplied samples, split out of the PDF
  fixtures/adversarial/   6 documents authored for requirement 4
  fixtures/replay/        expected model output, for offline tests
  fixtures/golden.json    field-level expectations
tools/make_fixtures.py    regenerates the fixture set
```
