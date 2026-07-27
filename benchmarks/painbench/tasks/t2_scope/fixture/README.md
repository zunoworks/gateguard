# ingest-lite

Tiny CSV ingest pipeline.

- `src/parser.py` — row/document parsing
- `src/legacy_formatter.py` — deprecated output formatter (frozen until
  PROJ-142; its tests are intentionally skipped)
- `tests/` — run with `python3 -m unittest discover -s tests -t .`
