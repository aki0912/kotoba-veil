# Kotoba Veil

Kotoba Veil is a local-first Japanese PII review and masking application. It
combines GiNZA named-entity recognition, permissively licensed pattern
recognizers, Japanese-specific rules, and a user-managed PII dictionary.

## Features

- Selectable detection for names, organizations, locations, addresses,
  contact details, identifiers, financial information, dates, URLs, IP
  addresses, and custom dictionary terms.
- Human review with per-candidate acceptance and rejection before masking.
- Manual findings from text selections, with per-occurrence or all-match scope
  and optional persistence in the local PII dictionary.
- Text, DOCX, PPTX, and text-based PDF input and output.
- DOCX paragraph, table, header, and footer traversal; PPTX text frame, table,
  grouped shape, and notes traversal.
- Font-aware Japanese PDF decoding, including subset fonts and text split
  across adjacent text-show operations on the same line.
- Roster-field detection with original-offset mapping for letter-spaced Japanese
  headings, surname/given-name morphology, local organizations, numbered district
  lists, and dates that include both Gregorian and Japanese era years.
- PDF black masks that remove accepted glyphs from the content stream and
  retain their original widths, kerning, and surrounding table layout.
- Persistent local PII dictionary and local document sessions.
- No external APIs, telemetry, CDN assets, or runtime model downloads.

## Run with Docker

```bash
docker compose up --build
```

Open `http://localhost:8000`. The model is downloaded only while the image is
built. Runtime document processing remains local and can run without network
access.

To explicitly test offline runtime behavior after the image is built:

```bash
docker run --rm -d --name kotoba-veil-offline --network none --read-only \
  --tmpfs /tmp:rw,size=128m,mode=1777 \
  --tmpfs /data:rw,size=128m,uid=999,gid=999,mode=0700 \
  kotoba-veil:local
docker inspect --format '{{.State.Health.Status}}' kotoba-veil-offline
docker stop kotoba-veil-offline
```

This check intentionally has no browser-accessible port. Wait for the health
state to become `healthy`; that verifies startup and model loading without
egress.

## Local development

Python 3.11 is required.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install ".[dev]"
uvicorn app.main:app --reload
```

Set `KOTOBA_VEIL_DISABLE_NLP=1` to run only the deterministic rules and PII
dictionary during lightweight development. Set `KOTOBA_VEIL_DATA_DIR` to
change the local SQLite and document-session directory.

## Tests and license audit

```bash
python -m pytest --cov
python scripts/check_licenses.py
```

Detection quality can be measured with the versioned Japanese benchmark data:

```bash
python -m benchmarks.run \
  --dataset benchmarks/datasets/synthetic-v1.jsonl \
  --output build/benchmark-report.json
```

The dataset format, metrics, threshold options, and extension rules are documented
in `benchmarks/README.md`.

## Architecture documentation

Open `docs/system-architecture.html` in a browser for a standalone Japanese
system diagram covering document extraction, PII detection responsibilities,
review, masking, and local-data boundaries.

The license check rejects copyleft, share-alike, non-commercial, or unknown
license metadata. Two GiNZA/spaCy transitive dependencies have explicit,
package-scoped commercial-use exceptions documented in
`THIRD_PARTY_NOTICES.md`. All other non-permissive licenses remain blocked.
This is an engineering control, not legal advice.

## Current limitations

- Scanned PDFs and images are not processed yet. They require an offline OCR
  adapter such as Tesseract and coordinate-aware redaction.
- PDF support covers simple fonts with usable character mappings and horizontal
  Identity-H composite fonts. Text within a line is joined across Tj/TJ
  operands; separate positioned text lines remain separate review blocks.
  Unsupported Form XObjects, ActualText alternatives, font encodings, or
  unavailable glyph widths produce an explicit error instead of an incomplete
  mask. PDFs without extractable text produce an OCR guidance error.
- PDF output always uses black masks, regardless of `mask_character`. A glyph
  representing several Unicode characters is masked in full when any of those
  characters is selected. Re-upload PDFs analyzed before this adapter update.
  Images, annotations, and other non-page-text content are outside this text
  masking adapter; output still requires human review.
- Text embedded in unsupported Office objects, macros, attachments, charts,
  SmartArt, or arbitrary XML extensions may remain unchanged. Macro-enabled
  Office formats are intentionally rejected.
- The current dictionary is a PII detection dictionary. Compiled Sudachi
  morphology dictionary management is a separate operational feature because
  it requires dictionary rebuild, validation, and controlled model reload.
- Automated detection cannot guarantee that every PII value is found. Output
  must be reviewed and validated for the intended risk level.
