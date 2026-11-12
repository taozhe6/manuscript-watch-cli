# manuscript-watch-cli

Command-line tracker and local status history ledger for academic manuscript submissions (`mwatch` / `manuscript-watch`).

It polls journal submission portals over HTTPS, records status transitions to local UTF-8 CSV ledgers, and emits clean tabular or structured (`json`, `jsonl`, `tsv`) output suitable for interactive terminals, shell pipelines, and scheduled tasks.

## Overview

- **Subcommands**: `init`, `account`, `check`, `status`, `history`, `providers`, and `doctor`.
- **Offline History Ledger**: Appends transitions to per-manuscript `history.csv` files; `status` and `history` read local ledgers without network requests.
- **Stream Discipline**: Diagnostic messages go to `stderr`; structured records (`--format table|json|jsonl|tsv`) go to `stdout` and honor `NO_COLOR`.
- **Semantic Exit Codes**: `--exit-code-on-change` exits with `2` when a manuscript is newly recorded or changes status.
- **Pluggable Architecture**: Ships with a pure-HTTP `editorial_manager` provider and discovers external provider plugins via Python package entry points.

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Once installed, both `mwatch` and `manuscript-watch` commands are available in `.venv/bin` (or globally when installed with `pipx` / `uv tool`).

## Usage

Initialize a `0600` configuration file (defaults to `~/.config/manuscript-watch/config.toml`, or `./config.toml` when present in the working directory):

```bash
mwatch init
mwatch account add --journal gastro --name "Gastroenterology" --username alice
```

Poll live statuses and record any new transitions:

```bash
mwatch check
mwatch -q check --changed-only --exit-code-on-change --format json
```

Inspect recorded manuscripts and chronological status changes offline:

```bash
mwatch status
mwatch history
mwatch doctor
```

## Configuration

`config.toml` lookup order:
1. `--config <path>` or `$MWATCH_CONFIG`
2. `./config.toml` (writes ledgers under `./data/`)
3. `$XDG_CONFIG_HOME/manuscript-watch/config.toml` (writes ledgers under `$XDG_DATA_HOME/manuscript-watch/history/`)

```toml
[[accounts]]
id = "gastro-main"
provider = "editorial_manager"
journal = "gastro"
name = "Gastroenterology"
username = "your_username"
password = "your_password"
enabled = true
```

Instead of storing `password` directly, an account entry may specify `password_env` (environment variable name) or `password_cmd` (external command printing the secret).

### Built-in Provider Scope

`editorial_manager` authenticates over standard HTTPS against `editorialmanager.com/<journal>/` (`role=author`), expands paginated author tables up to 500 items, and aborts without retrying when upstream reports `AccountLocked`. Journals that disable native Editorial Manager passwords in favor of browser-only SSO/ORCID login require a custom provider plugin.

## Exit Codes

| Code | Meaning |
| :--- | :--- |
| `0` | Success |
| `1` | Invalid arguments or configuration error |
| `2` | Status change recorded (`--exit-code-on-change`) |
| `3` | Authentication rejected or account locked |
| `4` | Network failure or unexpected HTML layout |

## Provider Plugins

Third-party or private packages can register custom providers under the `manuscript_watch_cli.providers` entry-point group by implementing `SubmissionProvider` (`name`, `description`, `verify_auth`, `fetch_submissions`) from `manuscript_watch_cli.providers.base`. Run `mwatch providers` to inspect active providers.

## License

MIT License. See [LICENSE](LICENSE).
