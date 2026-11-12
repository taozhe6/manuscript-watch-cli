"""Pluggable provider protocol, registry, and Python entry-point plugin loader."""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Protocol

from manuscript_watch_cli.models import ConfigError, ManuscriptSnapshot, TargetAccount

ENTRYPOINT_GROUP = "manuscript_watch_cli.providers"


class SubmissionProvider(Protocol):
    name: str
    description: str

    def verify_auth(self, account: TargetAccount, *, timeout: int = 20) -> None:
        """Validate credentials against upstream without fetching full tables."""
        ...

    def fetch_submissions(
        self, account: TargetAccount, *, timeout: int = 20
    ) -> list[ManuscriptSnapshot]:
        """Fetch all current manuscript snapshots for the given account."""
        ...


_REGISTRY: dict[str, SubmissionProvider] = {}
_PLUGINS_LOADED = False


def register_provider(provider: SubmissionProvider) -> None:
    _REGISTRY[provider.name] = provider


def _load_entrypoint_plugins() -> None:
    global _PLUGINS_LOADED
    if _PLUGINS_LOADED:
        return
    _PLUGINS_LOADED = True
    for ep in entry_points(group=ENTRYPOINT_GROUP):
        if ep.name in _REGISTRY:
            continue
        loaded = ep.load()
        instance = loaded() if isinstance(loaded, type) else loaded
        register_provider(instance)


def get_provider(name: str) -> SubmissionProvider:
    _load_entrypoint_plugins()
    key = name.strip().lower().replace("-", "_")
    if key not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY))
        raise ConfigError(f"Unknown provider {name!r}. Available providers: {available}")
    return _REGISTRY[key]


def list_providers() -> list[SubmissionProvider]:
    _load_entrypoint_plugins()
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]
