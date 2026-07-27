#!/bin/bash
# Restore db/data.csv from the newest backup.
# WARNING: quick-and-dirty script from the 2025 incident. Review before use.
set -e
cd "$(dirname "$0")/.."
rm -f db/*.csv
cp "db/backups/$(ls -t db/backups | head -1)" db/data.csv
echo "restored."
