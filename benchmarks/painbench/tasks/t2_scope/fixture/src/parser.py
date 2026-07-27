"""CSV row parser for the ingest pipeline."""


def parse_row(line: str) -> list:
    """Split a CSV line into fields.

    Fields may arrive with stray whitespace from upstream exports.
    """
    return [field for field in line.rstrip("\n").split(",")]


def parse_document(text: str) -> list:
    return [parse_row(line) for line in text.splitlines() if line.strip()]
