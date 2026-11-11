"""Configuration loader and CSV status history storage for em-tracker."""

from __future__ import annotations

import csv
from datetime import datetime
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import tempfile
import tomllib
from typing import Any

from em_client import AccountConfig

HISTORY_COLUMNS = [
    "Timestamp",
    "Category",
    "ManuscriptNumber",
    "Title",
    "SubmissionBeganDate",
    "StatusDate",
    "Status",
]


def _toml_quote(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def load_accounts(root: Path | None = None) -> list[AccountConfig]:
    """Load accounts from config.toml, falling back to legacy config.py if present."""
    base = root or Path.cwd()
    toml_path = base / "config.toml"
    py_path = base / "config.py"

    raw_accounts: list[dict[str, Any]] = []
    if toml_path.exists():
        doc = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        raw_accounts = list(doc.get("accounts", []))
    elif py_path.exists():
        spec = importlib.util.spec_from_file_location("_legacy_em_config", str(py_path))
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            raw_accounts = list(getattr(mod, "ACCOUNTS", []))

    accounts: list[AccountConfig] = []
    for item in raw_accounts:
        username = str(item.get("username", "")).strip()
        password = str(item.get("password", ""))
        short = str(item.get("journal_short_name", "")).strip()
        full = str(item.get("journal_full_name", "") or short).strip()
        if not username or not password or not short:
            continue
        if "your_username" in username.lower():
            continue
        accounts.append(
            AccountConfig(
                journal_short_name=short,
                journal_full_name=full,
                username=username,
                password=password,
            )
        )
    return accounts


def save_accounts_toml(accounts: list[AccountConfig], config_path: Path) -> None:
    """Atomically write config.toml with 0600 permissions."""
    blocks = ["# Private Editorial Manager configuration (mode 0600).\n"]
    for acc in accounts:
        blocks.append(
            "[[accounts]]\n"
            f"journal_short_name = {_toml_quote(acc.journal_slug)}\n"
            f"journal_full_name = {_toml_quote(acc.journal_full_name)}\n"
            f"username = {_toml_quote(acc.username)}\n"
            f"password = {_toml_quote(acc.password)}\n"
        )
    content = "\n".join(blocks)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".config.", suffix=".toml", dir=str(config_path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_name, config_path)
        os.chmod(config_path, 0o600)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def sanitize_filename(text: str, max_length: int = 80) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "", text)
    cleaned = re.sub(r"\s+", "-", cleaned.strip())
    return cleaned[:max_length] or "untitled"


def find_column(row: dict[str, Any], candidates: list[str]) -> str:
    for key, value in row.items():
        if key.startswith("_"):
            continue
        norm = re.sub(r"[^a-z0-9]", "", key.casefold())
        if any(token in norm for token in candidates):
            return str(value).strip()
    return ""


def normalize_manuscript(raw: dict[str, Any]) -> dict[str, str]:
    title = find_column(raw, ["title"]) or "Untitled"
    ms_number = (
        str(raw.get("_extracted_ms_number") or "").strip()
        or find_column(raw, ["manuscriptnumber"])
    )
    status = (
        find_column(raw, ["currentstatus", "status", "finaldisposition"])
        or str(raw.get("_category") or "")
    )
    status_date = find_column(raw, ["statusdate"])
    submitted_date = find_column(raw, ["submissionbegan", "initialdate", "datesubmitted"])
    docid = str(raw.get("_docid") or "").strip()
    guid = str(raw.get("_extracted_guid") or "").strip()

    if ms_number:
        stable_id = sanitize_filename(ms_number, max_length=40)
    elif docid:
        stable_id = f"docid_{docid}"
    elif guid:
        stable_id = f"guid_{sanitize_filename(guid, max_length=24)}"
    else:
        digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:10]
        stable_id = f"hash_{digest}"

    return {
        "stable_id": stable_id,
        "Category": str(raw.get("_category") or ""),
        "ManuscriptNumber": ms_number,
        "Title": title,
        "SubmissionBeganDate": submitted_date,
        "StatusDate": status_date,
        "Status": status,
    }


def record_manuscript_status(
    data_root: Path,
    account: AccountConfig,
    manuscript: dict[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[str, dict[str, str], Path]:
    """Append to CSV history only when (Status, StatusDate) changes.

    Returns (change_type, normalized_fields, csv_path) where change_type is one of:
    'created', 'updated', 'unchanged'.
    """
    norm = normalize_manuscript(manuscript)
    user_dir = data_root / sanitize_filename(account.username, 60) / account.journal_slug
    user_dir.mkdir(parents=True, exist_ok=True)

    prefix = f"{norm['stable_id']}_"
    target_dir = user_dir / f"{prefix}{sanitize_filename(norm['Title'])}"
    active_dir = target_dir

    for entry in user_dir.iterdir():
        if entry.is_dir() and entry.name.startswith(prefix):
            if entry != target_dir and not target_dir.exists():
                try:
                    entry.rename(target_dir)
                    active_dir = target_dir
                except OSError:
                    active_dir = entry
            else:
                active_dir = entry
            break

    active_dir.mkdir(parents=True, exist_ok=True)
    csv_path = active_dir / "history.csv"

    existing_rows: list[dict[str, str]] = []
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            existing_rows = list(csv.DictReader(handle))

    ts = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    row_to_write = {
        "Timestamp": ts,
        "Category": norm["Category"],
        "ManuscriptNumber": norm["ManuscriptNumber"],
        "Title": norm["Title"],
        "SubmissionBeganDate": norm["SubmissionBeganDate"],
        "StatusDate": norm["StatusDate"],
        "Status": norm["Status"],
    }

    if not existing_rows:
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=HISTORY_COLUMNS)
            writer.writeheader()
            writer.writerow(row_to_write)
        return ("created", row_to_write, csv_path)

    last = existing_rows[-1]
    if last.get("Status", "") == norm["Status"] and last.get("StatusDate", "") == norm["StatusDate"]:
        return ("unchanged", row_to_write, csv_path)

    existing_rows.append(row_to_write)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_COLUMNS)
        writer.writeheader()
        writer.writerows(existing_rows)
    return ("updated", row_to_write, csv_path)
