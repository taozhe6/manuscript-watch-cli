"""Command-line entrypoint for manuscript-watch-cli (`mwatch`)."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import platform
import sys
from typing import Sequence

from manuscript_watch_cli import __version__
from manuscript_watch_cli.config import (
    check_config_permissions,
    load_accounts,
    load_raw_document,
    resolve_config_path,
    resolve_data_dir,
    save_raw_entries,
    write_starter_config,
)
from manuscript_watch_cli.ledger import (
    iter_ledger_files,
    list_history_events,
    list_latest_records,
    record_snapshot,
)
from manuscript_watch_cli.models import (
    ConfigError,
    ExitCode,
    MWatchError,
    TargetAccount,
)
from manuscript_watch_cli.providers import get_provider, list_providers
from manuscript_watch_cli.render import emit_records

FORMAT_CHOICES = ("table", "json", "jsonl", "tsv")


def _log(message: str, *, quiet: bool = False) -> None:
    if not quiet:
        print(message, file=sys.stderr)


def _select_accounts(
    accounts: list[TargetAccount],
    *,
    targets: Sequence[str],
    journal_filter: str | None,
) -> list[TargetAccount]:
    selected = list(accounts)
    if journal_filter:
        jf = journal_filter.strip().casefold()
        selected = [
            acc
            for acc in selected
            if acc.journal_slug == jf or jf in acc.name.casefold()
        ]
    if targets:
        tokens = {t.strip().casefold() for t in targets if t.strip()}
        selected = [
            acc
            for acc in selected
            if acc.effective_id.casefold() in tokens
            or acc.journal_slug in tokens
            or acc.username.casefold() in tokens
        ]
    return selected


def cmd_init(args: argparse.Namespace) -> int:
    cfg_path = resolve_config_path(args.config)
    write_starter_config(cfg_path, force=args.force)
    _log(f"Initialized starter config at {cfg_path} (mode 0600).", quiet=args.quiet)
    return int(ExitCode.OK)


def cmd_providers(args: argparse.Namespace) -> int:
    rows = [{"name": p.name, "description": p.description} for p in list_providers()]
    emit_records(
        rows,
        fmt=args.format,
        columns=[("name", "PROVIDER", 24), ("description", "DESCRIPTION", 72)],
    )
    return int(ExitCode.OK)


def cmd_account_list(args: argparse.Namespace) -> int:
    cfg_path = resolve_config_path(args.config)
    accounts = load_accounts(cfg_path, include_disabled=True, resolve_secrets=False)
    rows = [acc.public_summary() for acc in accounts]
    emit_records(
        rows,
        fmt=args.format,
        columns=[
            ("id", "ID", 28),
            ("provider", "PROVIDER", 20),
            ("journal", "JOURNAL", 14),
            ("name", "DISPLAY NAME", 28),
            ("username", "USERNAME", 28),
            ("enabled", "ENABLED", 8),
        ],
    )
    return int(ExitCode.OK)


def _prompt_if_tty(prompt: str) -> str:
    if not sys.stdin.isatty():
        return ""
    try:
        return input(prompt)
    except EOFError:
        return ""


def cmd_account_add(args: argparse.Namespace) -> int:
    cfg_path = resolve_config_path(args.config)
    doc = load_raw_document(cfg_path)
    entries = list(doc.get("accounts", []))

    provider_name = (args.provider or "editorial_manager").strip().lower().replace("-", "_")
    provider = get_provider(provider_name)

    journal = (args.journal or _prompt_if_tty("Journal short code (e.g. gastro): ")).strip().lower()
    if not journal:
        raise ConfigError("Journal short code is required (pass --journal).")
    if args.name:
        name = args.name.strip()
    elif not args.journal and sys.stdin.isatty():
        name = _prompt_if_tty(f"Display name [{journal.upper()}]: ").strip() or journal.upper()
    else:
        name = journal.upper()
    username = (args.username or _prompt_if_tty("Username: ")).strip()
    account_id = (args.id or f"{journal}:{username}").strip()

    password_env = (args.password_env or "").strip()
    password_cmd = (args.password_cmd or "").strip()
    secret = ""
    if password_env:
        secret = os.environ.get(password_env, "")
        if not secret and not args.no_verify:
            raise ConfigError(f"Environment variable {password_env!r} is empty; cannot verify login.")
    elif password_cmd:
        pass
    else:
        secret = os.environ.get("MWATCH_PASSWORD", "")
        if not secret and sys.stdin.isatty():
            secret = getpass.getpass("Password (hidden): ")

    if not journal or not username or (not secret and not password_env and not password_cmd):
        raise ConfigError("Journal code, username, and credential source are required.")

    for existing in entries:
        ex_j = str(existing.get("journal") or existing.get("journal_short_name") or "").strip().lower()
        ex_u = str(existing.get("username") or "").strip().casefold()
        if ex_j == journal and ex_u == username.casefold():
            raise ConfigError(f"Account already configured: {username} @ {journal}")

    new_entry: dict[str, object] = {
        "id": account_id,
        "provider": provider.name,
        "journal": journal,
        "name": name,
        "username": username,
        "enabled": True,
    }
    if password_env:
        new_entry["password_env"] = password_env
    elif password_cmd:
        new_entry["password_cmd"] = password_cmd
    else:
        new_entry["password"] = secret

    if not args.no_verify:
        from manuscript_watch_cli.config import _resolve_secret

        resolved = _resolve_secret(new_entry, account_id)
        candidate = TargetAccount(
            journal=journal,
            name=name,
            username=username,
            secret=resolved,
            provider=provider.name,
            account_id=account_id,
        )
        _log(f"Verifying {provider.name} login for {journal} ...", quiet=args.quiet)
        provider.verify_auth(candidate, timeout=args.timeout)

    entries.append(new_entry)
    save_raw_entries(entries, cfg_path)
    _log(f"Saved {account_id} to {cfg_path} (mode 0600).", quiet=args.quiet)
    return int(ExitCode.OK)


def cmd_account_remove(args: argparse.Namespace) -> int:
    cfg_path = resolve_config_path(args.config)
    doc = load_raw_document(cfg_path)
    entries = list(doc.get("accounts", []))
    target = args.target.strip().casefold()

    kept = []
    removed = 0
    for item in entries:
        j = str(item.get("journal") or item.get("journal_short_name") or "").strip().lower()
        u = str(item.get("username") or "").strip()
        aid = str(item.get("id") or f"{j}:{u}").strip()
        if target in (aid.casefold(), j.casefold()):
            removed += 1
        else:
            kept.append(item)

    if removed == 0:
        raise ConfigError(f"No configured account matched {args.target!r}.")

    save_raw_entries(kept, cfg_path)
    _log(f"Removed {removed} account(s) from {cfg_path}.", quiet=args.quiet)
    return int(ExitCode.OK)


def cmd_check(args: argparse.Namespace) -> int:
    cfg_path = resolve_config_path(args.config)
    data_dir = resolve_data_dir(args.data_dir, cfg_path)
    accounts = load_accounts(cfg_path)
    selected = _select_accounts(accounts, targets=args.targets, journal_filter=args.journal)

    if not selected:
        raise ConfigError(
            f"No enabled accounts matched (config: {cfg_path}). "
            "Run `mwatch init` or `mwatch account add` first."
        )

    ms_filter = (args.manuscript or "").strip().casefold()
    all_transitions: list[dict[str, object]] = []
    worst_code = ExitCode.OK

    for acc in selected:
        provider = get_provider(acc.provider)
        _log(f"Checking {acc.name} ({acc.journal_slug}) as {acc.username} ...", quiet=args.quiet)
        try:
            snapshots = provider.fetch_submissions(acc, timeout=args.timeout)
        except MWatchError as exc:
            _log(f"error: {exc}", quiet=False)
            if int(exc.exit_code) > int(worst_code):
                worst_code = exc.exit_code
            continue

        for snap in snapshots:
            if ms_filter and (
                ms_filter not in snap.manuscript_number.casefold()
                and ms_filter not in snap.title.casefold()
            ):
                continue
            tr = record_snapshot(data_dir, snap, dry_run=args.no_save)
            if args.changed_only and tr.change == "unchanged":
                continue
            row = tr.to_dict()
            row["display_status"] = (
                f"{tr.previous_status} -> {snap.status}"
                if tr.change == "updated" and tr.previous_status != snap.status
                else snap.status
            )
            all_transitions.append(row)

    emit_records(
        all_transitions,
        fmt=args.format,
        columns=[
            ("change", "STATE", 10),
            ("journal", "JOURNAL", 12),
            ("manuscript_number", "MANUSCRIPT", 22),
            ("display_status", "STATUS", 34),
            ("status_date", "STATUS DATE", 16),
            ("title", "TITLE", 48),
        ],
    )

    if worst_code != ExitCode.OK:
        return int(worst_code)
    has_changes = any(item["change"] in ("created", "updated") for item in all_transitions)
    if args.exit_code_on_change and has_changes:
        return int(ExitCode.CHANGED)
    return int(ExitCode.OK)


def cmd_status(args: argparse.Namespace) -> int:
    cfg_path = resolve_config_path(args.config)
    data_dir = resolve_data_dir(args.data_dir, cfg_path)
    rows = list_latest_records(
        data_dir,
        journal_filter=args.journal or "",
        manuscript_filter=args.manuscript or "",
    )
    emit_records(
        rows,
        fmt=args.format,
        columns=[
            ("journal", "JOURNAL", 12),
            ("manuscript_number", "MANUSCRIPT", 22),
            ("status", "STATUS", 28),
            ("status_date", "STATUS DATE", 16),
            ("transitions", "STEPS", 6),
            ("title", "TITLE", 48),
        ],
    )
    return int(ExitCode.OK)


def cmd_history(args: argparse.Namespace) -> int:
    cfg_path = resolve_config_path(args.config)
    data_dir = resolve_data_dir(args.data_dir, cfg_path)
    events = list_history_events(data_dir, query=args.query or "", limit=args.limit)
    emit_records(
        events,
        fmt=args.format,
        columns=[
            ("timestamp", "RECORDED AT", 20),
            ("journal", "JOURNAL", 12),
            ("manuscript_number", "MANUSCRIPT", 22),
            ("status", "STATUS", 28),
            ("status_date", "STATUS DATE", 16),
            ("title", "TITLE", 40),
        ],
    )
    return int(ExitCode.OK)


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg_path = resolve_config_path(args.config)
    data_dir = resolve_data_dir(args.data_dir, cfg_path)
    perm_ok, perm_detail = check_config_permissions(cfg_path)
    accounts: list[TargetAccount] = []
    cfg_error = ""
    if cfg_path.exists():
        try:
            accounts = load_accounts(cfg_path, include_disabled=True, resolve_secrets=args.live)
        except MWatchError as exc:
            cfg_error = str(exc)

    live_results: list[dict[str, str]] = []
    overall_ok = True
    if cfg_path.exists() and not perm_ok:
        overall_ok = False
    if cfg_error:
        overall_ok = False

    if args.live and not cfg_error:
        for acc in accounts:
            if not acc.enabled:
                continue
            prov = get_provider(acc.provider)
            try:
                prov.verify_auth(acc, timeout=args.timeout)
                live_results.append({"account": acc.effective_id, "result": "ok"})
            except MWatchError as exc:
                overall_ok = False
                live_results.append({"account": acc.effective_id, "result": str(exc)})

    report = {
        "version": __version__,
        "python": platform.python_version(),
        "providers": [p.name for p in list_providers()],
        "config_path": str(cfg_path),
        "config_permissions_ok": perm_ok,
        "config_detail": cfg_error or perm_detail,
        "configured_accounts": len(accounts),
        "data_dir": str(data_dir),
        "tracked_manuscripts": len(iter_ledger_files(data_dir)),
        "live_checks": live_results,
        "healthy": overall_ok,
    }

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"manuscript-watch-cli {report['version']} (Python {report['python']})")
        print(f"  providers          : {', '.join(report['providers'])}")
        print(f"  config             : {report['config_detail']}")
        print(f"  accounts           : {report['configured_accounts']}")
        print(f"  ledger directory   : {report['data_dir']} ({report['tracked_manuscripts']} manuscript ledgers)")
        for item in live_results:
            print(f"  live auth [{item['account']}] : {item['result']}")

    return int(ExitCode.OK if overall_ok else ExitCode.USAGE_OR_CONFIG)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", metavar="PATH", help="Path to config.toml.")
    common.add_argument("--data-dir", metavar="DIR", help="Directory for CSV status ledgers.")
    common.add_argument("-q", "--quiet", action="store_true", help="Suppress progress messages on stderr.")

    parser = argparse.ArgumentParser(
        prog="mwatch",
        parents=[common],
        description="Command-line tracker and local status history ledger for academic manuscript submissions.",
        epilog=(
            "Exit codes:\n"
            "  0  Success\n"
            "  1  Invalid arguments or configuration error\n"
            "  2  Status transition detected (when --exit-code-on-change is set)\n"
            "  3  Authentication rejected or account locked\n"
            "  4  Upstream network or HTML parsing error"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_init = sub.add_parser("init", parents=[common], help="Create a starter config.toml (mode 0600).")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing config.toml.")
    p_init.set_defaults(handler=cmd_init)

    p_chk = sub.add_parser("check", parents=[common], help="Poll live manuscript statuses and record transitions.")
    p_chk.add_argument("targets", nargs="*", help="Optional account IDs, journal codes, or usernames to check.")
    p_chk.add_argument("--journal", "-j", metavar="CODE", help="Filter by journal short code or name.")
    p_chk.add_argument("--manuscript", "-m", metavar="QUERY", help="Filter by manuscript number or title substring.")
    p_chk.add_argument("--changed-only", action="store_true", help="Output only newly created or updated statuses.")
    p_chk.add_argument("--no-save", action="store_true", help="Dry-run without writing CSV history.")
    p_chk.add_argument(
        "--exit-code-on-change",
        action="store_true",
        help="Exit with code 2 if any manuscript is newly created or updated (cron/hook friendly).",
    )
    p_chk.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds (default: 20).")
    p_chk.add_argument("--format", "-f", choices=FORMAT_CHOICES, default="table", help="Output format.")
    p_chk.set_defaults(handler=cmd_check)

    p_st = sub.add_parser("status", parents=[common], help="Show latest recorded statuses offline (no network).")
    p_st.add_argument("--journal", "-j", metavar="CODE", help="Filter by journal code or name.")
    p_st.add_argument("--manuscript", "-m", metavar="QUERY", help="Filter by manuscript number or title substring.")
    p_st.add_argument("--format", "-f", choices=FORMAT_CHOICES, default="table", help="Output format.")
    p_st.set_defaults(handler=cmd_status)

    p_hist = sub.add_parser("history", parents=[common], help="Show chronological status transition log offline.")
    p_hist.add_argument("query", nargs="?", default="", help="Optional filter (journal, manuscript ID, or title).")
    p_hist.add_argument("--limit", "-n", type=int, default=50, help="Maximum events to show (default: 50, 0=all).")
    p_hist.add_argument("--format", "-f", choices=FORMAT_CHOICES, default="table", help="Output format.")
    p_hist.set_defaults(handler=cmd_history)

    p_acc = sub.add_parser("account", parents=[common], help="Manage configured journal accounts.")
    acc_sub = p_acc.add_subparsers(dest="account_cmd", required=True, metavar="<subcommand>")

    p_acc_ls = acc_sub.add_parser("list", parents=[common], help="List configured accounts (secrets redacted).")
    p_acc_ls.add_argument("--format", "-f", choices=FORMAT_CHOICES, default="table", help="Output format.")
    p_acc_ls.set_defaults(handler=cmd_account_list)

    p_acc_add = acc_sub.add_parser("add", parents=[common], help="Verify and add an account to config.toml.")
    p_acc_add.add_argument("--id", help="Custom account identifier.")
    p_acc_add.add_argument("--provider", default="editorial_manager", help="Provider name (default: editorial_manager).")
    p_acc_add.add_argument("--journal", "-j", help="Journal short code (e.g. gastro).")
    p_acc_add.add_argument("--name", help="Journal display name.")
    p_acc_add.add_argument("--username", "-u", help="Account username.")
    p_acc_add.add_argument("--password-env", help="Read secret from this environment variable instead of storing it.")
    p_acc_add.add_argument("--password-cmd", help="Shell command that prints the secret to stdout (e.g. Keychain/1Password).")
    p_acc_add.add_argument("--no-verify", action="store_true", help="Save without testing live login.")
    p_acc_add.add_argument("--timeout", type=int, default=20, help="Verification timeout in seconds.")
    p_acc_add.set_defaults(handler=cmd_account_add)

    p_acc_rm = acc_sub.add_parser("remove", parents=[common], help="Remove an account by ID or journal code.")
    p_acc_rm.add_argument("target", help="Account ID or journal short code.")
    p_acc_rm.set_defaults(handler=cmd_account_remove)

    p_prov = sub.add_parser("providers", parents=[common], help="List registered submission platform providers.")
    p_prov.add_argument("--format", "-f", choices=FORMAT_CHOICES, default="table", help="Output format.")
    p_prov.set_defaults(handler=cmd_providers)

    p_doc = sub.add_parser("doctor", parents=[common], help="Inspect config permissions, ledger paths, and connectivity.")
    p_doc.add_argument("--live", action="store_true", help="Also verify live authentication for enabled accounts.")
    p_doc.add_argument("--timeout", type=int, default=20, help="HTTP timeout for --live checks.")
    p_doc.add_argument("--format", "-f", choices=("table", "json"), default="table", help="Output format.")
    p_doc.set_defaults(handler=cmd_doctor)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return int(ExitCode.USAGE_OR_CONFIG)
    try:
        return int(args.handler(args))
    except MWatchError as exc:
        print(f"mwatch: error: {exc}", file=sys.stderr)
        return int(exc.exit_code)
    except KeyboardInterrupt:
        print("\nmwatch: interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
