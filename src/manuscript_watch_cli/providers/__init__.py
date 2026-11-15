"""Built-in submission providers."""

from manuscript_watch_cli.providers.base import (
    SubmissionProvider,
    get_provider,
    list_providers,
    register_provider,
)
from manuscript_watch_cli.providers.editorial_manager import EditorialManagerProvider
from manuscript_watch_cli.providers.ejournalpress import EJournalPressProvider

register_provider(EditorialManagerProvider())
register_provider(EJournalPressProvider())

__all__ = [
    "EJournalPressProvider",
    "EditorialManagerProvider",
    "SubmissionProvider",
    "get_provider",
    "list_providers",
    "register_provider",
]
