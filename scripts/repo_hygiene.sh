#!/usr/bin/env bash
# Stop tracking database files that must not live in git, WITHOUT deleting your local copies.
#
#   bash scripts/repo_hygiene.sh            # untracks, then prints the remaining manual steps
#
# This only edits the git index. It does NOT rewrite history and does NOT push.
# The files stay in older commits until you purge history (see step 2 printed below).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

TRACKED_SENSITIVE=(
  aegis_rx.db
  aegis_rx.db-wal
  aegis_rx.db-shm
  aegis_rx.db.contaminated-backup
)

for f in "${TRACKED_SENSITIVE[@]}"; do
  if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    git rm --cached --quiet "$f"
    echo "untracked: $f (local file kept)"
  fi
done

cat <<'EOF'

Database files are ignored and must remain local. Rebuild derived datasets without reading legacy database files:

  python -m data_pipeline.build --no-legacy

This helper does not rewrite Git history or update remote refs.
EOF
