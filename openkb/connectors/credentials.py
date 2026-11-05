"""Connector credentials in CLI order.

Process environment, then ``<kb>/.env``, then ``~/.config/openkb/.env``.
This is intentionally not :func:`openkb.config.resolve_credential_bundle`,
which lets the KB file win over the process environment.
"""

from __future__ import annotations

import os
from pathlib import Path

from openkb.config import GLOBAL_CONFIG_DIR

S3_ACCESS_KEY_ID = "OPENKB_S3_ACCESS_KEY_ID"
S3_SECRET_ACCESS_KEY = "OPENKB_S3_SECRET_ACCESS_KEY"
GDRIVE_CLIENT_ID = "OPENKB_GDRIVE_CLIENT_ID"
GDRIVE_CLIENT_SECRET = "OPENKB_GDRIVE_CLIENT_SECRET"
GDRIVE_REFRESH_TOKEN = "OPENKB_GDRIVE_REFRESH_TOKEN"
ONEDRIVE_CLIENT_ID = "OPENKB_ONEDRIVE_CLIENT_ID"
ONEDRIVE_CLIENT_SECRET = "OPENKB_ONEDRIVE_CLIENT_SECRET"
ONEDRIVE_REFRESH_TOKEN = "OPENKB_ONEDRIVE_REFRESH_TOKEN"

CONNECTOR_ENV_KEYS = (
    S3_ACCESS_KEY_ID,
    S3_SECRET_ACCESS_KEY,
    GDRIVE_CLIENT_ID,
    GDRIVE_CLIENT_SECRET,
    GDRIVE_REFRESH_TOKEN,
    ONEDRIVE_CLIENT_ID,
    ONEDRIVE_CLIENT_SECRET,
    ONEDRIVE_REFRESH_TOKEN,
)


def _dotenv_get(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    from dotenv import dotenv_values

    value = dotenv_values(path).get(key)
    if value is None or value == "":
        return None
    return value


def resolve_connector_credential(kb_dir: Path, key: str) -> str | None:
    """Return the first non-empty value for ``key`` in CLI order."""
    process = os.environ.get(key)
    if process:
        return process
    kb_value = _dotenv_get(kb_dir / ".env", key)
    if kb_value:
        return kb_value
    return _dotenv_get(GLOBAL_CONFIG_DIR / ".env", key)


def s3_credentials(kb_dir: Path) -> tuple[str, str] | None:
    """Return the S3 key pair, or ``None`` when either key is absent."""
    access = resolve_connector_credential(kb_dir, S3_ACCESS_KEY_ID)
    secret = resolve_connector_credential(kb_dir, S3_SECRET_ACCESS_KEY)
    if not access or not secret:
        return None
    return access, secret


def resolved_secrets(kb_dir: Path) -> list[str]:
    """Credential values currently visible, used to keep them out of state files."""
    found: list[str] = []
    for key in CONNECTOR_ENV_KEYS:
        value = resolve_connector_credential(kb_dir, key)
        if value:
            found.append(value)
    return found
