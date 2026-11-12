"""Domain models, semantic exit codes, and typed exceptions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import IntEnum
import hashlib
import re
from typing import Any


class ExitCode(IntEnum):
    OK = 0
    USAGE_OR_CONFIG = 1
    CHANGED = 2
    AUTH_FAILED = 3
    NETWORK_OR_PARSE = 4


class MWatchError(RuntimeError):
    """Base error with an associated CLI exit code."""

    exit_code: ExitCode = ExitCode.NETWORK_OR_PARSE


class ConfigError(MWatchError):
    exit_code = ExitCode.USAGE_OR_CONFIG


class AuthenticationError(MWatchError):
    exit_code = ExitCode.AUTH_FAILED


class ProviderFetchError(MWatchError):
    exit_code = ExitCode.NETWORK_OR_PARSE


def sanitize_token(text: str, max_length: int = 80) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "", text)
    cleaned = re.sub(r"\s+", "-", cleaned.strip())
    return cleaned[:max_length] or "untitled"


@dataclass(frozen=True)
class TargetAccount:
    journal: str
    name: str
    username: str
    secret: str
    provider: str = "editorial_manager"
    account_id: str = ""
    enabled: bool = True

    @property
    def journal_slug(self) -> str:
        slug = self.journal.strip().strip("/").lower()
        if not slug or not slug.replace("-", "").replace("_", "").isalnum():
            raise ConfigError(f"Invalid journal code: {self.journal!r}")
        return slug

    @property
    def effective_id(self) -> str:
        return self.account_id.strip() or f"{self.journal_slug}:{self.username}"

    def public_summary(self) -> dict[str, Any]:
        return {
            "id": self.effective_id,
            "provider": self.provider,
            "journal": self.journal_slug,
            "name": self.name,
            "username": self.username,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class ManuscriptSnapshot:
    provider: str
    journal: str
    journal_name: str
    username: str
    category: str
    manuscript_number: str
    title: str
    initial_date_submitted: str
    status_date: str
    status: str
    docid: str = ""
    guid: str = ""

    @property
    def stable_id(self) -> str:
        if self.manuscript_number.strip():
            return sanitize_token(self.manuscript_number.strip(), max_length=48)
        if self.docid.strip():
            return f"docid_{sanitize_token(self.docid.strip(), max_length=24)}"
        if self.guid.strip():
            return f"guid_{sanitize_token(self.guid.strip(), max_length=24)}"
        digest = hashlib.sha1(self.title.encode("utf-8")).hexdigest()[:10]
        return f"hash_{digest}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["stable_id"] = self.stable_id
        return data


@dataclass(frozen=True)
class TransitionResult:
    change: str  # 'created' | 'updated' | 'unchanged'
    snapshot: ManuscriptSnapshot
    previous_status: str
    previous_status_date: str
    checked_at: str
    history_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "change": self.change,
            "checked_at": self.checked_at,
            "previous_status": self.previous_status,
            "previous_status_date": self.previous_status_date,
            "history_path": self.history_path,
            **self.snapshot.to_dict(),
        }
