# Real-world testing: making documents and running the edge cases

The provided samples are one template with none of requirement 4's failure cases baked in.
To test for real you need documents that *do* contain them, as PDFs, DOCX, and images. This
doc gives you (1) three ways to generate such documents, (2) copy-paste prompts for an image
model, and (3) the expected pipeline output for every case so you can tell pass from fail.

Run anything you make with:

```bash
python -m ratecon.cli your-doc.pdf   --provider deepseek
python -m ratecon.cli your-scan.png  --provider deepseek     # OCR path
```

---

## 1. Three ways to make test documents

**A — Image model (most realistic OCR test).** Prompt GPT-image / Gemini / etc. to render a
rate confirmation as a document image (§2). Text fidelity varies between models, which is the
point: it exercises the OCR + grounding path the way a real fax/scan would. Save as PNG/JPG.

**B — HTML → PDF/PNG (deterministic text).** Paste the base HTML below into a file, edit the
values, open in a browser, and **Print → Save as PDF** (or screenshot for a PNG). The text is
exactly what you typed, so you're testing extraction logic, not OCR noise. Best for asserting
exact field values.

**C — Plain text.** Fastest: drop the case into a `.txt` and run it. No install needed. Use
this to iterate on a case before rendering it to PDF/image.

Minimal HTML template for route B (fill the `‹…›` slots):

```html
<h2>CARRIER RATE &amp; LOAD CONFIRMATION</h2>
<p>Reference ID: ‹LD-TEST-01›  |  Equipment: ‹Flatbed›  |  Agreed Amount: ‹$2,250.00›</p>
<p>Pickup Date: ‹08/12/2026›</p>
<table border="1" cellpadding="6">
 <tr><th>#</th><th>Type</th><th>Location</th><th>Date</th><th>Commodity</th><th>Weight</th></tr>
 <tr><td>1</td><td>Pickup</td><td>‹Des Plaines, IL 60018›</td><td>‹08/12/2026›</td><td>‹Beverages›</td><td>‹38,200 lbs›</td></tr>
 <tr><td>2</td><td>Drop</td><td>‹Westerville, OH 43081›</td><td>‹08/14/2026›</td><td>‹Beverages›</td><td>‹38,200 lbs›</td></tr>
</table>
<p>Base Rate: ‹$1,800.00›   Fuel Surcharge: ‹$450.00›   Total: ‹$2,250.00›</p>
```

---

## 2. Image-generation prompts (copy-paste)

Give the model the **base prompt**, then swap in one **variant** block per edge case. Ask for
one document per image.

**Base prompt**

> Generate a photorealistic image of a single-page US freight **carrier rate confirmation**,
> as if printed or faxed on white paper. Plain black text, clear document layout, no artistic
> styling. Include, in this order: a title "CARRIER RATE & LOAD CONFIRMATION"; a header block
> with Broker, Carrier, MC #, Phone, and **Reference ID**; a **Stops** table with columns
> #, Type (Pickup/Drop), Location (street, city, ST, ZIP), Date, Commodity, Weight; and a
> **Rate Breakdown** block listing each charge line and a Total. Use realistic freight values.
> Render all text legibly. Then apply the following specifics exactly:

**Variants** (append one to the base prompt)

| # | Edge case | Append this |
|---|---|---|
| 1 | Clean baseline | Reference ID LD-CLEAN-01; Van; Des Plaines, IL 60018 → Westerville, OH 43081; pickup 08/12/2026, drop 08/14/2026; Beverages; 38,200 lbs; Base $1,800, Fuel $450, Total $2,250. |
| 2 | Ambiguous date | Reference ID LD-AMB-01; dates written **3/4/26** (pickup) and **3/9/26** (drop) in M/D/YY with no other date on the page; Van; Dallas, TX 75212 → Memphis, TN 38116; Paper Goods; 18,400 lbs; Base $1,900, Total $1,900. |
| 3 | Real fuel surcharge, balances | Reference ID LD-FSC-01; Reefer; Salinas, CA 93901 → Jessup, MD 20794; Lettuce; 41,000 lbs; Base $2,200, **Fuel Surcharge $450**, Total $2,650. |
| 4 | Unmapped charge (gap explained) | Reference ID LD-UNM-01; Flatbed; Base Carrier Rate $500, a line called **"Carrier Charge" $200** (NOT fuel), Total $700. |
| 5 | Arithmetic mismatch | Reference ID LD-BAD-01; Base $1,500, Fuel $200, **Total $2,600** with no other line explaining the $900 gap. |
| 6 | Missing total | Reference ID LD-NOTOT-01; Base $1,500, Fuel $250, and **no Total line at all**. |
| 7 | Missing origin ZIP + weight | Reference ID LD-MISS-01; pickup city/state only (no ZIP), Weight column shows "–"; Total $1,400. |
| 8 | Multi-stop (3+) | Reference ID LD-MULTI-01; **three stops**: Pickup Miami, FL; Pickup Chicago, IL; Drop San Jose, CA; Total $3,000. |
| 9 | kg weight | Reference ID LD-KG-01; Weight given as **18,000 kg**; Total $2,100. |
| 10 | Step-deck equipment | Reference ID LD-STEP-01; Equipment **"Step Deck"**; Total $2,400. |
| 11 | Total appears as a line item | Reference ID LD-TOTROW-01; rate breakdown lists Base $500, Carrier Charge $200, and a **"Total $700" row inside the same breakdown table**. |
| 12 | International DD/MM date | Reference ID LD-DMY-01; dates **28/07/2026** and **05/08/2026** (day-first); Toronto, ON → Detroit, MI. |
| 13 | Load-ID decoys | Reference ID LD-DECOY-01, and ALSO print a PO/Container No, an MC number, and Truck/Trailer numbers, to check the right one is picked. |

---

## 3. Expected output — pass/fail matrix

Run each and check the contract fields + `_meta.reconciliation.status` + `confidence`. (With
an image, allow small OCR-driven differences in free-text like city spelling; the *band* and
*reconciliation status* are the real signals.)

| # | Case | Expect |
|---|---|---|
| 1 | Clean | all 12 keys populated; `balanced`; `high` (or `medium` if a ZIP/weight is soft) |
| 2 | `3/4/26` | `pickup_date` 2026-03-04; `date_locale.basis` = `assumed_us`; `DATE_LOCALE_ASSUMED`; `medium` |
| 3 | Real FSC | `fuel_surcharge` 450; `balanced`; `high` |
| 4 | Unmapped charge | `fuel_surcharge` **null**; `unmapped_charge`; `other_charges` has Carrier Charge 200; `medium` |
| 5 | Arithmetic mismatch | `arithmetic_mismatch`; `RATE_ARITHMETIC_MISMATCH`; `low` |
| 6 | Missing total | `total_rate` null; `missing_total`; `low` |
| 7 | Missing origin/weight | `zip`/`weight_lbs` null with info notes; band driven by whatever critical field is missing |
| 8 | Multi-stop | origin = first pickup, destination = last drop; `MULTI_STOP_FLATTENED`; 3 in `_meta.stops`; `medium` |
| 9 | kg weight | `weight_lbs` ≈ value × 2.20462; converted-from-kg info note |
| 10 | Step deck | `equipment_type` = `other`; `EQUIPMENT_BUCKETED`; `medium` |
| 11 | Total-as-row | `unmapped_charge` (NOT `arithmetic_mismatch`); Total row **not** in `other_charges`; `medium` |
| 12 | DD/MM | dates resolve day-first; trip not inverted |
| 13 | Decoys | `load_id` = the Reference ID, not the PO/MC/truck numbers |

The rule of thumb for every case: a correct read, or a `medium`/`low` with a **named reason**
in `_meta`. A wrong value at `high` is the only real failure.

---

## 4. Batch it like a mini eval set

Put labelled docs in a folder and compare against expectations — this is Part 2's harness at
small scale. `ratecon/score.py` already does field-level, post-normalization scoring; point it
at a `golden.json` of your generated cases and run per-field accuracy + auto-populate coverage
(see `tests/test_golden.py` for the pattern). The one metric to watch: **critical-field errors
on the `high`-confidence slice must stay zero** — that's the number that decides whether a load
can auto-book.

Notes on live runs:
- `deepseek-v4-pro` on `high` is slow (~2 min/doc in testing). For a batch, set
  `RATECON_DEEPSEEK_REASONING_EFFORT=low` or `RATECON_DEEPSEEK_MODEL=deepseek-v4-flash`, or use
  `--provider openai`, and reserve high effort for docs that fail a cheap check.
- OCR quality dominates the image path. If an image scores badly, check the extracted text
  first (`python -c "from ratecon.ingest import to_text; print(to_text('scan.png'))"`) before
  blaming the pipeline.
```
