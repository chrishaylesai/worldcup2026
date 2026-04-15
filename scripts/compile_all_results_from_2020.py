#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "all_results_from_2020.csv"
HEADER = ["date", "competition", "home_team", "score", "away_team", "venue"]
SOURCE_GLOB = "Group */*/RESULTS_2020-2026.md"
MARKDOWN_HEADER = ["Date", "Competition", "Home Team", "Score", "Away Team", "Venue"]


def parse_markdown_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        raise ValueError(f"Not a markdown table row: {line!r}")
    values = [part.strip() for part in stripped.strip("|").split("|")]
    if len(values) != 6:
        raise ValueError(f"Expected 6 columns, found {len(values)}: {line!r}")
    return values


def is_separator_row(values: list[str]) -> bool:
    return all(cell and set(cell) <= {"-", ":"} for cell in values)


def iter_results_rows(root: Path) -> list[list[str]]:
    rows: list[list[str]] = []
    for results_path in sorted(root.glob(SOURCE_GLOB)):
        with results_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line.startswith("|"):
                    continue
                values = parse_markdown_row(line)
                if values == MARKDOWN_HEADER or is_separator_row(values):
                    continue
                rows.append(values)
    return rows


def write_csv(rows: list[list[str]], output_path: Path) -> None:
    sorted_rows = sorted(rows, key=lambda row: (row[0], row[1], row[2], row[4], row[5], row[3]))
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(sorted_rows)


def main() -> None:
    rows = iter_results_rows(ROOT)
    write_csv(rows, OUTPUT_PATH)
    print(f"Wrote {len(rows)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
