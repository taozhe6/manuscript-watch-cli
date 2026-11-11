"""Interactive helper to verify and append an Editorial Manager account into config.toml."""

from __future__ import annotations

import getpass
from pathlib import Path
import sys

from em_client import AccountConfig, EditorialManagerError, login_author_session
from storage import load_accounts, save_accounts_toml


def main() -> int:
    root = Path.cwd()
    config_path = root / "config.toml"
    existing = load_accounts(root)

    print("Add Editorial Manager Account")
    journal_short = input("Journal short code (from editorialmanager.com/<code/>, e.g. gastro): ").strip().lower()
    journal_full = input("Journal display name (optional, Enter to use code): ").strip() or journal_short.upper()
    username = input("Username: ").strip()
    password = getpass.getpass("Password (hidden): ")

    if not journal_short or not username or not password:
        print("Error: journal code, username, and password are required.", file=sys.stderr)
        return 1

    candidate = AccountConfig(
        journal_short_name=journal_short,
        journal_full_name=journal_full,
        username=username,
        password=password,
    )

    for acc in existing:
        if acc.journal_slug == candidate.journal_slug and acc.username.casefold() == candidate.username.casefold():
            print(f"Account already exists for {candidate.username} @ {candidate.journal_slug}.", file=sys.stderr)
            return 1

    print(f"Verifying login against {candidate.journal_base_url} ...")
    try:
        login_author_session(candidate)
    except EditorialManagerError as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1

    existing.append(candidate)
    save_accounts_toml(existing, config_path)
    print(f"Saved verified account to {config_path} (permissions 0600).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
