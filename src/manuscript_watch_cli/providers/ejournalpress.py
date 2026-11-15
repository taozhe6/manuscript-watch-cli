"""Pure-HTTP eJournalPress provider (*.msubmit.net and manuscripts.jama*.com)."""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup
import requests

from manuscript_watch_cli.models import (
    AuthenticationError,
    ConfigError,
    ManuscriptSnapshot,
    ProviderFetchError,
    TargetAccount,
)

DEFAULT_RETRIES = 3
RETRY_DELAY_SECONDS = 3.0

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

MANUSCRIPT_RE = re.compile(
    r"\b(?:[A-Z][A-Z0-9]{1,11}-\d{2}|[A-Z]{2,10}\d{2})-\d+(?:-?R\d+)?\b", re.I
)
ENTRY_NAMES = {
    "live": "Live Manuscripts",
    "direct_manuscript": "Direct Manuscript Entry",
    "action_ejp": "Author Action Required",
    "post_decision": "Post Decision Manuscripts",
}
EXCLUDED_LINKS = re.compile(
    r"^(?:author instructions|reviewer instructions|manuscript home|journal home|help|terms of service|"
    r"submit (?:new )?manuscript|modify unavailability dates|"
    r"modify (?:profile(?:/password)?|password)|download copy of my records|log\s*out)\b",
    re.I,
)
REJECTED_RE = re.compile(
    r"(invalid (?:login|password|username|credentials)|incorrect (?:login|password|username)|"
    r"(?:login|password|username).{0,40}(?:incorrect|not found|invalid|not recognized|unrecognized)|"
    r"(?:user|account|username).{0,30}not found|(?:account|login).{0,30}locked|"
    r"unable to (?:log\s*in|sign\s*in|validate|authenticate)|do(?:es)? not match|login failed)",
    re.I,
)
CHALLENGE_RE = re.compile(
    r"(verify (?:that )?you are human|security (?:check|verification)|just a moment|"
    r"enter.{0,20}(?:verification code|captcha)|complete.{0,20}captcha|cf-turnstile)",
    re.I,
)


def normalized(text: str) -> str:
    return " ".join(text.split())


def ejp_host_slug(hostname: str | None) -> str:
    host = (hostname or "").lower()
    msubmit = re.fullmatch(r"([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)\.msubmit\.net", host)
    if msubmit:
        return msubmit.group(1)
    jama = re.fullmatch(r"manuscripts\.(jama(?:[a-z0-9-]{0,57}[a-z0-9])?)\.com", host)
    return jama.group(1) if jama else ""


def resolve_ejp_endpoint(raw_journal: str) -> tuple[str, str]:
    token = raw_journal.strip()
    if token.startswith("https://") or token.startswith("http://"):
        parsed = urlsplit(token)
        host = (parsed.hostname or "").lower()
        slug = ejp_host_slug(host)
        if parsed.scheme != "https" or not slug or parsed.username or parsed.port not in (None, 443):
            raise ConfigError(
                f"Invalid eJournalPress URL {raw_journal!r} "
                "(expected https://<slug>.msubmit.net/ or https://manuscripts.jama*.com/)."
            )
        return f"ejp-{slug}", f"https://{host}/cgi-bin/main.plex"

    clean = token.strip("/").lower().removeprefix("ejp-")
    if re.fullmatch(r"jama(?:[a-z0-9-]{0,57}[a-z0-9])?", clean):
        return f"ejp-{clean}", f"https://manuscripts.{clean}.com/cgi-bin/main.plex"
    if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", clean):
        return f"ejp-{clean}", f"https://{clean}.msubmit.net/cgi-bin/main.plex"
    raise ConfigError(f"Invalid eJournalPress journal code: {raw_journal!r}")


def manuscript_number(text: str, href: str = "") -> str:
    match = MANUSCRIPT_RE.search(text)
    if not match:
        match = MANUSCRIPT_RE.search(parse_qs(urlsplit(href).query).get("ms_id_key", [""])[0])
    return match.group(0).upper() if match else ""


def classify_home_link_text(text: str) -> str:
    text = normalized(text)
    if EXCLUDED_LINKS.search(text):
        return ""
    if re.search(r"\blive manuscripts\b", text, re.I):
        return "live"
    if re.search(r"\bpost[ -]decision manuscripts\b", text, re.I):
        return "post_decision"
    if re.match(r"^#\s*" + MANUSCRIPT_RE.pattern, text, re.I):
        return "direct_manuscript"
    return "action_ejp" if manuscript_number(text) else ""


def link_title(text: str, number: str) -> str:
    match = (
        re.search(re.escape(number) + r"\s*[-\u2013\u2014:]\s*(.+)$", normalized(text), re.I)
        if number
        else None
    )
    return match.group(1).strip() if match else ""


def link_entry(link: Any, index: int, selector: str, origin: str, kind: str) -> dict[str, Any]:
    text = normalized(link.get_text(" ", strip=True))
    href = link.get("href", "")
    base = link.find_previous("base", href=True)
    if href and base:
        href = urljoin(urljoin(origin, base["href"]), href)
    number = manuscript_number(text, href)
    key = number or parse_qs(urlsplit(href).query).get("ms_id_key", [""])[0]
    return {
        "number": number,
        "key": key,
        "title": link_title(text, number),
        "title_rank": 1,
        "kind": kind,
        "href": href,
        "origin": origin,
        "selector": selector,
        "index": index,
    }


def parse_home_entries(html: str, home_url: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    soup = BeautifulSoup(html, "lxml")
    selector = 'tbody.desktop-section[role="author"] a'
    area = soup.select_one('tbody.desktop-section[role="author"]')
    if (
        area is None
        and re.search(r"\bAuthor Tasks\b", soup.get_text(" ", strip=True), re.I)
        and any(
            re.fullmatch(r"log\s*out", normalized(link.get_text(" ", strip=True)), re.I)
            for link in soup.select("a")
        )
    ):
        area = soup
        selector = "a"
    if area is None:
        raise ProviderFetchError("EJP_INCOMPLETE: Author task section missing on home page.")

    folders: dict[str, dict[str, Any]] = {}
    direct: list[dict[str, Any]] = []
    for index, link in enumerate(area.select("a")):
        text = normalized(link.get_text(" ", strip=True))
        kind = classify_home_link_text(text)
        if not kind:
            continue
        entry = link_entry(link, index, selector, home_url, kind)
        if kind in ("live", "post_decision"):
            label = r"Live Manuscripts" if kind == "live" else r"Post[ -]Decision Manuscripts"
            context = normalized(link.parent.get_text(" ", strip=True))
            matches = re.findall(label + r"\s*\(([\d,]+)\)", text + " " + context, re.I)
            if not matches and link.find_parent("tr") is not None:
                row_context = normalized(link.find_parent("tr").get_text(" ", strip=True))
                matches = re.findall(label + r"\s*\(([\d,]+)\)", row_context, re.I)
            counts = {int(count.replace(",", "")) for count in matches}
            if len(counts) != 1:
                raise ProviderFetchError("EJP_INCOMPLETE: Folder manuscript count missing or conflicting.")
            entry["count"] = counts.pop()
            if kind in folders and folders[kind]["count"] != entry["count"]:
                raise ProviderFetchError("EJP_INCOMPLETE: Duplicate folder count mismatch on home page.")
            folders[kind] = entry
        else:
            direct.append(entry)
    if not folders and not direct:
        raise ProviderFetchError("EJP_INCOMPLETE: No verifiable manuscript folders or entries on home page.")
    return [folders[k] for k in ("live", "post_decision") if k in folders], direct


def parse_folder_entries(html: str, origin: str, kind: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    tables = soup.select("table.folder_table")
    selector = "table.folder_table a"
    if not tables:
        selector = "a"
        for link in soup.select("a"):
            ltext = normalized(link.get_text(" ", strip=True))
            lhref = link.get("href", "")
            if (
                re.match(r"^(?:View Manuscript\s+#|#?\s*" + MANUSCRIPT_RE.pattern + r"$)", ltext, re.I)
                or (
                    "ms_id_key=" in lhref
                    and "form_type=dt_folder" not in lhref
                    and not EXCLUDED_LINKS.search(ltext)
                )
            ):
                table = link.find_parent("table")
                if table is not None and all(table is not found for found in tables):
                    tables.append(table)
    if not tables:
        raise ProviderFetchError("EJP_INCOMPLETE: Folder page missing manuscript table.")

    links = soup.select(selector)
    indices = {id(link): index for index, link in enumerate(links)}
    entries: dict[str, dict[str, Any]] = {}
    for table in tables:
        title_column = -1
        for row in table.select("tr"):
            if row.find_parent("table") is not table:
                continue
            cells = row.find_all(["th", "td"], recursive=False)
            labels = [normalized(cell.get_text(" ", strip=True)).casefold().rstrip(":") for cell in cells]
            for index, label in enumerate(labels):
                if label in ("title", "manuscript title"):
                    title_column = index
            candidates = [
                link
                for link in row.select("a")
                if not EXCLUDED_LINKS.search(normalized(link.get_text(" ", strip=True)))
                and (
                    manuscript_number(link.get_text(" ", strip=True), link.get("href", ""))
                    or parse_qs(urlsplit(link.get("href", "")).query).get("ms_id_key")
                )
            ]
            if not candidates:
                continue
            link = next(
                (
                    item
                    for item in candidates
                    if re.search(r"view manuscript|^#", normalized(item.get_text(" ", strip=True)), re.I)
                ),
                candidates[0],
            )
            entry = link_entry(link, indices[id(link)], selector, origin, kind)
            if not entry["number"]:
                entry["number"] = manuscript_number(row.get_text(" ", strip=True))
                entry["key"] = entry["number"] or entry["key"]
            if 0 <= title_column < len(cells):
                title = normalized(cells[title_column].get_text(" ", strip=True))
                if title and title.casefold().rstrip(":") not in ("title", "manuscript title"):
                    entry.update(title=title, title_rank=3)
            if entry["key"] in entries:
                raise ProviderFetchError("EJP_INCOMPLETE: Duplicate manuscript row in folder.")
            entries[entry["key"]] = entry
    return list(entries.values())


def parse_stage_history(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table.dump_history_table")
    if table is None:
        raise ProviderFetchError("EJP_TABLE: Missing dump_history_table.")
    rows = [
        [cell.get_text(" ", strip=True).strip() for cell in row.find_all(["th", "td"], recursive=False)]
        for row in table.select("tr")
        if row.find_parent("table") is table
    ]
    header = -1
    stage_column = date_column = -1
    for index, row in enumerate(rows):
        keys = [normalized(value).casefold().rstrip(":") for value in row]
        stages = [i for i, key in enumerate(keys) if key in ("stage", "阶段")]
        dates = [i for i, key in enumerate(keys) if key in ("start date", "开始时间", "开始日期")]
        if stages and dates:
            header, stage_column, date_column = index, stages[0], dates[0]
            break
    if header < 0:
        raise ProviderFetchError("EJP_TABLE: Stage history table missing Stage / Start Date headers.")

    history: list[dict[str, str]] = []
    for row in rows[header + 1 :]:
        if not any(row):
            continue
        if len(row) <= max(stage_column, date_column) or not row[date_column]:
            raise ProviderFetchError("EJP_TABLE: Incomplete stage history row.")
        if row == rows[header]:
            continue
        history.append({"status": normalized(row[stage_column]), "status_date": row[date_column]})
    if not history or not any(stage["status"] for stage in history):
        raise ProviderFetchError("EJP_TABLE: Stage history table contains no valid stages.")
    return history[::-1]


def detail_title(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) >= 2 and normalized(cells[0].get_text(" ", strip=True)).casefold().rstrip(":") in (
            "title",
            "manuscript title",
        ):
            return normalized(cells[1].get_text(" ", strip=True))
    for heading in soup.select("h1, h2, h3, .manuscript-title, .ms_title"):
        text = normalized(heading.get_text(" ", strip=True))
        title = link_title(text, manuscript_number(text))
        if title:
            return title
    return ""


def _has_author_home(soup: BeautifulSoup) -> bool:
    has_logout = any(
        re.fullmatch(r"log\s*out", normalized(a.get_text(" ", strip=True)), re.I)
        for a in soup.select("a")
    )
    if not has_logout:
        return False
    if soup.select_one('input[name="password"], input[type="password"]'):
        return False
    if soup.select_one('tbody.desktop-section[role="author"], [role="author"]'):
        return True
    return bool(re.search(r"\b(?:Author Tasks|Live Manuscripts)\b", soup.get_text(" ", strip=True), re.I))


def _check_challenge_or_rejection(html: str, status_code: int, slug: str) -> None:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    if (
        status_code == 403
        or CHALLENGE_RE.search(text)
        or soup.select_one(
            'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], input[name*="captcha"], .g-recaptcha, .h-captcha, .cf-turnstile'
        )
    ):
        raise AuthenticationError(
            f"[{slug}] eJournalPress triggered interactive security/CAPTCHA challenge; "
            "use browser-session `manuscript-watch` or `Manuscript Desk` for portals behind Turnstile/CAPTCHA."
        )
    if REJECTED_RE.search(text):
        raise AuthenticationError(f"[{slug}] eJournalPress rejected credentials or account is locked.")


class EJournalPressProvider:
    name = "ejournalpress"
    description = "eJournalPress HTTPS session (*.msubmit.net and manuscripts.jama*.com)"

    def _open_session(
        self,
        account: TargetAccount,
        *,
        timeout: int = 20,
        retries: int = DEFAULT_RETRIES,
        retry_delay: float = RETRY_DELAY_SECONDS,
    ) -> tuple[requests.Session, str, str, str]:
        ejp_id, main_url = resolve_ejp_endpoint(account.journal)
        expected_host = (urlsplit(main_url).hostname or "").lower()
        home_url = f"{main_url}?form_type=home"

        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            session = requests.Session()
            session.headers.update(DEFAULT_HEADERS)
            try:
                landing = session.get(home_url, timeout=timeout)
                _check_challenge_or_rejection(landing.text, landing.status_code, ejp_id)
                landing.raise_for_status()
                if (urlsplit(landing.url).hostname or "").lower() != expected_host:
                    raise ProviderFetchError(f"[{ejp_id}] Redirected outside configured journal domain.")

                soup = BeautifulSoup(landing.text, "lxml")
                if _has_author_home(soup):
                    return session, landing.url, landing.text, ejp_id

                pw_input = soup.select_one('input[name="password"], input[type="password"]')
                form = pw_input.find_parent("form") if pw_input else soup.find("form")
                if not form or not pw_input:
                    raise AuthenticationError(f"[{ejp_id}] Could not locate eJournalPress login form.")

                payload: dict[str, str] = {}
                login_field = "login"
                pw_field = pw_input.get("name") or "password"
                found_login = False
                for inp in form.find_all("input"):
                    iname = inp.get("name")
                    if not iname:
                        continue
                    itype = (inp.get("type") or "text").lower()
                    if itype in ("hidden", "submit"):
                        if itype == "hidden" or not payload.get(iname):
                            payload[iname] = inp.get("value", "")
                    elif iname == "login" or (not found_login and itype in ("text", "email")):
                        login_field = iname
                        found_login = True

                payload[login_field] = account.username
                payload[pw_field] = account.secret
                action_url = urljoin(landing.url, form.get("action") or main_url)
                method = (form.get("method") or "post").lower()
                if method == "get":
                    resp = session.get(action_url, params=payload, headers={"Referer": landing.url}, timeout=timeout)
                else:
                    resp = session.post(action_url, data=payload, headers={"Referer": landing.url}, timeout=timeout)

                _check_challenge_or_rejection(resp.text, resp.status_code, ejp_id)
                resp.raise_for_status()
                if (urlsplit(resp.url).hostname or "").lower() != expected_host:
                    raise ProviderFetchError(f"[{ejp_id}] Login redirected outside configured journal domain.")

                post_soup = BeautifulSoup(resp.text, "lxml")
                if post_soup.select_one('input[name="password"], input[type="password"]'):
                    raise AuthenticationError(f"[{ejp_id}] eJournalPress rejected credentials.")
                if _has_author_home(post_soup):
                    return session, resp.url, resp.text, ejp_id

                home_resp = session.get(home_url, headers={"Referer": resp.url}, timeout=timeout)
                _check_challenge_or_rejection(home_resp.text, home_resp.status_code, ejp_id)
                home_resp.raise_for_status()
                if _has_author_home(BeautifulSoup(home_resp.text, "lxml")):
                    return session, home_resp.url, home_resp.text, ejp_id

                raise AuthenticationError(f"[{ejp_id}] Unexpected eJournalPress response after login.")
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < retries:
                    time.sleep(retry_delay)

        raise ProviderFetchError(f"[{ejp_id}] Network failure after {retries} attempts: {last_exc}")

    def _get_entry_page(
        self,
        session: requests.Session,
        entry: dict[str, Any],
        expected_host: str,
        ejp_id: str,
        timeout: int,
    ) -> tuple[str, str]:
        if not entry["href"]:
            raise ProviderFetchError(f"[{ejp_id}] EJP_INCOMPLETE: Entry link missing href.")
        destination = urljoin(entry["origin"], entry["href"])
        if (urlsplit(destination).hostname or "").lower() != expected_host:
            raise ProviderFetchError(f"[{ejp_id}] EJP_INCOMPLETE: Manuscript link left journal domain.")
        resp = session.get(destination, headers={"Referer": entry["origin"]}, timeout=timeout)
        _check_challenge_or_rejection(resp.text, resp.status_code, ejp_id)
        resp.raise_for_status()
        if (urlsplit(resp.url).hostname or "").lower() != expected_host:
            raise ProviderFetchError(f"[{ejp_id}] EJP_INCOMPLETE: Entry redirected outside journal domain.")
        return resp.url, resp.text

    def _history_from_entry(
        self,
        session: requests.Session,
        entry: dict[str, Any],
        expected_host: str,
        ejp_id: str,
        timeout: int,
    ) -> tuple[list[dict[str, str]], str, str]:
        page_url, html = self._get_entry_page(session, entry, expected_host, ejp_id, timeout)
        soup = BeautifulSoup(html, "lxml")
        title = detail_title(html)
        number = entry["number"] or manuscript_number(soup.get_text(" ", strip=True), page_url)
        if soup.select_one("table.dump_history_table"):
            return parse_stage_history(html), title, number

        links = soup.select("a")
        for label in ("current stage", "check status"):
            for index, link in enumerate(links):
                if normalized(link.get_text(" ", strip=True)).casefold() == label:
                    status_entry = link_entry(link, index, "a", page_url, entry["kind"])
                    status_url, status_html = self._get_entry_page(
                        session, status_entry, expected_host, ejp_id, timeout
                    )
                    status_soup = BeautifulSoup(status_html, "lxml")
                    if status_soup.select_one("table.dump_history_table"):
                        number = number or manuscript_number(
                            status_soup.get_text(" ", strip=True), status_url
                        )
                        return (
                            parse_stage_history(status_html),
                            title or detail_title(status_html),
                            number,
                        )
        return [], title, number

    def verify_auth(self, account: TargetAccount, *, timeout: int = 20) -> None:
        self._open_session(account, timeout=timeout, retries=2, retry_delay=2.0)

    def fetch_submissions(
        self, account: TargetAccount, *, timeout: int = 20
    ) -> list[ManuscriptSnapshot]:
        session, home_url, home_html, ejp_id = self._open_session(account, timeout=timeout)
        expected_host = (urlsplit(home_url).hostname or "").lower()
        try:
            folders, candidates = parse_home_entries(home_html, home_url)
            for folder in folders:
                if folder["count"] == 0:
                    continue
                cur_url, cur_html = self._get_entry_page(
                    session, folder, expected_host, ejp_id, timeout
                )
                seen: set[str] = set()
                visited: set[str] = set()
                while True:
                    if cur_url in visited:
                        raise ProviderFetchError(f"[{ejp_id}] EJP_INCOMPLETE: Folder pagination loop.")
                    visited.add(cur_url)
                    entries = parse_folder_entries(cur_html, cur_url, folder["kind"])
                    for entry in entries:
                        if entry["key"] in seen:
                            raise ProviderFetchError(
                                f"[{ejp_id}] EJP_INCOMPLETE: Duplicate manuscript across folder pages."
                            )
                        seen.add(entry["key"])
                    candidates.extend(entries)
                    if len(seen) == folder["count"]:
                        break
                    if len(seen) > folder["count"]:
                        raise ProviderFetchError(
                            f"[{ejp_id}] EJP_INCOMPLETE: Observed count exceeded folder header count."
                        )
                    links = BeautifulSoup(cur_html, "lxml").select("a")
                    next_links = [
                        (idx, link)
                        for idx, link in enumerate(links)
                        if normalized(link.get_text(" ", strip=True)).casefold() in ("next", "next page")
                    ]
                    if len(next_links) != 1:
                        raise ProviderFetchError(
                            f"[{ejp_id}] EJP_INCOMPLETE: Folder unique manuscript count mismatch."
                        )
                    idx, link = next_links[0]
                    cur_url, cur_html = self._get_entry_page(
                        session,
                        link_entry(link, idx, "a", cur_url, folder["kind"]),
                        expected_host,
                        ejp_id,
                        timeout,
                    )

            groups: dict[str, list[dict[str, Any]]] = {}
            for candidate in candidates:
                groups.setdefault(candidate["key"], []).append(candidate)

            observed: dict[str, dict[str, Any]] = {}
            title_ranks: dict[str, int] = {}
            priorities = {"action_ejp": 0, "direct_manuscript": 1, "live": 2, "post_decision": 3}

            for paths in sorted(
                groups.values(), key=lambda items: all(item["kind"] == "action_ejp" for item in items)
            ):
                preferred = min(paths, key=lambda item: priorities[item["kind"]])
                title_source = max(paths, key=lambda item: item["title_rank"] if item["title"] else 0)
                title = title_source["title"]
                title_rank = title_source["title_rank"] if title else 0
                number = preferred["number"]
                stage_history: list[dict[str, str]] = []

                for entry in sorted(paths, key=lambda item: priorities[item["kind"]]):
                    stage_history, found_title, found_number = self._history_from_entry(
                        session, entry, expected_host, ejp_id, timeout
                    )
                    number = number or found_number
                    if found_title and (not title or title_source["title_rank"] < 2):
                        title = found_title
                        title_rank = 2
                    if stage_history:
                        break
                if not stage_history and number in observed:
                    stage_history = observed[number]["stage_history"]
                if not stage_history or not number:
                    raise ProviderFetchError(
                        f"[{ejp_id}] EJP_TABLE: Unable to read full stage history or manuscript number."
                    )

                current = {
                    "manuscript_number": number,
                    "title": title or number,
                    "status": stage_history[-1]["status"],
                    "status_date": stage_history[-1]["status_date"],
                    "initial_date_submitted": stage_history[0]["status_date"],
                    "category": ENTRY_NAMES.get(preferred["kind"], preferred["kind"]),
                    "entry_kind": preferred["kind"],
                    "stage_history": stage_history,
                }
                if number in observed:
                    prior = observed[number]
                    if prior["stage_history"] != stage_history:
                        raise ProviderFetchError(
                            f"[{ejp_id}] EJP_INCOMPLETE: Conflicting stage history across entries for {number}."
                        )
                    if priorities[current["entry_kind"]] < priorities[prior["entry_kind"]]:
                        prior["entry_kind"] = current["entry_kind"]
                        prior["category"] = current["category"]
                    if title_rank > title_ranks[number] or prior["title"] == number:
                        prior["title"] = current["title"]
                        title_ranks[number] = title_rank
                else:
                    observed[number] = current
                    title_ranks[number] = title_rank

            return [
                ManuscriptSnapshot(
                    provider="ejournalpress",
                    journal=ejp_id,
                    journal_name=account.name,
                    username=account.username,
                    category=row["category"],
                    manuscript_number=row["manuscript_number"],
                    title=row["title"],
                    initial_date_submitted=row["initial_date_submitted"],
                    status_date=row["status_date"],
                    status=row["status"],
                    stage_history=tuple((s["status"], s["status_date"]) for s in row["stage_history"]),
                )
                for row in observed.values()
            ]
        except requests.RequestException as exc:
            raise ProviderFetchError(
                f"[{ejp_id}] Network error while reading eJournalPress submissions: {exc}"
            ) from exc
