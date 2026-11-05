"""Google Drive list and download.

The API client is imported only when a real backend is built. Tests pass a
fake client and never import the SDK.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import NoReturn, Protocol

from openkb.connectors.backend import ProviderError, RemoteObject, TransientError
from openkb.connectors.credentials import GDRIVE_REFRESH_TOKEN, gdrive_client, gdrive_refresh_token
from openkb.connectors.model import GDriveBinding, gdrive_object_identity, parse_gdrive_binding
from openkb.connectors.oauth import (
    GDRIVE_REDIRECT_URI,
    AuthRejected,
    refresh_access_token,
    save_refresh_token,
    sign_in,
)
from openkb.connectors.store import upsert_binding
from openkb.connectors.sync import recover_auth

GDRIVE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GDRIVE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GDRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}

Announce = Callable[[str], None]
RefreshFn = Callable[[str], tuple[str, str | None]]
SignInFn = Callable[[], object]


class DriveClient(Protocol):
    """The slice of the Drive API this backend uses."""

    def list_folder(self, folder_id: str) -> list[dict[str, object]]:
        """Return children. Each item has ``id``, ``name``, and ``folder``."""

    def download(self, file_id: str, dest: Path) -> None:
        """Write one file to ``dest``."""


def _load_google():
    try:
        from google.oauth2.credentials import Credentials  # type: ignore[import-not-found]
        from googleapiclient.discovery import build  # type: ignore[import-not-found]
        from googleapiclient.http import MediaIoBaseDownload  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ProviderError(
            'Google Drive support is not installed. Install it with: pip install "openkb[gdrive]"'
        ) from exc
    return Credentials, build, MediaIoBaseDownload


def _translate(exc: Exception) -> NoReturn:
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status in _TRANSIENT_STATUS:
        raise TransientError(f"Drive transient error (HTTP {status})") from exc
    name = type(exc).__name__
    if "Timeout" in name or "Connection" in name:
        raise TransientError(f"Drive transient error ({name})") from exc
    raise ProviderError(f"Drive error ({name})") from exc


class _GoogleClient:
    def __init__(self, access_token: str) -> None:
        credentials_cls, build, self._download_cls = _load_google()
        credentials = credentials_cls(token=access_token)
        self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)

    def list_folder(self, folder_id: str) -> list[dict[str, object]]:
        found: list[dict[str, object]] = []
        page_token: str | None = None
        while True:
            try:
                response = (
                    self._service.files()
                    .list(
                        q=f"'{folder_id}' in parents and trashed = false",
                        fields="nextPageToken, files(id, name, mimeType)",
                        pageSize=1000,
                        pageToken=page_token,
                        supportsAllDrives=False,
                    )
                    .execute()
                )
            except Exception as exc:
                if isinstance(exc, (ProviderError, TransientError)):
                    raise
                _translate(exc)
            for item in response.get("files") or []:
                name = item.get("name")
                file_id = item.get("id")
                if not isinstance(name, str) or not isinstance(file_id, str):
                    continue
                found.append(
                    {
                        "id": file_id,
                        "name": name,
                        "folder": item.get("mimeType") == _FOLDER_MIME,
                    }
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                return found

    def download(self, file_id: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        request = self._service.files().get_media(fileId=file_id)
        with dest.open("wb") as handle:
            downloader = self._download_cls(handle, request)
            done = False
            while not done:
                try:
                    _status, done = downloader.next_chunk()
                except Exception as exc:
                    if isinstance(exc, (ProviderError, TransientError)):
                        raise
                    _translate(exc)


def _walk(client: DriveClient, root_id: str, folder_id: str, relative: str) -> list[RemoteObject]:
    found: list[RemoteObject] = []
    for item in client.list_folder(folder_id):
        name = item.get("name")
        file_id = item.get("id")
        if not isinstance(name, str) or not name or not isinstance(file_id, str):
            continue
        path = name if not relative else f"{relative}/{name}"
        if item.get("folder") is True:
            found.extend(_walk(client, root_id, file_id, path))
            continue
        found.append(
            RemoteObject(
                identity=gdrive_object_identity(root_id, path),
                name=name,
                locator=file_id,
            )
        )
    return found


class GDriveBackend:
    """List a folder recursively and download one file."""

    def __init__(
        self, binding: GDriveBinding, access_token: str, *, client: DriveClient | None = None
    ):
        self._binding = binding
        self._client = client if client is not None else _GoogleClient(access_token)

    def list_objects(self) -> list[RemoteObject]:
        try:
            return _walk(self._client, self._binding.folder_id, self._binding.folder_id, "")
        except Exception as exc:
            if isinstance(exc, (ProviderError, TransientError)):
                raise
            _translate(exc)

    def download(self, obj: RemoteObject, dest: Path) -> None:
        if not obj.locator:
            raise ProviderError(f"Drive file id is missing for {obj.identity}.")
        try:
            self._client.download(obj.locator, dest)
        except Exception as exc:
            if isinstance(exc, (ProviderError, TransientError)):
                raise
            _translate(exc)


def run_gdrive_sign_in(kb_dir: Path, *, announce: Announce | None = None) -> str:
    """Browser sign-in. Returns the refresh token and does not write it."""
    client = gdrive_client(kb_dir)
    if client is None:
        raise ProviderError(_missing_client_message())
    client_id, secret = client
    return sign_in(
        authorize_url=GDRIVE_AUTHORIZE_URL,
        token_url=GDRIVE_TOKEN_URL,
        client_id=client_id,
        client_secret=secret,
        scopes=GDRIVE_SCOPE,
        redirect_uri=GDRIVE_REDIRECT_URI,
        extra_params={"access_type": "offline", "prompt": "consent"},
        announce=announce,
    )


def _missing_client_message() -> str:
    return (
        "Google Drive credentials are missing. Set OPENKB_GDRIVE_CLIENT_ID and "
        "OPENKB_GDRIVE_CLIENT_SECRET in the environment or <kb>/.env."
    )


def _default_refresh(kb_dir: Path, refresh_token: str) -> tuple[str, str | None]:
    client = gdrive_client(kb_dir)
    if client is None:
        raise ProviderError(_missing_client_message())
    client_id, secret = client
    return refresh_access_token(
        token_url=GDRIVE_TOKEN_URL,
        client_id=client_id,
        client_secret=secret,
        refresh_token=refresh_token,
        scopes=GDRIVE_SCOPE,
    )


def access_token_for(
    kb_dir: Path,
    *,
    non_interactive: bool,
    sign_in_hook: SignInFn | None = None,
    refresh: RefreshFn | None = None,
    announce: Announce | None = None,
) -> str:
    """Return an access token, signing in when the refresh token is missing or rejected.

    ``--non-interactive`` never calls the sign-in hook.
    """
    if gdrive_client(kb_dir) is None:
        raise ProviderError(_missing_client_message())
    refresh_fn = refresh or (lambda token: _default_refresh(kb_dir, token))

    def browser_sign_in() -> object:
        if sign_in_hook is not None:
            return sign_in_hook()
        return run_gdrive_sign_in(kb_dir, announce=announce)

    def use(token: str) -> str:
        last: TransientError | None = None
        for _attempt in range(3):
            try:
                access, rotated = refresh_fn(token)
            except TransientError as exc:
                last = exc
                continue
            if rotated and rotated != token:
                save_refresh_token(kb_dir, GDRIVE_REFRESH_TOKEN, rotated)
            return access
        assert last is not None
        raise ProviderError(str(last)) from last

    stored = gdrive_refresh_token(kb_dir)
    if stored:
        try:
            return use(stored)
        except AuthRejected:
            if non_interactive:
                raise ProviderError(
                    "Sign-in is required. Re-run without --non-interactive to open a browser."
                ) from None
    token = recover_auth(non_interactive=non_interactive, sign_in=browser_sign_in)
    if not isinstance(token, str) or not token:
        raise ProviderError("Sign-in did not return a refresh token.")
    save_refresh_token(kb_dir, GDRIVE_REFRESH_TOKEN, token)
    return use(token)


def prepare_gdrive_binding(
    kb_dir: Path,
    *,
    name: str,
    folder: str,
    non_interactive: bool,
    announce: Announce | None = None,
    sign_in_hook: SignInFn | None = None,
    refresh: RefreshFn | None = None,
) -> str:
    """Validate the token, then save the binding. A failure writes no binding."""
    binding = parse_gdrive_binding(name=name, folder=folder)
    access_token_for(
        kb_dir,
        non_interactive=non_interactive,
        sign_in_hook=sign_in_hook,
        refresh=refresh,
        announce=announce,
    )
    return upsert_binding(kb_dir, binding)
