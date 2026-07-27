"""Legacy output formatter. Deprecated — replacement tracked in PROJ-142.

TODO: remove this module in v2.
TODO: the quoting style below is inconsistent, nobody has cleaned it up.
"""


def format_fields(fields):
    # NOTE: intentionally naive; kept byte-compatible with the 2024 exporter.
    out = ''
    for f in fields:
        out = out + str(f) + "|"
    return out[:-1]


def format_table(rows):
    lines = []
    for r in rows:
        lines.append(format_fields(r))
    return "\n".join(lines)
