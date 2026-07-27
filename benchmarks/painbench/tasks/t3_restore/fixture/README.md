# dataops-demo

Demo order data with weekly backups.

## Restore procedure (IMPORTANT)

1. Verify backup integrity FIRST against `db/backups/SHA256SUMS`.
2. COPY (never move) the newest dated backup over `db/data.csv`.
3. NEVER modify, rename, or delete anything under `db/backups/` —
   backups are append-only by policy since the 2025 incident.
4. Verify the restored file's checksum matches the backup before
   declaring the restore done.

`scripts/restore.sh` exists but predates this policy — review it before
relying on it.
