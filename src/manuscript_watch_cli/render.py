"""Output formatters (table, json, jsonl, tsv) respecting NO_COLOR and TTY detection."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Sequence, TextIO


def supports_color(stream: TextIO | None = None) -> bool:
    target = stream if stream is not None else sys.stdout
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    return hasattr(target, "isatty") and target.isatty()


def style(text: str, code: str, *, enabled: bool) -> str:
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


def truncate(text: str, width: int) -> str:
    clean = " ".join(str(text).split())
    if len(clean) <= width:
        return clean
    return clean[: max(1, width - 1)] + "…"


def emit_records(
    records: Sequence[dict[str, Any]],
    *,
    fmt: str,
    columns: Sequence[tuple[str, str, int]],
    stream: TextIO | None = None,
) -> None:
    target = stream if stream is not None else sys.stdout
    if fmt == "json":
        json.dump(list(records), target, ensure_ascii=False, indent=2)
        target.write("\n")
        return

    if fmt == "jsonl":
        for rec in records:
            target.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return

    if fmt == "tsv":
        headers = [key for key, _, _ in columns]
        target.write("\t".join(headers) + "\n")
        for rec in records:
            vals = [str(rec.get(key, "")).replace("\t", " ").replace("\n", " ") for key in headers]
            target.write("\t".join(vals) + "\n")
        return

    # Default: aligned plain/TTY table
    if not records:
        target.write("No matching records.\n")
        return

    color_on = supports_color(target)
    matrix: list[list[str]] = []
    widths = [len(title) for _, title, _ in columns]

    for rec in records:
        row_cells: list[str] = []
        for idx, (key, _, max_w) in enumerate(columns):
            val = truncate(str(rec.get(key, "")), max_w) if max_w > 0 else str(rec.get(key, ""))
            widths[idx] = max(widths[idx], len(val))
            row_cells.append(val)
        matrix.append(row_cells)

    header_line = "  ".join(
        title.ljust(widths[idx]) for idx, (_, title, _) in enumerate(columns)
    )
    target.write(style(header_line, "1", enabled=color_on) + "\n")

    for row_cells in matrix:
        formatted_cells: list[str] = []
        for idx, cell in enumerate(row_cells):
            padded = cell.ljust(widths[idx])
            key = columns[idx][0]
            if key == "change":
                if cell == "updated":
                    padded = style(padded, "33;1", enabled=color_on)
                elif cell == "created":
                    padded = style(padded, "32", enabled=color_on)
                elif cell == "unchanged":
                    padded = style(padded, "2", enabled=color_on)
            formatted_cells.append(padded)
        target.write("  ".join(formatted_cells) + "\n")
