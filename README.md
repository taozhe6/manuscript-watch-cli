# manuscript-watch-cli

Command-line tracker and local status history ledger for academic manuscript submissions (`mwatch` / `manuscript-watch`).

It polls journal submission portals over HTTPS, records status transitions (including full Day-1 chronological stage histories on eJournalPress) to local UTF-8 CSV ledgers, and emits clean tabular or structured (`json`, `jsonl`, `tsv`) output suitable for interactive terminals, shell pipelines, and scheduled tasks.

## Overview

- **Subcommands**: `init`, `account`, `check`, `status`, `history`, `providers`, and `doctor`.
- **Built-in Providers**:
  - `editorial_manager`: pure-HTTP Editorial Manager session (`editorialmanager.com/<journal>/`, `role=author`).
  - `ejournalpress`: pure-HTTP eJournalPress session (`<slug>.msubmit.net` such as IOVS, TVST, JOV, plus `manuscripts.jama*.com` portals when reachable without interactive CAPTCHA), verifying folder counts and backfilling complete Day-1 `dump_history_table` stage timelines (`R1` / `R2` isolated).
- **Offline History Ledger**: Appends transitions to per-manuscript `history.csv` files; `status` and `history` read local ledgers without network requests.
- **Stream Discipline**: Diagnostic messages go to `stderr`; structured records (`--format table|json|jsonl|tsv`) go to `stdout` and honor `NO_COLOR`.
- **Semantic Exit Codes**: `--exit-code-on-change` exits with `2` when a manuscript is newly recorded or changes status.
- **Pluggable Architecture**: Discovers external provider plugins via Python package entry points (`manuscript_watch_cli.providers`).

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
mwatch account add --provider editorial_manager --journal gastro --name "Gastroenterology" --username alice
mwatch account add --provider ejournalpress --journal iovs --name "IOVS" --username alice --password-env EJOURNALPRESS_PASSWORD
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
mwatch providers
mwatch doctor
```

## Configuration

`config.toml` lookup order:
1. `--config <path>` or `$MWATCH_CONFIG`
2. `./config.toml` (writes ledgers under `./data/`)
3. `$XDG_CONFIG_HOME/manuscript-watch/config.toml` (writes ledgers under `$XDG_DATA_HOME/manuscript-watch/history/`)

```toml
[[accounts]]
id = "iovs-main"
provider = "ejournalpress"
journal = "iovs" # accepts short code (`iovs`, `jamaophth`) or full HTTPS URL
name = "IOVS"
username = "your_username"
password_env = "EJOURNALPRESS_PASSWORD"
enabled = true
```

Each account entry supports `password`, `password_env` (environment variable name), or `password_cmd` (external command printing the secret).

### Portal Scope & Browser Fallback

`editorial_manager` and `ejournalpress` authenticate over standard HTTPS and abort without retrying when upstream reports locked credentials or interactive CAPTCHA/Turnstile challenges. Portals requiring external browser SSO/ORCID login (Elsevier Submission on `submit.elsevier.com` / `submit.editorialmanager.com`, Springer Nature SNAPP) or interactive Cloudflare Turnstile verification belong in the companion browser-backed trackers (`manuscript-watch` / `Manuscript Desk`).

## Exit Codes

| Code | Meaning |
| :--- | :--- |
| `0` | Success |
| `1` | Invalid arguments or configuration error |
| `2` | Status change recorded (`--exit-code-on-change`) |
| `3` | Authentication rejected, account locked, or interactive CAPTCHA challenge |
| `4` | Network failure or unexpected HTML layout |

## Provider Plugins

Third-party or private packages can register custom providers under the `manuscript_watch_cli.providers` entry-point group by implementing `SubmissionProvider` (`name`, `description`, `verify_auth`, `fetch_submissions`) from `manuscript_watch_cli.providers.base`. Run `mwatch providers` to inspect active providers.

## License

MIT License. See [LICENSE](LICENSE).
