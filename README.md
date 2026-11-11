# em-tracker

Lightweight Python CLI for checking manuscript statuses on journals running **Editorial Manager** (`editorialmanager.com`) with native username/password accounts.

It logs in over standard HTTPS sessions (`requests` + `BeautifulSoup`), traverses non-empty categories on `AuthorMainMenu.aspx`, and records status transitions to local UTF-8 CSV files under `data/`.

## Scope

- Supports native Editorial Manager author login (`editorialmanager.com/<journal_code>/`).
- Does **not** automate browser-based SSO flows (such as Elsevier SSO, Springer Nature SNAPP, or ORCID).
- Aborts immediately without retrying when Editorial Manager reports invalid credentials or a locked account (`AccountLocked`), retrying only on transient network errors.

## Quick Start

Requires Python 3.11+.

```bash
git clone https://github.com/taozhe6/em-tracker.git
cd em-tracker

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp config.example.toml config.toml
chmod 600 config.toml
```

Edit `config.toml` (or run `.venv/bin/python add_config.py` to verify login interactively with hidden password input):

```toml
[[accounts]]
journal_short_name = "gastro"
journal_full_name = "Gastroenterology"
username = "your_username"
password = "your_password"
```

`journal_short_name` is the path segment from `https://www.editorialmanager.com/<journal_short_name>/`.

## Usage

Check all configured accounts non-interactively (suitable for scripts or cron):

```bash
.venv/bin/python main.py --all
```

Check a single journal without writing local CSV history:

```bash
.venv/bin/python main.py --journal gastro --no-save
```

Launch the interactive selector (also supports one-off temporary queries without saving credentials):

```bash
.venv/bin/python main.py
```

## Status History

When saving is enabled (the default), status snapshots are appended to:

```text
data/<username>/<journal_short_name>/<manuscript_id>_<title>/history.csv
```

A new row is written only when `Status` or `StatusDate` changes compared with the previous recorded entry.

## License

MIT License. See [LICENSE](LICENSE).
