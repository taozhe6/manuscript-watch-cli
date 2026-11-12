"""Local CSV status transition ledger and offline query helpers."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from manuscript_watch_cli.models import (
    ManuscriptSnapshot,
    TransitionResult,
    sanitize_token,
)

LEDGER_COLUMNS = [
    "Timestamp",
    "Provider",
    "Journal",
    "JournalName",
    "Username",
    "Category",
    "ManuscriptNumber",
    "Title",
    "InitialDateSubmitted",
    "StatusDate",
    "Status",
]


def record_snapshot(
    data_dir: Path,
    snapshot: ManuscriptSnapshot,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> TransitionResult:
    checked_dt = now or datetime.now()
    checked_at = checked_dt.strftime("%Y-%m-%d %H:%M:%S")

    journal_dir = (
        data_dir
        / sanitize_token(snapshot.username, 60)
        / sanitize_token(snapshot.journal, 40)
    )
    prefix = f"{snapshot.stable_id}_"
    target_dir = journal_dir / f"{prefix}{sanitize_token(snapshot.title, 72)}"
    active_dir = target_dir

    if journal_dir.exists():
        for child in journal_dir.iterdir():
            if child.is_dir() and child.name.startswith(prefix):
                if not dry_run and child != target_dir and not target_dir.exists():
                    try:
                        child.rename(target_dir)
                        active_dir = target_dir
                    except OSError:
                        active_dir = child
                else:
                    active_dir = child
                break

    csv_path = active_dir / "history.csv"
    existing: list[dict[str, str]] = []
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            existing = list(csv.DictReader(handle))

    new_row = {
        "Timestamp": checked_at,
        "Provider": snapshot.provider,
        "Journal": snapshot.journal,
        "JournalName": snapshot.journal_name,
        "Username": snapshot.username,
        "Category": snapshot.category,
        "ManuscriptNumber": snapshot.manuscript_number,
        "Title": snapshot.title,
        "InitialDateSubmitted": snapshot.initial_date_submitted,
        "StatusDate": snapshot.status_date,
        "Status": snapshot.status,
    }

    if not existing:
        if not dry_run:
            active_dir.mkdir(parents=True, exist_ok=True)
            _write_csv(csv_path, [new_row])
        return TransitionResult(
            change="created",
            snapshot=snapshot,
            previous_status="",
            previous_status_date="",
            checked_at=checked_at,
            history_path=str(csv_path),
        )

    last = existing[-1]
    prev_status = last.get("Status", "")
    prev_date = last.get("StatusDate", "")
    if prev_status == snapshot.status and prev_date == snapshot.status_date:
        return TransitionResult(
            change="unchanged",
            snapshot=snapshot,
            previous_status=prev_status,
            previous_status_date=prev_date,
            checked_at=checked_at,
            history_path=str(csv_path),
        )

    if not dry_run:
        active_dir.mkdir(parents=True, exist_ok=True)
        existing.append(new_row)
        _write_csv(csv_path, existing)

    return TransitionResult(
        change="updated",
        snapshot=snapshot,
        previous_status=prev_status,
        previous_status_date=prev_date,
        checked_at=checked_at,
        history_path=str(csv_path),
    )


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in LEDGER_COLUMNS})


def iter_ledger_files(data_dir: Path) -> list[Path]:
    if not data_dir.exists():
        return []
    return sorted(data_dir.glob("*/*/*/history.csv"))


def list_latest_records(
    data_dir: Path,
    *,
    journal_filter: str = "",
    manuscript_filter: str = "",
) -> list[dict[str, Any]]:
    j_norm = journal_filter.strip().casefold()
    m_norm = manuscript_filter.strip().casefold()
    summaries: list[dict[str, Any]] = []

    for csv_file in iter_ledger_files(data_dir):
        with csv_file.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            continue
        last = rows[-1]
        journal = last.get("Journal", "") or csv_file.parent.parent.name
        ms_num = last.get("ManuscriptNumber", "")
        title = last.get("Title", "")

        if j_norm and j_norm not in journal.casefold() and j_norm not in last.get("JournalName", "").casefold():
            continue
        if m_norm and m_norm not in ms_num.casefold() and m_norm not in title.casefold():
            continue

        summaries.append({
            "journal": journal,
            "journal_name": last.get("JournalName", "") or journal,
            "username": last.get("Username", "") or csv_file.parent.parent.parent.name,
            "manuscript_number": ms_num,
            "title": title,
            "status": last.get("Status", ""),
            "status_date": last.get("StatusDate", ""),
            "initial_date_submitted": last.get("InitialDateSubmitted", ""),
            "last_recorded_at": last.get("Timestamp", ""),
            "transitions": len(rows),
            "history_path": str(csv_file),
        })

    summaries.sort(key=lambda item: (item["journal"], item["manuscript_number"], item["title"]))
    return summaries


def list_history_events(
    data_dir: Path,
    *,
    query: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    q_norm = query.strip().casefold()
    events: list[dict[str, Any]] = []

    for csv_file in iter_ledger_files(data_dir):
        with csv_file.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        journal_fallback = csv_file.parent.parent.name
        for row in rows:
            journal = row.get("Journal", "") or journal_fallback
            ms_num = row.get("ManuscriptNumber", "")
            title = row.get("Title", "")
            if q_norm and not any(
                q_norm in field.casefold()
                for field in (journal, row.get("JournalName", ""), ms_num, title, row.get("Status", ""))
            ):
                continue
            events.append({
                "timestamp": row.get("Timestamp", ""),
                "journal": journal,
                "manuscript_number": ms_num,
                "status": row.get("Status", ""),
                "status_date": row.get("StatusDate", ""),
                "title": title,
            })

    events.sort(key=lambda item: item["timestamp"], reverse=True)
    return events[:limit] if limit > 0 else events
