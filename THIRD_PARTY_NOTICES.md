# Third-party notices

Kotoba Veil's direct dependencies are selected for commercial use. The
principal runtime components are:

| Component | License | Purpose |
| --- | --- | --- |
| FastAPI | MIT | Web API |
| Uvicorn | BSD-3-Clause | Application server |
| Presidio Analyzer | MIT | PII recognizer interfaces and pattern detection |
| GiNZA and ja_ginza | MIT | Japanese named-entity recognition |
| SudachiPy / SudachiDict | Apache-2.0 with notices in SudachiDict LEGAL | Japanese tokenization |
| spaCy | MIT | NLP pipeline framework |
| python-docx | MIT | DOCX processing |
| python-pptx | MIT | PPTX processing |
| pypdf | BSD-3-Clause | PDF processing |

Redistributions must retain the applicable license and attribution notices.
Run `python scripts/check_licenses.py` in a fully installed environment to
generate the complete machine-readable inventory in
`third-party-licenses.json`. This inventory must be reviewed before a public
release because dependency metadata and transitive dependencies can change.

## Approved commercial-use exceptions

The current GiNZA/spaCy dependency graph includes the following packages which
are outside the default "MIT, Apache, BSD family only" policy. The project
owner has explicitly approved them as package-scoped commercial-use
exceptions:

| Component | Declared license | Introduced through |
| --- | --- | --- |
| certifi | MPL-2.0 | requests / spaCy |
| tqdm | MPL-2.0 AND MIT | spaCy |

These licenses permit commercial use, but MPL-2.0 is not classified as a
permissive license by the default policy. `scripts/check_licenses.py` permits
only the exact package-and-license pairs above and records each exception in
`third-party-licenses.json`. Any additional MPL, copyleft, share-alike,
non-commercial, or unknown dependency still fails the release gate.
