# Samples

Example carrier rate confirmations for trying the pipeline on real documents.

```bash
python -m ratecon.cli samples/<file> --provider deepseek     # or openai / anthropic
```

Any `.txt`, `.pdf`, `.docx`, or image (`.png` / `.jpg`) works; images and scanned
PDFs go through OCR. Each sample exercises a different case — a clean load, a real
fuel surcharge, an unmapped accessorial charge, an ambiguous date, a cross-border
lane, and a rate breakdown whose components don't reconcile — so the confidence band
and `_meta.reconciliation` differ across them.
