# Credential rotation review (dry-run only)

This ZIP contains the standalone rotation tool, synthetic tests, and this guide. The safe commands below only inspect a local SQLite database; do not add `--apply`.

## Before running

1. Close the AEGIS Rx server.
2. Run the commands from a fresh temporary extraction directory. They do not switch branches, pull, or modify repository files.
3. Point `--db` at the intended local database by its full path.

The script refuses databases missing the expected auth tables or required columns. It prints aggregate counts only; it does not print user IDs, names, phone numbers, TOTP values, or tokens. A dry run opens the database read-only and creates no backup. Do not paste database output into chat.

## PowerShell

```powershell
$reviewDir = Join-Path $env:TEMP 'aegis-credential-rotation-review'
New-Item -ItemType Directory -Path $reviewDir
Expand-Archive -LiteralPath "$HOME\Downloads\aegis_credential_rotation.zip" -DestinationPath $reviewDir
py -3 (Join-Path $reviewDir 'scripts\rotate_auth_credentials.py') --db 'C:\path\to\DDI\aegis_rx.db'
```

## Windows CMD

Use a fresh temporary folder name if the folder already exists.

```cmd
mkdir "%TEMP%\aegis-credential-rotation-review"
powershell -NoProfile -Command "Expand-Archive -LiteralPath '%USERPROFILE%\Downloads\aegis_credential_rotation.zip' -DestinationPath '%TEMP%\aegis-credential-rotation-review'"
py -3 "%TEMP%\aegis-credential-rotation-review\scripts\rotate_auth_credentials.py" --db "C:\path\to\DDI\aegis_rx.db"
```

If `py -3` is unavailable, use `python` with the same script and database arguments.

## Reading the report

The report includes database-wide counts for users, matching compromised TOTP values, sessions, and one-time-code records. It omits user identifiers. A nonzero compromised count means the script found values matching its built-in hash list. No changes or backup are made by this command.

## Verification design

Before reporting or proceeding, the script checks that `users`, `user_sessions`, and `otp_records` exist and have the columns it relies on. For a backup, it checks SQLite integrity, schema objects, and every table's rows against the source database. The tests use only generated synthetic databases, including a forced failure after an earlier delete to confirm transaction rollback.

Any operation that changes a database requires a separate review and explicit approval. Do not use `--apply`, remove backups, or alter Git history as part of this dry-run review.
