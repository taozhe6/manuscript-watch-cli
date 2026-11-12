"""XDG-aware configuration discovery, secret resolution, and atomic TOML persistence."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import tomllib
from typing import Any

from manuscript_watch_cli.models import ConfigError, TargetAccount


def resolve_config_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env_cfg = os.environ.get("MWATCH_CONFIG", "").strip()
    if env_cfg:
        return Path(env_cfg).expanduser().resolve()
    local_cfg = Path.cwd() / "config.toml"
    if local_cfg.exists():
        return local_cfg.resolve()
    xdg_cfg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg_cfg).expanduser() if xdg_cfg else (Path.home() / ".config")
    return (base / "manuscript-watch" / "config.toml").resolve()


def resolve_data_dir(explicit: str | None, config_path: Path) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env_data = os.environ.get("MWATCH_DATA_DIR", "").strip()
    if env_data:
        return Path(env_data).expanduser().resolve()
    if config_path.parent == Path.cwd().resolve():
        return (Path.cwd() / "data").resolve()
    xdg_data = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg_data).expanduser() if xdg_data else (Path.home() / ".local" / "share")
    return (base / "manuscript-watch" / "history").resolve()


def _toml_str(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _resolve_secret(entry: dict[str, Any], label: str) -> str:
    env_key = str(entry.get("password_env", "")).strip()
    if env_key:
        val = os.environ.get(env_key, "")
        if not val:
            raise ConfigError(f"Account {label}: environment variable {env_key!r} is unset or empty.")
        return val

    cmd_str = str(entry.get("password_cmd", "")).strip()
    if cmd_str:
        try:
            out = subprocess.check_output(shlex.split(cmd_str), text=True, timeout=10)
        except Exception as exc:
            raise ConfigError(f"Account {label}: password_cmd failed: {exc}") from exc
        secret = out.rstrip("\r\n")
        if not secret:
            raise ConfigError(f"Account {label}: password_cmd returned empty output.")
        return secret

    direct = str(entry.get("password", ""))
    if not direct or direct == "your_password":
        raise ConfigError(f"Account {label}: missing credential (set password, password_env, or password_cmd).")
    return direct


def load_raw_document(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {"accounts": []}
    try:
        return tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {config_path}: {exc}") from exc


def load_accounts(
    config_path: Path,
    *,
    include_disabled: bool = False,
    resolve_secrets: bool = True,
) -> list[TargetAccount]:
    doc = load_raw_document(config_path)
    raw_list = doc.get("accounts", [])
    if not isinstance(raw_list, list):
        raise ConfigError(f"{config_path}: `accounts` must be an array of tables (`[[accounts]]`).")

    accounts: list[TargetAccount] = []
    for idx, entry in enumerate(raw_list, 1):
        if not isinstance(entry, dict):
            continue
        enabled = bool(entry.get("enabled", True))
        if not enabled and not include_disabled:
            continue

        journal = str(entry.get("journal") or entry.get("journal_short_name") or "").strip()
        username = str(entry.get("username") or "").strip()
        if not journal or not username or username == "your_username":
            continue

        name = str(entry.get("name") or entry.get("journal_full_name") or journal.upper()).strip()
        provider = str(entry.get("provider") or "editorial_manager").strip().lower().replace("-", "_")
        account_id = str(entry.get("id") or "").strip()
        label = account_id or f"{journal}:{username}"

        secret = _resolve_secret(entry, label) if (resolve_secrets and enabled) else str(entry.get("password", ""))
        accounts.append(
            TargetAccount(
                journal=journal,
                name=name,
                username=username,
                secret=secret,
                provider=provider,
                account_id=account_id,
                enabled=enabled,
            )
        )
    return accounts


def write_starter_config(config_path: Path, *, force: bool = False) -> None:
    if config_path.exists() and not force:
        raise ConfigError(f"{config_path} already exists (pass --force to overwrite).")
    template = (
        "# manuscript-watch-cli configuration (keep mode 0600).\n"
        "# Tip: use `password_env` or `password_cmd` if you prefer not storing secrets on disk.\n\n"
        "[[accounts]]\n"
        'id = "gastro-main"\n'
        'provider = "editorial_manager"\n'
        'journal = "gastro"\n'
        'name = "Gastroenterology"\n'
        'username = "your_username"\n'
        'password = "your_password"\n'
        "enabled = true\n"
    )
    _atomic_write_0600(config_path, template)


def save_raw_entries(entries: list[dict[str, Any]], config_path: Path) -> None:
    blocks = ["# manuscript-watch-cli configuration (mode 0600).\n"]
    for item in entries:
        lines = ["[[accounts]]"]
        if item.get("id"):
            lines.append(f"id = {_toml_str(str(item['id']))}")
        lines.append(f"provider = {_toml_str(str(item.get('provider', 'editorial_manager')))}")
        journal = str(item.get("journal") or item.get("journal_short_name") or "").strip().lower()
        name = str(item.get("name") or item.get("journal_full_name") or journal.upper()).strip()
        lines.append(f"journal = {_toml_str(journal)}")
        lines.append(f"name = {_toml_str(name)}")
        lines.append(f"username = {_toml_str(str(item['username']))}")
        if item.get("password_env"):
            lines.append(f"password_env = {_toml_str(str(item['password_env']))}")
        elif item.get("password_cmd"):
            lines.append(f"password_cmd = {_toml_str(str(item['password_cmd']))}")
        else:
            lines.append(f"password = {_toml_str(str(item.get('password', '')))}")
        lines.append(f"enabled = {'true' if item.get('enabled', True) else 'false'}")
        blocks.append("\n".join(lines) + "\n")

    _atomic_write_0600(config_path, "\n".join(blocks))


def _atomic_write_0600(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".mwatch.", suffix=".toml", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def check_config_permissions(config_path: Path) -> tuple[bool, str]:
    if not config_path.exists():
        return (False, f"Config file not found at {config_path}")
    mode = config_path.stat().st_mode & 0o777
    if mode & 0o077:
        return (False, f"Insecure permissions {oct(mode)} on {config_path} (run `chmod 600 {config_path}`)")
    return (True, f"{config_path} (mode {oct(mode)})")
