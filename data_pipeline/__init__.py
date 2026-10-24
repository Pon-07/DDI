"""
AEGIS Rx reproducible dataset pipeline.

Builds a SEPARATE staging SQLite database and clean CSV datasets from the raw source files
shipped in this repository. It never writes to the production database (aegis_rx.db).

Principles
----------
* Raw records are preserved verbatim (``*_raw`` tables); cleaning only adds derived tables.
* Nothing is fabricated: unknown severities stay unknown, missing drug names stay missing,
  absent features (RxNorm, ATC, CYP450 ...) are NULL and reported as missing.
* Every run is reproducible: inputs are fingerprinted (SHA-256) and row counts are asserted.

Run:  python -m data_pipeline.build            (from the repository root)
"""

PIPELINE_VERSION = "1.0.0"
