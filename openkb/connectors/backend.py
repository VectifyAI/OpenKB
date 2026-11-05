"""List-and-download interface shared by remote backends."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class TransientError(Exception):
    """A retryable HTTP failure (throttle, timeout, 5xx)."""


class ProviderError(Exception):
    """A failure of one provider. Other providers still run."""


@dataclass(frozen=True)
class RemoteObject:
    """One remote file selected for ingest.

    ``locator`` is the provider's download handle (an S3 key is already in
    ``identity``; Drive stores a file id here).
    """

    identity: str
    name: str
    locator: str = ""


class ObjectBackend(Protocol):
    """List objects under a binding and download one of them."""

    def list_objects(self) -> list[RemoteObject]:
        """Return every object under the binding."""

    def download(self, obj: RemoteObject, dest: Path) -> None:
        """Write ``obj`` to ``dest``."""
