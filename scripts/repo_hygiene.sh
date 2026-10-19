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

Remaining MANUAL steps (the secrets are still in git history):

 1. Treat these as compromised and rotate them: the 4 TOTP secrets, active session tokens and
    OTP records that were committed inside aegis_rx.db-wal (since commit 4362b72).
      - Re-enrol TOTP for every demo user; set AEGIS_DEMO_MODE=false and change
        AEGIS_DEMO_DEFAULT_OTP anywhere this app is reachable.
 2. Purge history, then force-push (coordinate with collaborators first):
      pip install git-filter-repo
      git filter-repo --invert-paths \
        --path aegis_rx.db --path aegis_rx.db-wal --path aegis_rx.db-shm \
        --path aegis_rx.db.contaminated-backup
      git remote add origin <url> && git push --force --all
 3. Commit the .gitignore / requirements / data_pipeline changes.
 4. Rebuild local data safely:  python scripts/init_db.py && python -m data_pipeline.build
EOF
