"""Validate and copy downloaded DDInter category files into data_validation/ (never overwrites anything).

    python scripts/add_ddinter_files.py C:\\downloads\\ddinter_downloads_code_B.csv ... [--data-dir data_validation]

Each file must be named ddinter_downloads_code_<LETTER>.csv, use the expected header and parse cleanly. Existing
files are never replaced. It prints a per-file summary and, for the whole set, how many pairs are new, repeated
with the same level, or in conflict with a file already present. Then run:

    python -m data_pipeline.build && python -m ml.train_final && python -m pytest -q
"""
from __future__ import annotations

import argparse
import shutil
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from data_pipeline.common import DDINTER_EXPECTED_CODES, ddinter_code, discover_ddinter_files, sha256_file  # noqa: E402
from data_pipeline.ddinter import clean_ddinter, read_ddinter_csv  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--data-dir", type=Path, default=REPO / "data_validation")
    ap.add_argument("--dry-run", action="store_true", help="validate only, copy nothing")
    a = ap.parse_args(argv)

    existing = {ddinter_code(p): p for p in discover_ddinter_files(a.data_dir)}
    problems, accepted = [], []
    for f in a.files:
        code = ddinter_code(f)
        if code is None or f.name != f"ddinter_downloads_code_{code}.csv" or code not in DDINTER_EXPECTED_CODES:
            problems.append(f"{f.name}: name must be ddinter_downloads_code_<{'/'.join(DDINTER_EXPECTED_CODES)}>.csv")
            continue
        if code in existing:
            problems.append(f"{f.name}: category {code} already present ({existing[code].name}); refusing to overwrite")
            continue
        try:
            rows = read_ddinter_csv(f)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if not rows:
            problems.append(f"{f.name}: no data rows")
            continue
        accepted.append((code, f, rows))
        print(f"{f.name}: {len(rows):,} rows, sha256 {sha256_file(f)[:16]}")
    if problems:
        print("\nREJECTED (nothing copied):")
        for p in problems:
            print("  -", p)
        return 1

    # what would the merged set look like?
    all_rows = []
    for p in discover_ddinter_files(a.data_dir):
        all_rows += read_ddinter_csv(p)
    before = clean_ddinter(all_rows) if all_rows else None
    for _, _, rows in accepted:
        all_rows += rows
    after = clean_ddinter(all_rows)
    status = lambda res: dict(Counter(p["label_status"] for p in res.pairs))  # noqa: E731
    print(f"\nPairs before: {len(before.pairs):,} {status(before)}" if before else "\nNo files present before.")
    print(f"Pairs after : {len(after.pairs):,} {status(after)}  | drugs: {len(after.drugs):,} | quarantined rows: {sum(1 for r in after.raw_rows if r.quarantine_reason)}")
    if not a.dry_run:
        a.data_dir.mkdir(parents=True, exist_ok=True)
        for _, f, _ in accepted:
            shutil.copy2(f, a.data_dir / f.name)
        print(f"\nCopied {len(accepted)} file(s) to {a.data_dir}. Now run the pipeline and retrain (see --help).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
