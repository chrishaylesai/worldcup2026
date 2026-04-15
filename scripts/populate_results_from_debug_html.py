#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag


ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 4, 13)
MIN_DATE = date(2020, 1, 1)
RESULTS_HEADER = "| Date | Competition | Home Team | Score | Away Team | Venue |"
RESULTS_SEPARATOR = "|------|-------------|-----------|-------|-----------|-------|"
CSV_HEADER = ["date", "competition", "home_team", "score", "away_team", "venue"]
YEAR_RE = re.compile(r"^(20\d{2})$")
SCORE_RE = re.compile(r"^\d+\s*[–-]\s*\d+$")
QUALIFIER_REFERENCE_EXEMPT_TEAMS = {"canada", "mexico", "united states"}
NON_PLAYED_MARKERS = {
    "",
    "cancelled",
    "canceled",
    "postponed",
    "abd",
    "abandoned",
    "void",
    "awarded",
    "tbd",
    "match postponed",
}
NATIONAL_TEAM_HINTS = (
    " national football team",
    " men's national soccer team",
    " men’s national soccer team",
    " national soccer team",
    " national team",
)


@dataclass(frozen=True)
class MatchRow:
    match_date: date
    competition: str
    home_team: str
    score: str
    away_team: str
    venue: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root containing Group */*/RESULTS_2020-2026.md and debug_html/",
    )
    return parser.parse_args()


def normalize_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = value.replace("–", "–")
    value = re.sub(r"\[[^\]]*\]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|")


def slugify_component(value: str) -> str:
    value = value.lower().strip()
    value = value.replace("&", "and")
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value


def plain_text(node: Tag | NavigableString | None) -> str:
    if node is None:
        return ""
    if isinstance(node, NavigableString):
        return normalize_text(str(node))
    clone = BeautifulSoup(str(node), "html.parser")
    for tag in clone.select("sup.reference, span.flagicon, style, script"):
        tag.decompose()
    return normalize_text(clone.get_text(" ", strip=True))


def derive_debug_html_path(root: Path, results_path: Path) -> Path:
    group = results_path.parent.parent.name
    team = results_path.parent.name
    filename = f"{slugify_component(group)}_{slugify_component(team)}.html"
    return root / "debug_html" / filename


def nearest_year(node: Tag) -> int | None:
    for previous in node.previous_elements:
        if not isinstance(previous, Tag):
            continue
        if previous.name in {"h2", "h3", "h4"}:
            ident = previous.get("id")
            if ident and YEAR_RE.match(ident):
                return int(ident)
        if previous.name == "div":
            heading = previous.find(["h2", "h3", "h4"], id=YEAR_RE)
            if heading and heading.get("id"):
                return int(heading["id"])
    return None


def parse_match_date(raw_date: str, fallback_year: int | None) -> date | None:
    text = normalize_text(raw_date)
    if not text:
        return None
    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    if fallback_year is None:
        return None
    for fmt in ("%d %B", "%d %b", "%B %d", "%b %d"):
        try:
            partial = datetime.strptime(f"{text} 2000", f"{fmt} %Y")
            return date(fallback_year, partial.month, partial.day)
        except ValueError:
            pass
    return None


def clean_competition(value: str) -> str:
    return normalize_text(value)


def clean_venue(value: str) -> str:
    cleaned = normalize_text(value)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    cleaned = re.sub(r"\s+\(([AHN])\)$", "", cleaned)
    return cleaned


def clean_score(value: str) -> str:
    cleaned = normalize_text(value)
    match = re.search(r"(\d+)\s*[–-]\s*(\d+)", cleaned)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return cleaned


def reverse_score(score: str) -> str:
    match = re.fullmatch(r"(\d+)\s*[–-]\s*(\d+)", score)
    if match is None:
        return score
    return f"{match.group(2)}-{match.group(1)}"


def extract_team_name(cell: Tag) -> str:
    org = cell.select_one(".fn.org")
    if org is not None:
        text = plain_text(org)
    else:
        text = plain_text(cell)
    text = re.sub(r"\b[a-z]{2,4}\s*\+\d{1,2}\b$", "", text, flags=re.IGNORECASE)
    return normalize_text(text)


def is_national_team(cell: Tag, team_name: str, target_team: str) -> bool:
    normalized_team = normalize_text(team_name)
    if normalized_team == target_team:
        return True
    anchor_titles = [
        normalize_text(anchor.get("title", "")).lower()
        for anchor in cell.find_all("a")
        if anchor.get("title")
    ]
    if any(any(hint in title for hint in NATIONAL_TEAM_HINTS) for title in anchor_titles):
        return True
    hrefs = [anchor.get("href", "") for anchor in cell.find_all("a") if anchor.get("href")]
    if any("_national_football_team" in href or "_national_soccer_team" in href for href in hrefs):
        return True
    return False


def normalize_team_for_cell_check(value: str) -> str:
    return normalize_text(value).replace("Côte d’Ivoire", "Ivory Coast")


def normalize_reference_team(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", normalize_text(value))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.replace("&", "and")
    ascii_text = re.sub(r"[^a-zA-Z0-9]+", " ", ascii_text)
    return re.sub(r"\s+", " ", ascii_text).strip().lower()


def is_2026_world_cup_qualifier(competition: str) -> bool:
    normalized = clean_competition(competition).lower()
    return "2026" in normalized and "world cup" in normalized and (
        "qualification" in normalized or "qualifier" in normalized
    )


def parse_football_box_rows(soup: BeautifulSoup, target_team: str) -> list[MatchRow]:
    matches: list[MatchRow] = []
    for table in soup.select("table.vevent"):
        first_row = table.find("tr")
        if first_row is None:
            continue
        cells = first_row.find_all("td", recursive=False)
        if len(cells) < 5:
            cells = first_row.find_all("td")
        if len(cells) < 5:
            continue

        date_comp_cell, home_cell, score_cell, away_cell, venue_cell = cells[:5]
        date_text_node = date_comp_cell.find("span")
        raw_date = plain_text(date_text_node or date_comp_cell)
        competition_tag = date_comp_cell.find("small")
        competition = clean_competition(plain_text(competition_tag or date_comp_cell))
        if competition_tag is None:
            competition = clean_competition(re.sub(re.escape(raw_date), "", plain_text(date_comp_cell)).strip())
        score = clean_score(plain_text(score_cell))
        if score.lower() in NON_PLAYED_MARKERS or not SCORE_RE.match(score):
            continue

        match_date = parse_match_date(raw_date, nearest_year(table))
        if match_date is None or match_date < MIN_DATE or match_date > TODAY:
            continue

        home_team = extract_team_name(home_cell)
        away_team = extract_team_name(away_cell)
        if not home_team or not away_team:
            continue
        if not is_national_team(home_cell, home_team, target_team):
            continue
        if not is_national_team(away_cell, away_team, target_team):
            continue

        matches.append(
            MatchRow(
                match_date=match_date,
                competition=competition,
                home_team=home_team,
                score=score,
                away_team=away_team,
                venue=clean_venue(plain_text(venue_cell)),
            )
        )
    return matches


def find_results_table(soup: BeautifulSoup) -> Tag | None:
    for table in soup.select("table.wikitable"):
        header_row = table.find("tr")
        if header_row is None:
            continue
        headers = [plain_text(cell).lower() for cell in header_row.find_all(["th", "td"], recursive=False)]
        if "date" in headers and "score" in headers and "competition" in headers:
            if "opponents" in headers or "opponent" in headers:
                return table
    return None


def find_target_team_name(soup: BeautifulSoup) -> str:
    title = plain_text(soup.find("h1"))
    title = re.sub(r"\s+results.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\(.*\)$", "", title).strip()
    title = title.replace("men’s", "men's")
    if title.endswith(" men's national football team"):
        title = title[: -len(" men's national football team")]
    elif title.endswith(" men's national soccer team"):
        title = title[: -len(" men's national soccer team")]
    elif title.endswith(" national football team"):
        title = title[: -len(" national football team")]
    elif title.endswith(" national soccer team"):
        title = title[: -len(" national soccer team")]
    return normalize_team_for_cell_check(title)


def parse_results_table_rows(soup: BeautifulSoup, target_team: str) -> list[MatchRow]:
    table = find_results_table(soup)
    if table is None:
        return []

    note_text = plain_text(table.find_previous("p"))
    team_score_first = "score is shown first" in note_text.lower()
    matches: list[MatchRow] = []
    rows = table.find_all("tr")
    header_cells = rows[0].find_all(["th", "td"], recursive=False)
    headers = [plain_text(cell).lower() for cell in header_cells]
    header_map = {header: idx for idx, header in enumerate(headers)}
    date_idx = header_map.get("date")
    venue_idx = header_map.get("venue")
    opponent_idx = header_map.get("opponents", header_map.get("opponent"))
    score_idx = header_map.get("score")
    competition_idx = header_map.get("competition")
    if None in {date_idx, venue_idx, opponent_idx, score_idx, competition_idx}:
        return []

    for row in rows[1:]:
        cells = row.find_all(["td", "th"], recursive=False)
        max_idx = max(date_idx, venue_idx, opponent_idx, score_idx, competition_idx)
        if len(cells) <= max_idx:
            continue

        date_text = plain_text(cells[date_idx])
        match_date = parse_match_date(date_text, None)
        if match_date is None or match_date < MIN_DATE or match_date > TODAY:
            continue

        venue_text = clean_venue(plain_text(cells[venue_idx]))
        opponent_cell = cells[opponent_idx]
        opponent = plain_text(opponent_cell)
        score = clean_score(plain_text(cells[score_idx]))
        competition = clean_competition(plain_text(cells[competition_idx]))
        if score.lower() in NON_PLAYED_MARKERS or not SCORE_RE.match(score):
            continue

        if not is_national_team(opponent_cell, opponent, target_team):
            continue

        marker_match = re.search(r"\(([AHN])\)\s*$", plain_text(cells[venue_idx]))
        marker = marker_match.group(1) if marker_match else None

        if marker == "H":
            home_team, away_team = target_team, opponent
        elif marker == "A":
            home_team, away_team = opponent, target_team
        elif marker == "N":
            if team_score_first:
                home_team, away_team = target_team, opponent
            else:
                home_team, away_team = opponent, target_team
        else:
            if team_score_first:
                home_team, away_team = target_team, opponent
            else:
                home_team, away_team = opponent, target_team
        if team_score_first and away_team == target_team:
            score = reverse_score(score)

        matches.append(
            MatchRow(
                match_date=match_date,
                competition=competition,
                home_team=home_team,
                score=score,
                away_team=away_team,
                venue=venue_text,
            )
        )
    return matches


def parse_schema_footballbox_rows(soup: BeautifulSoup, target_team: str) -> list[MatchRow]:
    matches: list[MatchRow] = []
    for box in soup.select("div.footballbox"):
        time_node = box.select_one("time[itemprop='startDate']")
        home_cell = box.select_one("th.fhome")
        away_cell = box.select_one("th.faway")
        score_cell = box.select_one("th.fscore")
        venue_node = box.select_one(".fright [itemprop='name address']")
        if not all([time_node, home_cell, away_cell, score_cell, venue_node]):
            continue

        datetime_value = time_node.get("datetime", "")
        if not datetime_value:
            continue
        try:
            match_date = datetime.fromisoformat(datetime_value.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if match_date < MIN_DATE or match_date > TODAY:
            continue

        competition = ""
        for previous in box.previous_elements:
            if not isinstance(previous, Tag):
                continue
            if previous.name in {"h2", "h3"}:
                break
            if previous.name == "div" and "footballbox" in (previous.get("class") or []):
                break
            if previous.name == "p":
                text = clean_competition(plain_text(previous))
                if text:
                    competition = text
                    break
        score = clean_score(plain_text(score_cell))
        if score.lower() in NON_PLAYED_MARKERS or not SCORE_RE.match(score):
            continue

        home_team = extract_team_name(home_cell)
        away_team = extract_team_name(away_cell)
        if not is_national_team(home_cell, home_team, target_team):
            continue
        if not is_national_team(away_cell, away_team, target_team):
            continue

        matches.append(
            MatchRow(
                match_date=match_date,
                competition=competition,
                home_team=home_team,
                score=score,
                away_team=away_team,
                venue=clean_venue(plain_text(venue_node)),
            )
        )
    return matches


def dedupe_rows(rows: Iterable[MatchRow]) -> list[MatchRow]:
    deduped: dict[tuple[date, str, str, str, str, str], MatchRow] = {}
    for row in rows:
        key = (
            row.match_date,
            row.competition,
            row.home_team,
            row.score,
            row.away_team,
            row.venue,
        )
        deduped[key] = row
    return sorted(deduped.values(), key=lambda row: (row.match_date, row.home_team, row.away_team, row.score))


def load_qualifier_reference(root: Path) -> dict[tuple[str, frozenset[str]], list[tuple[str, str, str]]]:
    reference_path = root / "wc_qualifiers.csv"
    if not reference_path.exists():
        return {}

    reference: dict[tuple[str, frozenset[str]], list[tuple[str, str, str]]] = {}
    with reference_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            home_team = normalize_text(row["home_team"])
            away_team = normalize_text(row["away_team"])
            key = (
                row["date"],
                frozenset({normalize_reference_team(home_team), normalize_reference_team(away_team)}),
            )
            reference.setdefault(key, []).append((home_team, clean_score(row["score"]), away_team))
    return reference


def apply_qualifier_reference(
    rows: Iterable[MatchRow],
    target_team: str,
    qualifier_reference: dict[tuple[str, frozenset[str]], list[tuple[str, str, str]]],
) -> list[MatchRow]:
    if normalize_reference_team(target_team) in QUALIFIER_REFERENCE_EXEMPT_TEAMS:
        return list(rows)

    corrected: list[MatchRow] = []
    for row in rows:
        if not is_2026_world_cup_qualifier(row.competition):
            corrected.append(row)
            continue

        key = (
            row.match_date.isoformat(),
            frozenset({normalize_reference_team(row.home_team), normalize_reference_team(row.away_team)}),
        )
        candidates = qualifier_reference.get(key, [])
        if len(candidates) != 1:
            corrected.append(row)
            continue

        home_team, score, away_team = candidates[0]
        corrected.append(
            MatchRow(
                match_date=row.match_date,
                competition=row.competition,
                home_team=home_team,
                score=score,
                away_team=away_team,
                venue=row.venue,
            )
        )
    return corrected


def render_results(rows: list[MatchRow]) -> str:
    lines = [RESULTS_HEADER, RESULTS_SEPARATOR]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row.match_date.isoformat(),
                    markdown_escape(row.competition),
                    markdown_escape(row.home_team),
                    markdown_escape(row.score),
                    markdown_escape(row.away_team),
                    markdown_escape(row.venue),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def render_csv(rows: list[MatchRow]) -> str:
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(CSV_HEADER)
    for row in rows:
        writer.writerow(
            [
                row.match_date.isoformat(),
                row.competition,
                row.home_team,
                row.score,
                row.away_team,
                row.venue,
            ]
        )
    return output.getvalue()


def process_results_file(
    results_path: Path,
    root: Path,
    qualifier_reference: dict[tuple[str, frozenset[str]], list[tuple[str, str, str]]],
) -> tuple[Path, int]:
    html_path = derive_debug_html_path(root, results_path)
    if not html_path.exists():
        raise FileNotFoundError(f"Missing debug HTML for {results_path}: {html_path}")

    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    target_team = find_target_team_name(soup)
    rows = parse_football_box_rows(soup, target_team)
    rows.extend(parse_results_table_rows(soup, target_team))
    rows.extend(parse_schema_footballbox_rows(soup, target_team))
    rows = apply_qualifier_reference(rows, target_team, qualifier_reference)
    rows = dedupe_rows(rows)

    results_path.write_text(render_results(rows), encoding="utf-8")
    results_path.with_name("results_2020-2026.csv").write_text(render_csv(rows), encoding="utf-8")
    return results_path, len(rows)


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    results_files = sorted(root.glob("Group */*/RESULTS_2020-2026.md"))
    if not results_files:
        raise SystemExit("No results files found.")

    qualifier_reference = load_qualifier_reference(root)
    processed: list[tuple[Path, int]] = []
    for results_path in results_files:
        processed.append(process_results_file(results_path, root, qualifier_reference))

    total_rows = sum(count for _, count in processed)
    print(f"Updated {len(processed)} files with {total_rows} result rows.")
    for path, count in processed:
        print(f"{path.relative_to(root)}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
