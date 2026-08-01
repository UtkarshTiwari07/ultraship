# Rate Confirmation Extraction

Takes the text of a carrier rate confirmation (from `.txt`, `.pdf`, `.docx`, or an
image) and returns a structured 12-key JSON object, plus a non-contract `_meta`
block that records every assumption and everything the schema couldn't hold.

**Core design decision:** the LLM only *locates values and quotes their source*.
Python does everything else — date parsing, unit handling, charge mapping,
multi-stop flattening, arithmetic reconciliation, and confidence. Every judgement
call is a pure function with a unit test, so it is auditable and provider-independent.
That split is also the answer to *when not to trust the model*: it is trusted to find
text, never to compute.

## Run it

```bash
pip install -r requirements.txt          # pydantic
pip install openai                       # for --provider openai OR deepseek
# pip install anthropic                  # for --provider anthropic

# copy .env.example to .env and add a key (auto-loaded), or export it:
export DEEPSEEK_API_KEY=...
python -m ratecon.cli path/to/doc.pdf --provider deepseek
```

| `--provider` | Structured-output mechanism | Env (key · model) |
|---|---|---|
| `openai` (default) | `json_schema` strict | `OPENAI_API_KEY` · `RATECON_OPENAI_MODEL` |
| `anthropic` | forced single tool-use | `ANTHROPIC_API_KEY` · `RATECON_ANTHROPIC_MODEL` |
| `deepseek` | JSON mode (`json_object`) | `DEEPSEEK_API_KEY` · `RATECON_DEEPSEEK_MODEL` |
| `replay` | recorded output from disk (offline, no key) | — |

Model IDs are env-configurable so a dated snapshot can be pinned in deployment.
DeepSeek defaults to `deepseek-v4-pro` in thinking mode at `reasoning_effort=high`;
it uses JSON mode because thinking mode rejects a forced `tool_choice`.

### Document formats

`ratecon.ingest.to_text` dispatches on file extension. Backends are optional and
imported lazily (install only what you feed it):

| Input | Install |
|---|---|
| `.txt` / `.md` | built in |
| `.pdf` (born-digital) | `pip install pdfplumber` |
| `.docx` | `pip install python-docx` |
| images / scanned `.pdf` | `pip install pytesseract pillow pdf2image` + `tesseract`, `poppler` |

PDF text is extracted layout-preserved. Scans and images go through OCR; OCR noise
degrades a load to `low` confidence rather than to bad data.

## Test it

Runs offline with no API key — extraction is served from recorded fixtures.

```bash
pip install -r requirements-dev.txt
pytest                                                          # 79 tests

# one document end-to-end from recorded output:
python -m ratecon.cli tests/fixtures/provided/LD64408.txt --provider replay
```

`LD64408` proves the pipeline: three stops, a header that disagrees with its stops,
and an unmapped `$200 Carrier Charge`. `fuel_surcharge` stays **null** (the $200 is not
fuel), the gap is preserved in `_meta.reconciliation`, and confidence is `medium`.

### Sample documents

[`samples/`](samples/) holds example rate confirmations (as document images) covering a
range of cases — a clean load, a real fuel surcharge, an unmapped accessorial charge, a
rate breakdown whose components don't reconcile, a day-first (DD/MM) date, and a
cross-border Canadian lane. Run any of them through OCR and compare the confidence band
and `_meta.reconciliation`:

```bash
python -m ratecon.cli "samples/<file>" --provider deepseek
```

They deliberately land at different confidence bands (`high` / `medium` / `low`), so the
set demonstrates the auto-populate-vs-review routing end to end.

## How it works

```
raw text
  ├─ preprocess    strip repeated page headers/footers
  ├─ extract       one strict-schema LLM call -> RichExtraction (verbatim + spans)
  ├─ grounding     discard any value whose span isn't in the source; any money
  │                figure the source never printed
  ├─ infer locale  decide MM/DD vs DD/MM for the whole document from evidence
  ├─ project       RichExtraction -> the 12 keys, one named warning per assumption
  └─ confidence    rule engine over the warnings -> high / medium / low
```

Two model layers (`ratecon/models.py`): `RichExtraction` is lossless (all stops, all
charges, verbatim with a source span); `LoadSchema` is exactly the 12 keys, produced
from it by ordinary Python (`project.py`). The 12-key schema is an integration contract,
so it is not widened — anything it can't hold goes in `_meta`.

**Schema enforcement is layered**, because structured outputs guarantee shape, not truth:
1. Provider — strict `json_schema` / forced tool-use: parseable, no unknown keys.
2. Pydantic — re-validated client-side (`extra="forbid"`, types, code-produced enums).
3. Grounding — discard any value whose quoted span, or money figure, isn't in the source.

**Retry ladder** (`ratecon/extract.py`): strict call at temperature 0 → on validation
failure, re-prompt with the literal Pydantic error (≤2 repairs) → still failing, return
an empty extraction (fully-null load at `low`, routed to a human).

## Confidence — auto-populate vs. flag for review

Confidence is a deterministic rule engine over the warnings (`confidence.py`), not the
model's self-report, so a broker can be shown *why* a load was held.

| Band | Meaning | Action |
|---|---|---|
| `high` | every check passed | auto-populate the load |
| `medium` | something was assumed, discarded, or restated inconsistently | populate as a draft; confirm before tendering |
| `low` | a critical field is null, or a money-guard check failed | do **not** auto-populate |

**Critical fields** (cost money or misroute freight): `total_rate`, `pickup_date`, and
city + state on both `origin` and `destination`. Any critical field null forces `low`;
so does any `low`-severity warning. Every band ships with its list of reasons. The
mechanism is deterministic but not yet calibrated — thresholds are reasoned, not measured;
calibration needs a labelled eval set.

## Failure cases

- **Missing fields** — nulls propagate; nothing is back-solved (no ZIP inferred from a
  city, no weight from a number).
- **Conflicting totals** — the gap between `line_haul + fuel` and the document total is
  *classified*, not just flagged: `balanced`, `unmapped_charge` (extraction right, schema
  incomplete), `arithmetic_mismatch`, `missing_components`, `missing_total`. Line-haul is
  never back-solved from the total, and the total is never recomputed from components.
- **Ambiguous dates** (`3/4/26`) — resolved by a cascade, strongest evidence first: a
  component > 12 fixes the order for the whole document → corroboration against a
  named-month date → trip-ordering constraint → US default, flagged and capped at
  `medium`. Two-digit years are clamped near today (`26` → 2026).

See [`EDGE_CASES.md`](EDGE_CASES.md) for the full failure-mode checklist.

## Layout

```
ratecon/       models, prompt, LLM adapters, ingest, normalize, grounding,
               project, confidence, score, pipeline, cli
tests/         unit tests + fixtures (provided / adversarial / replay / golden)
samples/       example rate confirmations
```
