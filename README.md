# Rate confirmation extraction — UltraShip AI Engineer skill test (Part 1)

Takes the raw text of a carrier rate confirmation and returns the required
12-key JSON object, plus a non-contract `_meta` block that records every
assumption and everything the schema couldn't hold.

**Core design decision:** the LLM only *locates values and quotes their source*.
Python does everything else — date parsing, unit handling, charge mapping,
multi-stop flattening, arithmetic reconciliation, and confidence. Every judgement
call is a pure function with a unit test, so it can be explained without appealing
to what the model "felt". That split is also the answer to *when not to trust the
model*: it's trusted to find text, never to compute.

---

## Run it

```bash
pip install -r requirements.txt          # just pydantic
pip install openai                        # for --provider openai OR deepseek
# pip install anthropic                   # for --provider anthropic

export OPENAI_API_KEY=...                  # or ANTHROPIC_API_KEY / DEEPSEEK_API_KEY
python -m ratecon.cli path/to/doc.txt --provider openai
python -m ratecon.cli path/to/doc.txt --provider deepseek   # OpenAI-compatible API
```

Providers (`ratecon/llm.py`), all behind one two-method protocol so swapping is a flag:

| `--provider` | Enforcement mechanism | Env for key / model |
|---|---|---|
| `openai` (default) | `json_schema` strict — grammar-level guarantee | `OPENAI_API_KEY` · `RATECON_OPENAI_MODEL` |
| `anthropic` | forced single tool-use | `ANTHROPIC_API_KEY` · `RATECON_ANTHROPIC_MODEL` |
| `deepseek` | forced function-calling, OpenAI-compatible endpoint | `DEEPSEEK_API_KEY` · `RATECON_DEEPSEEK_MODEL` |
| `replay` | serves recorded output from disk (offline, no key) | — |

Model IDs are env-configurable on purpose: pin a dated snapshot in deployment so a
provider-side update can't change behaviour silently.

---

## Test it yourself

Everything below runs **offline with no API key and no spend** — extraction is
served by `ReplayClient` from recorded fixtures in `tests/fixtures/replay/`.

```bash
# 1. Full suite (62 tests: normalization, grounding, projection, confidence, golden gate)
pip install -r requirements-dev.txt
pytest                                     # -> 62 passed

# 2. Run one document end-to-end (recorded model output, deterministic)
python -m ratecon.cli tests/fixtures/provided/LD64408.txt --provider replay
python -m ratecon.cli tests/fixtures/provided/LD64408.txt --provider replay --contract-only

# 3. The golden gate — prints per-field accuracy + auto-populate coverage
pytest tests/test_golden.py -s
```

`LD64408` is the document that proves the pipeline: three stops, a header that
disagrees with its stops, and an unmapped `$200 Carrier Charge`. Expected contract
output — note `fuel_surcharge` stays **null** (the $200 is not fuel) and confidence
is `medium`:

```json
{
  "load_id": "LD64408",
  "origin":      { "city": "Miami",    "state": "FL", "zip": null },
  "destination": { "city": "San Jose", "state": "CA", "zip": null },
  "pickup_date": "2026-07-28", "delivery_date": "2026-08-05",
  "equipment_type": "flatbed",
  "line_haul_rate": 500.0, "fuel_surcharge": null, "total_rate": 700.0,
  "weight_lbs": null, "commodity": "Ceramics; Commodity_t",
  "confidence": "medium"
}
```

Drop `--contract-only` to see `_meta`: the reconciliation record (`schema_sum` 500 vs
`document_total` 700, the $200 preserved in `other_charges`), every warning, the stops,
and provenance. To try a **live** model, install its SDK, export the key, and swap
`--provider replay` for `openai` / `anthropic` / `deepseek`.

> Note: `pytest.ini` sets `pythonpath = .` so a bare `pytest` (what CI runs) can
> import the `ratecon` package, not only `python -m pytest`.

---

## How it works

```
raw text
  ├─ preprocess    strip repeated page headers/footers, log what was removed
  ├─ extract       one strict-schema LLM call -> RichExtraction (verbatim + spans)
  ├─ grounding     discard any value whose span isn't in the source; any money
  │                figure the source never printed
  ├─ infer locale  decide MM/DD vs DD/MM for the whole document from evidence
  ├─ project       RichExtraction -> the 12 keys, one named warning per assumption
  └─ confidence    rule engine over the warnings -> high / medium / low
```

Two model layers (`ratecon/models.py`), deliberately separate:

- **`RichExtraction`** — lossless: all stops, all charges, everything verbatim with a
  source span. No typing, no math.
- **`LoadSchema`** — exactly the 12 keys asked for. Produced from `RichExtraction` by
  ordinary Python (`project.py`).

The 12-key schema is an integration contract, so it isn't widened with `stops[]` or
`other_charges[]`. Anything it can't hold goes in the non-contract `_meta` block.

### Schema enforcement (three layers)

Structured outputs guarantee **shape, not truth**, so enforcement is layered and the
provider's guarantee is treated as a convenience, not the safety net:

1. **Provider** — strict `json_schema` / forced tool-use: parseable, no unknown keys.
2. **Pydantic** — re-validated client-side (`extra="forbid"`, types, enums produced by
   code). This is why the pipeline degrades gracefully onto a weaker provider.
3. **Grounding** — discard any located value whose quoted span isn't in the source,
   and any money figure never printed. Catches the confident invention the first two
   can't.

**Retry ladder** (`ratecon/extract.py`): strict call at temp 0 → on validation failure,
re-prompt with the literal Pydantic error text appended (≤2 repairs) → still failing,
return an empty extraction (fully-null load at `low`, routed to a human). No rung ever
returns a partially-guessed object above `low`.

---

## Confidence — when to auto-populate vs. flag for review

Confidence is a **deterministic rule engine over the warnings** (`confidence.py`), not
the model's opinion of itself — a self-reported score would be the "vibes" the brief
rules out, and unfalsifiable, so a broker could never be told *why* a load was held.

| Band | Meaning | Action |
|---|---|---|
| `high` | every check passed | auto-populate the load |
| `medium` | something was assumed, discarded, or restated inconsistently | populate as a draft; confirm before tendering |
| `low` | a critical field is null, or a money-guard check failed | do **not** auto-populate |

**Critical fields** (cost money or misroute freight): `total_rate`, `pickup_date`, and
city + state on both `origin` and `destination`. Any critical field null forces `low`;
so does any `low`-severity warning. Every band ships with its list of reasons.

The mechanism is built and deterministic; it is **not calibrated** — with 3 sample
documents from 1 template there's no way to establish that `high` empirically means a
sub-0.5% critical-field error rate. Thresholds are reasoned, not measured. That gap is
where Part 2 (eval) begins.

---

## Failure cases

- **Missing fields** — nulls propagate; nothing is back-solved. No ZIP / no weight →
  `null` with an info note. The model is told never to infer a ZIP from a city, and
  grounding enforces it.
- **Conflicting totals** — the gap between `line_haul + fuel` and the document total is
  *classified*, not just flagged: `balanced`, `unmapped_charge` (`medium` — extraction
  right, schema incomplete), `arithmetic_mismatch` (`low`), `missing_components`,
  `missing_total`. `line_haul_rate` is never back-solved from the total; the total is
  never recomputed from components.
- **Ambiguous dates** (`3/4/26`) — resolved by a cascade, strongest evidence first:
  a component > 12 fixes the order for the whole document → corroboration against an
  unambiguous named-month date → ordering constraint (delivery ≥ pickup) → US default,
  recorded as `DATE_LOCALE_ASSUMED` with confidence capped at `medium`. Two-digit years
  are clamped to a window around today (`26` → 2026, never 1926).

See [`EDGE_CASES.md`](EDGE_CASES.md) for the full failure-mode test checklist.

---

## Repo layout

```
ratecon/
  models.py      RichExtraction (lossless) + LoadSchema (the 12 keys)
  prompts.py     the locate-and-cite prompt
  llm.py         OpenAI / Anthropic / DeepSeek / offline replay adapters
  extract.py     retry ladder + grounding enforcement
  normalize.py   money, dates + locale cascade, addresses, equipment, weight
  grounding.py   span and money guards
  project.py     RichExtraction -> LoadSchema, all flattening rules
  confidence.py  the rule engine
  score.py       field-level scorer (the eval mechanism, at n=9)
  pipeline.py    orchestrator
  cli.py
tests/
  fixtures/provided/     3 supplied samples
  fixtures/adversarial/  6 documents authored for the failure cases
  fixtures/replay/       recorded model output, for offline tests
  fixtures/golden.json   field-level expectations (the CI gate)
```
