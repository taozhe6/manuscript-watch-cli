"""Pure-HTTP Editorial Manager session login and manuscript table parser."""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import requests

BASE_URL = "https://www.editorialmanager.com"
DEFAULT_TIMEOUT = 20
DEFAULT_RETRIES = 3
RETRY_DELAY_SECONDS = 4

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class EditorialManagerError(RuntimeError):
    """Raised when login or page parsing fails."""


class AuthenticationError(EditorialManagerError):
    """Raised when credentials are rejected or the account is temporarily locked."""


@dataclass(frozen=True)
class AccountConfig:
    journal_short_name: str
    journal_full_name: str
    username: str
    password: str

    @property
    def journal_slug(self) -> str:
        slug = self.journal_short_name.strip().strip("/").lower()
        if not slug or not slug.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"Invalid journal_short_name: {self.journal_short_name!r}")
        return slug

    @property
    def journal_base_url(self) -> str:
        return f"{BASE_URL}/{self.journal_slug}/"


def _extract_login_form_fields(html: str) -> dict[str, str]:
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


def _looks_like_login_page(soup: BeautifulSoup) -> bool:
    if soup.select_one('input[type="password"]'):
        return True
    return soup.find("form", attrs={"name": "loginForm"}) is not None


def login_author_session(
    account: AccountConfig,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    retry_delay: float = RETRY_DELAY_SECONDS,
) -> requests.Session:
    """Authenticate against Editorial Manager native login and return an active session."""
    base_url = account.journal_base_url
    default_url = urljoin(base_url, "default.aspx")
    login_page_url = urljoin(base_url, "login.asp")
    login_action_url = urljoin(base_url, "LoginAction.ashx")

    last_net_error: Exception | None = None
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

            payload = _extract_login_form_fields(login_page.text)
            payload["username"] = account.username
            payload["password"] = account.password
            payload["role"] = "author"

            resp = session.post(
                login_action_url,
                data=payload,
                headers={
                    "Origin": BASE_URL,
                    "Referer": login_page_url,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            body = resp.text

            if "acctLockedForm" in body or "AccountLocked" in body:
                raise AuthenticationError(
                    f"[{account.journal_slug}] Account is temporarily locked on Editorial Manager; "
                    "aborting without retry."
                )
            if "loginError" in body or "Invalid login" in body or "Login failed" in body:
                raise AuthenticationError(
                    f"[{account.journal_slug}] Login rejected; check username, password, or journal code "
                    "(note: journals requiring SSO/ORCID-only login are not supported by pure HTTP login)."
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
                return session

            raise AuthenticationError(
                f"[{account.journal_slug}] Unexpected login response (no MainMenu redirect found)."
            )
        except requests.RequestException as exc:
            last_net_error = exc
            if attempt < retries:
                time.sleep(retry_delay)

    raise EditorialManagerError(
        f"[{account.journal_slug}] Network error after {retries} attempts: {last_net_error}"
    )


def build_full_page_url(url: str) -> str:
    parsed = urlsplit(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["size1"] = ["500"]
    query["size2"] = ["500"]
    return urlunsplit(parsed._replace(query=urlencode(query, doseq=True)))


def parse_manuscript_table(html: str, category_label: str = "") -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    if _looks_like_login_page(soup):
        raise AuthenticationError("Session expired while loading manuscript table.")

    table = soup.find("table", id="datatable") or soup.find("table", id="searchresults")
    if not table:
        return []

    thead = table.find("thead")
    tbody = table.find("tbody")
    if not thead or not tbody:
        return []

    headers = [th.get_text(" ", strip=True) for th in thead.find_all("th")]
    records: list[dict[str, Any]] = []

    for row in tbody.find_all("tr"):
        cells = [child for child in row.children if getattr(child, "name", None) == "td"]
        if len(cells) != len(headers):
            continue

        item: dict[str, Any] = {
            headers[idx]: " ".join(cells[idx].get_text(" ", strip=True).split())
            for idx in range(len(headers))
        }

        extracted_ms_number = ""
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
                extracted_ms_number = m_pdf.group(2)
                extracted_guid = m_pdf.group(3)

            if not extracted_guid:
                m_letter = re.search(r"""popupLetter\(\d+,\s*['"]?\{?([^}'"]+)\}?['"]?\)""", href)
                if m_letter:
                    extracted_guid = m_letter.group(1)

            if not extracted_ms_number:
                m_sub = re.search(r"sub_num=([^&'\"]+)", href)
                if m_sub:
                    extracted_ms_number = m_sub.group(1)

            if not extracted_guid:
                m_msid = re.search(r"msid=\{([^}]+)\}", href)
                if m_msid:
                    extracted_guid = m_msid.group(1)

            if not docid:
                m_doc = re.search(r"docid=(\d+)", href)
                if m_doc:
                    docid = m_doc.group(1)

        item["_category"] = category_label
        item["_extracted_ms_number"] = extracted_ms_number
        item["_extracted_guid"] = extracted_guid
        item["_docid"] = docid
        records.append(item)

    return records


def parse_author_menu_categories(html: str, journal_base_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    if _looks_like_login_page(soup):
        raise AuthenticationError("AuthorMainMenu returned the login form (session invalid).")

    categories: list[dict[str, Any]] = []
    for link in soup.select("fieldset.datatablecontainer div.main_menu_item a[href]"):
        count_span = link.find_next_sibling("span", class_="count")
        if not count_span:
            continue
        m = re.search(r"\(([\d,]+)\)", count_span.get_text(strip=True))
        if not m:
            continue
        count = int(m.group(1).replace(",", ""))
        label = " ".join(link.get_text(" ", strip=True).split())
        categories.append({
            "label": label,
            "count": count,
            "url": urljoin(journal_base_url, link["href"]),
        })

    if not categories and not soup.select("fieldset.datatablecontainer"):
        raise EditorialManagerError("Unrecognized AuthorMainMenu HTML layout.")
    return categories


def fetch_all_manuscripts(
    session: requests.Session,
    account: AccountConfig,
    *,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    base_url = account.journal_base_url
    menu_url = urljoin(base_url, "AuthorMainMenu.aspx")
    menu_resp = session.get(
        menu_url,
        headers={"Referer": urljoin(base_url, "default2.aspx")},
        timeout=timeout,
    )
    menu_resp.raise_for_status()

    categories = parse_author_menu_categories(menu_resp.text, base_url)
    results: list[dict[str, Any]] = []

    for cat in categories:
        if cat["count"] <= 0:
            continue
        resp = session.get(
            cat["url"],
            headers={"Referer": menu_url},
            timeout=timeout,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        if soup.find("select", attrs={"name": "size1"}):
            resp = session.get(
                build_full_page_url(cat["url"]),
                headers={"Referer": cat["url"]},
                timeout=timeout,
            )
            resp.raise_for_status()
        rows = parse_manuscript_table(resp.text, category_label=cat["label"])
        results.extend(rows)

    return results
