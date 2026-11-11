"""Command-line Editorial Manager manuscript status tracker."""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path
import sys

from em_client import (
    AccountConfig,
    EditorialManagerError,
    fetch_all_manuscripts,
    login_author_session,
)
from storage import load_accounts, record_manuscript_status


def check_account(
    account: AccountConfig,
    *,
    data_dir: Path,
    no_save: bool = False,
) -> bool:
    print(f"\n[{account.journal_full_name}] ({account.journal_slug}) user={account.username}")
    try:
        session = login_author_session(account)
        manuscripts = fetch_all_manuscripts(session, account)
    except EditorialManagerError as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        return False

    if not manuscripts:
        print("  No active or historical manuscripts found.")
        return True

    print(f"  Found {len(manuscripts)} manuscript(s):")
    for item in manuscripts:
        if no_save:
            from storage import normalize_manuscript

            row = normalize_manuscript(item)
            ms_label = row["ManuscriptNumber"] or row["stable_id"]
            print(f"  - [{ms_label}] {row['Status']} ({row['StatusDate']}) | {row['Title'][:72]}")
            continue

        state, row, csv_path = record_manuscript_status(data_dir, account, item)
        tag = {"created": "NEW", "updated": "UPDATED", "unchanged": "OK"}[state]
        ms_label = row["ManuscriptNumber"] or "unassigned"
        print(f"  - [{tag}] {ms_label}: {row['Status']} ({row['StatusDate']}) | {row['Title'][:68]}")
        if state in ("created", "updated"):
            print(f"    saved -> {csv_path}")

    return True


def prompt_temporary_account() -> AccountConfig | None:
    short = input("Journal short code (e.g. gastro): ").strip().lower()
    full = input("Journal display name (optional): ").strip() or short.upper()
    username = input("Username: ").strip()
    password = getpass.getpass("Password (hidden): ")
    if not short or not username or not password:
        print("Cancelled: missing required fields.", file=sys.stderr)
        return None
    return AccountConfig(
        journal_short_name=short,
        journal_full_name=full,
        username=username,
        password=password,
    )


def interactive_menu(accounts: list[AccountConfig], data_dir: Path, no_save: bool) -> int:
    while True:
        print("\nEditorial Manager Tracker")
        for idx, acc in enumerate(accounts, 1):
            print(f"  [{idx}] {acc.journal_full_name} ({acc.journal_slug}) - {acc.username}")
        all_idx = len(accounts) + 1
        tmp_idx = len(accounts) + 2
        if accounts:
            print(f"  [{all_idx}] Check all configured journals")
        print(f"  [{tmp_idx}] One-off temporary query (not saved to config)")
        print("  [q] Quit")

        choice = input("Select option: ").strip().lower()
        if choice in ("q", "quit", "exit"):
            return 0
        if not choice.isdigit():
            print("Invalid selection.")
            continue

        num = int(choice)
        if 1 <= num <= len(accounts):
            check_account(accounts[num - 1], data_dir=data_dir, no_save=no_save)
        elif accounts and num == all_idx:
            for acc in accounts:
                check_account(acc, data_dir=data_dir, no_save=no_save)
        elif num == tmp_idx:
            temp_acc = prompt_temporary_account()
            if temp_acc:
                check_account(temp_acc, data_dir=data_dir, no_save=True)
        else:
            print("Invalid selection.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Track manuscript statuses across journals running Editorial Manager."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Check all configured accounts non-interactively and exit.",
    )
    parser.add_argument(
        "--journal",
        metavar="CODE",
        help="Check only journals matching this short code (e.g. gastro) and exit.",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Directory for CSV status history (default: ./data).",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Print current statuses without writing CSV history.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path.cwd()
    accounts = load_accounts(root)
    data_dir = Path(args.data_dir)

    if args.all or args.journal:
        selected = accounts
        if args.journal:
            slug = args.journal.strip().lower()
            selected = [acc for acc in accounts if acc.journal_slug == slug]
        if not selected:
            print(
                "No matching configured accounts found. Copy config.example.toml to config.toml "
                "or run `python add_config.py`.",
                file=sys.stderr,
            )
            return 1
        ok = True
        for acc in selected:
            if not check_account(acc, data_dir=data_dir, no_save=args.no_save):
                ok = False
        return 0 if ok else 1

    return interactive_menu(accounts, data_dir=data_dir, no_save=args.no_save)


if __name__ == "__main__":
    raise SystemExit(main())
