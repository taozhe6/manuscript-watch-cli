"""Pure-HTTP Editorial Manager provider."""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import requests

from manuscript_watch_cli.models import (
    AuthenticationError,
    ManuscriptSnapshot,
    ProviderFetchError,
    TargetAccount,
)

BASE_URL = "https://www.editorialmanager.com"
DEFAULT_RETRIES = 3
RETRY_DELAY_SECONDS = 4.0

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def build_full_page_url(url: str) -> str:
    parsed = urlsplit(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["size1"] = ["500"]
    query["size2"] = ["500"]
    return urlunsplit(parsed._replace(query=urlencode(query, doseq=True)))


def _extract_hidden_inputs(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form", attrs={"name": "loginForm"}) or soup.find("form")
    fields: dict[str, str] = {}
    if not form:
        return fields
    for inp in form.find_all("input"):
        name = inp.get("name")
        input_type = (inp.get("type") or "").lower()
        if name and input_type in ("hidden", "text", "password"):
            fields[name] = inp.get("value", "")
    return fields


def _is_login_screen(soup: BeautifulSoup) -> bool:
    if soup.select_one('input[type="password"]'):
        return True
    return soup.find("form", attrs={"name": "loginForm"}) is not None


def _find_column(row: dict[str, str], candidates: list[str]) -> str:
    normalized = [
        (re.sub(r"[^a-z0-9]", "", key.casefold()), value.strip())
        for key, value in row.items()
    ]
    for token in candidates:
        for norm_key, val in normalized:
            if norm_key == token:
                return val
    for token in candidates:
        for norm_key, val in normalized:
            if "date" in norm_key and "date" not in token:
                continue
            if token in norm_key:
                return val
    return ""


def parse_author_menu(html: str, journal_base_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    if _is_login_screen(soup):
        raise AuthenticationError("AuthorMainMenu returned login form (session expired or unauthorized).")

    categories: list[dict[str, Any]] = []
    for link in soup.select("fieldset.datatablecontainer div.main_menu_item a[href]"):
        count_span = link.find_next_sibling("span", class_="count")
        if not count_span:
            continue
        match = re.search(r"\(([\d,]+)\)", count_span.get_text(strip=True))
        if not match:
            continue
        count = int(match.group(1).replace(",", ""))
        label = " ".join(link.get_text(" ", strip=True).split())
        categories.append({
            "label": label,
            "count": count,
            "url": urljoin(journal_base_url, link["href"]),
        })

    if not categories and not soup.select("fieldset.datatablecontainer"):
        raise ProviderFetchError("Unrecognized AuthorMainMenu layout.")
    return categories


def parse_submission_table(
    html: str,
    *,
    account: TargetAccount,
    category_label: str,
) -> list[ManuscriptSnapshot]:
    soup = BeautifulSoup(html, "lxml")
    if _is_login_screen(soup):
        raise AuthenticationError("Session expired while reading submission table.")

    table = soup.find("table", id="datatable") or soup.find("table", id="searchresults")
    if not table:
        return []

    thead = table.find("thead")
    tbody = table.find("tbody")
    if not thead or not tbody:
        return []

    headers = [th.get_text(" ", strip=True) for th in thead.find_all("th")]
    snapshots: list[ManuscriptSnapshot] = []

    for row in tbody.find_all("tr"):
        cells = [child for child in row.children if getattr(child, "name", None) == "td"]
        if len(cells) != len(headers):
            continue

        col_map = {
            headers[idx]: " ".join(cells[idx].get_text(" ", strip=True).split())
            for idx in range(len(headers))
        }

        extracted_ms = ""
        extracted_guid = ""
        docid = ""
        for link in cells[0].find_all("a", href=True):
            href = link["href"]
            m_pdf = re.search(
                r"""viewAuthorPDFs\((\d+),\s*['"]([^'"]+)['"],\s*\d+,\s*['"]?\{?([^}'"]+)\}?['"]?\)""",
                href,
            )
            if m_pdf:
                docid = m_pdf.group(1)
                extracted_ms = m_pdf.group(2)
                extracted_guid = m_pdf.group(3)

            if not extracted_guid:
                m_letter = re.search(r"""popupLetter\(\d+,\s*['"]?\{?([^}'"]+)\}?['"]?\)""", href)
                if m_letter:
                    extracted_guid = m_letter.group(1)
            if not extracted_ms:
                m_sub = re.search(r"sub_num=([^&'\"]+)", href)
                if m_sub:
                    extracted_ms = m_sub.group(1)
            if not extracted_guid:
                m_msid = re.search(r"msid=\{([^}]+)\}", href)
                if m_msid:
                    extracted_guid = m_msid.group(1)
            if not docid:
                m_doc = re.search(r"docid=(\d+)", href)
                if m_doc:
                    docid = m_doc.group(1)

        ms_number = extracted_ms.strip() or _find_column(col_map, ["manuscriptnumber"])
        title = _find_column(col_map, ["title"]) or "Untitled"
        status = (
            _find_column(col_map, ["currentstatus", "status", "finaldisposition"])
            or category_label
        )
        status_date = _find_column(col_map, ["statusdate"])
        initial_submitted = _find_column(
            col_map, ["submissionbegan", "initialdate", "datesubmitted"]
        )

        snapshots.append(
            ManuscriptSnapshot(
                provider="editorial_manager",
                journal=account.journal_slug,
                journal_name=account.name,
                username=account.username,
                category=category_label,
                manuscript_number=ms_number,
                title=title,
                initial_date_submitted=initial_submitted,
                status_date=status_date,
                status=status,
                docid=docid,
                guid=extracted_guid,
            )
        )

    return snapshots


class EditorialManagerProvider:
    name = "editorial_manager"
    description = "Editorial Manager native HTTPS session (editorialmanager.com/<journal>/)"

    def _open_session(
        self,
        account: TargetAccount,
        *,
        timeout: int = 20,
        retries: int = DEFAULT_RETRIES,
        retry_delay: float = RETRY_DELAY_SECONDS,
    ) -> tuple[requests.Session, str]:
        base_url = f"{BASE_URL}/{account.journal_slug}/"
        default_url = urljoin(base_url, "default.aspx")
        login_page_url = urljoin(base_url, "login.asp")
        login_action_url = urljoin(base_url, "LoginAction.ashx")

        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            session = requests.Session()
            session.headers.update(DEFAULT_HEADERS)
            try:
                session.get(default_url, timeout=timeout)
                login_page = session.get(
                    login_page_url,
                    headers={"Referer": default_url},
                    timeout=timeout,
                )
                login_page.raise_for_status()

                payload = _extract_hidden_inputs(login_page.text)
                payload["username"] = account.username
                payload["password"] = account.secret
                payload["role"] = "author"

                resp = session.post(
                    login_action_url,
                    data=payload,
                    headers={"Origin": BASE_URL, "Referer": login_page_url},
                    timeout=timeout,
                )
                resp.raise_for_status()
                body = resp.text

                if "acctLockedForm" in body or "AccountLocked" in body:
                    raise AuthenticationError(
                        f"[{account.journal_slug}] Editorial Manager account is temporarily locked; "
                        "aborting without retry."
                    )
                if "loginError" in body or "Invalid login" in body or "Login failed" in body:
                    raise AuthenticationError(
                        f"[{account.journal_slug}] Credentials rejected (journals requiring external "
                        "SSO/ORCID-only login are not supported by pure HTTP login)."
                    )
                if any(
                    marker in body
                    for marker in (
                        "AuthorMainMenu.aspx",
                        "ReviewerMainMenu.aspx",
                        "EditorMainMenu.aspx",
                        "PublisherMainMenu.aspx",
                    )
                ):
                    return session, base_url

                raise AuthenticationError(
                    f"[{account.journal_slug}] Unexpected login response (no MainMenu redirect)."
                )
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < retries:
                    time.sleep(retry_delay)

        raise ProviderFetchError(
            f"[{account.journal_slug}] Network failure after {retries} attempts: {last_exc}"
        )

    def verify_auth(self, account: TargetAccount, *, timeout: int = 20) -> None:
        self._open_session(account, timeout=timeout, retries=2, retry_delay=2.0)

    def fetch_submissions(
        self, account: TargetAccount, *, timeout: int = 20
    ) -> list[ManuscriptSnapshot]:
        session, base_url = self._open_session(account, timeout=timeout)
        menu_url = urljoin(base_url, "AuthorMainMenu.aspx")
        try:
            menu_resp = session.get(
                menu_url,
                headers={"Referer": urljoin(base_url, "default2.aspx")},
                timeout=timeout,
            )
            menu_resp.raise_for_status()
            categories = parse_author_menu(menu_resp.text, base_url)

            all_items: list[ManuscriptSnapshot] = []
            for cat in categories:
                if cat["count"] <= 0:
                    continue
                resp = session.get(cat["url"], headers={"Referer": menu_url}, timeout=timeout)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")
                if soup.find("select", attrs={"name": "size1"}):
                    resp = session.get(
                        build_full_page_url(cat["url"]),
                        headers={"Referer": cat["url"]},
                        timeout=timeout,
                    )
                    resp.raise_for_status()
                all_items.extend(
                    parse_submission_table(resp.text, account=account, category_label=cat["label"])
                )
            return all_items
        except requests.RequestException as exc:
            raise ProviderFetchError(
                f"[{account.journal_slug}] Network error while reading submissions: {exc}"
            ) from exc
