"""Built-in submission providers."""

from manuscript_watch_cli.providers.base import (
    SubmissionProvider,
    get_provider,
    list_providers,
    register_provider,
)
from manuscript_watch_cli.providers.editorial_manager import EditorialManagerProvider

register_provider(EditorialManagerProvider())

__all__ = [
    "EditorialManagerProvider",
    "SubmissionProvider",
    "get_provider",
    "list_providers",
    "register_provider",
]
