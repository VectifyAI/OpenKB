"""OneDrive list and download.

The Microsoft auth library is imported only on the real refresh and download
path. Tests pass a fake client and never import it. This module does not
import the Drive backend.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn, Protocol

from openkb.connectors.backend import ProviderError, RemoteObject, TransientError
from openkb.connectors.credentials import (
    ONEDRIVE_REFRESH_TOKEN,
    onedrive_client,
    onedrive_refresh_token,
)
from openkb.connectors.model import OneDriveBinding, parse_onedrive_binding
from openkb.connectors.oauth import (
    LOOPBACK_HOST,
    LOOPBACK_PORT,
    AuthRejected,
    save_refresh_token,
    sign_in,
)
from openkb.connectors.store import upsert_binding
from openkb.connectors.sync import recover_auth

ONEDRIVE_AUTHORIZE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
ONEDRIVE_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
ONEDRIVE_AUTHORITY = "https://login.microsoftonline.com/common"
ONEDRIVE_SCOPE = "offline_access https://graph.microsoft.com/Files.Read"
ONEDRIVE_GRAPH_SCOPE = "https://graph.microsoft.com/Files.Read"
# Microsoft's portal text box cannot add an http://127.0.0.1 reply URL, and the
# port is ignored only for localhost. The listener still binds 127.0.0.1.
ONEDRIVE_REDIRECT_URI = f"http://localhost:{LOOPBACK_PORT}/callback"
GRAPH_ROOT = "https://graph.microsoft.com/v1.0/me/drive/root:/"
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}

Announce = Callable[[str], None]
RefreshFn = Callable[[str], tuple[str, str | None]]
SignInFn = Callable[[], object]


class DriveItemClient(Protocol):
    """The slice of Microsoft Graph this backend uses."""

    def list_children(self, folder_path: str) -> list[dict[str, object]]:
        """Return children. Each item has ``name`` and ``folder``."""

    def download(self, file_path: str, dest: Path) -> None:
        """Write one file to ``dest``."""


def _load_msal() -> Any:
    try:
        import msal  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ProviderError(
            'OneDrive support is not installed. Install it with: pip install "openkb[onedrive]"'
        ) from exc
    return msal


def _translate(exc: Exception) -> NoReturn:
    status = getattr(exc, "code", None)
    if status in _TRANSIENT_STATUS:
        raise TransientError(f"OneDrive transient error (HTTP {status})") from exc
    name = type(exc).__name__
    if "Timeout" in name or "URLError" in name or "Connection" in name:
        raise TransientError(f"OneDrive transient error ({name})") from exc
    raise ProviderError(f"OneDrive error ({name})") from exc


class _StripAuthOnCrossHost(urllib.request.HTTPRedirectHandler):
    """Drop Authorization when Graph redirects to a preauthenticated download host."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        old_host = urllib.parse.urlparse(req.full_url).netloc
        new_host = urllib.parse.urlparse(newurl).netloc
        if old_host != new_host:
            redirected.remove_header("Authorization")
        return redirected


def _quote_path(path: str) -> str:
    return urllib.parse.quote(path, safe="/")


def _children_url(folder_path: str) -> str:
    return f"{GRAPH_ROOT}{_quote_path(folder_path)}:/children"


def _content_url(file_path: str) -> str:
    return f"{GRAPH_ROOT}{_quote_path(file_path)}:/content"


def _request_json(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        _translate(exc)
    except urllib.error.URLError as exc:
        raise TransientError("OneDrive could not be reached") from exc
    if not isinstance(body, dict):
        raise ProviderError("OneDrive returned a response that is not an object.")
    return body


def download_graph_file(url: str, token: str, dest: Path) -> None:
    """Download one Graph ``/content`` URL, without sending the bearer token off Graph."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(_StripAuthOnCrossHost)
    try:
        with opener.open(request, timeout=60) as response:
            dest.write_bytes(response.read())
    except urllib.error.HTTPError as exc:
        _translate(exc)
    except urllib.error.URLError as exc:
        raise TransientError("OneDrive could not be reached") from exc


class _GraphClient:
    def __init__(self, access_token: str) -> None:
        _load_msal()
        self._token = access_token

    def list_children(self, folder_path: str) -> list[dict[str, object]]:
        found: list[dict[str, object]] = []
        url: str | None = _children_url(folder_path) + "?$select=id,name,folder,file"
        while url:
            payload = _request_json(url, self._token)
            for item in payload.get("value") or []:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                if not isinstance(name, str):
                    continue
                found.append({"name": name, "folder": "folder" in item})
            next_link = payload.get("@odata.nextLink")
            url = next_link if isinstance(next_link, str) and next_link else None
        return found

    def download(self, file_path: str, dest: Path) -> None:
        download_graph_file(_content_url(file_path), self._token, dest)


def _walk(client: DriveItemClient, folder_path: str) -> list[RemoteObject]:
    found: list[RemoteObject] = []
    for item in client.list_children(folder_path):
        name = item.get("name")
        if not isinstance(name, str) or not name or "/" in name or name in {".", ".."}:
            continue
        full = f"{folder_path}/{name}"
        if item.get("folder") is True:
            found.extend(_walk(client, full))
            continue
        found.append(RemoteObject(identity=f"onedrive://{full}", name=name, locator=full))
    return found


class OneDriveBackend:
    """List a path recursively and download one file."""

    def __init__(
        self,
        binding: OneDriveBinding,
        access_token: str,
        *,
        client: DriveItemClient | None = None,
    ) -> None:
        self._binding = binding
        self._client = client if client is not None else _GraphClient(access_token)

    def list_objects(self) -> list[RemoteObject]:
        try:
            return _walk(self._client, self._binding.path)
        except Exception as exc:
            if isinstance(exc, (ProviderError, TransientError)):
                raise
            _translate(exc)

    def download(self, obj: RemoteObject, dest: Path) -> None:
        if not obj.locator:
            raise ProviderError(f"OneDrive path is missing for {obj.identity}.")
        try:
            self._client.download(obj.locator, dest)
        except Exception as exc:
            if isinstance(exc, (ProviderError, TransientError)):
                raise
            _translate(exc)


def _missing_client_message() -> str:
    return (
        "OneDrive credentials are missing. Set OPENKB_ONEDRIVE_CLIENT_ID and "
        "OPENKB_ONEDRIVE_CLIENT_SECRET in the environment or <kb>/.env."
    )


def run_onedrive_sign_in(kb_dir: Path, *, announce: Announce | None = None) -> str:
    """Browser sign-in. Returns the refresh token and does not write it."""
    client = onedrive_client(kb_dir)
    if client is None:
        raise ProviderError(_missing_client_message())
    client_id, secret = client
    return sign_in(
        authorize_url=ONEDRIVE_AUTHORIZE_URL,
        token_url=ONEDRIVE_TOKEN_URL,
        client_id=client_id,
        client_secret=secret,
        scopes=ONEDRIVE_SCOPE,
        redirect_uri=ONEDRIVE_REDIRECT_URI,
        extra_params={"prompt": "consent"},
        announce=announce,
        host=LOOPBACK_HOST,
    )


def _default_refresh(kb_dir: Path, refresh_token: str) -> tuple[str, str | None]:
    client = onedrive_client(kb_dir)
    if client is None:
        raise ProviderError(_missing_client_message())
    client_id, secret = client
    msal = _load_msal()
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=ONEDRIVE_AUTHORITY,
        client_credential=secret,
    )
    try:
        result = app.acquire_token_by_refresh_token(refresh_token, scopes=[ONEDRIVE_GRAPH_SCOPE])
    except Exception as exc:
        _translate(exc)
    if not isinstance(result, dict):
        raise ProviderError("Token endpoint returned a response that is not an object.")
    error = result.get("error")
    if error:
        code = str(error)
        if code in {"invalid_grant", "invalid_client", "interaction_required", "consent_required"}:
            raise AuthRejected(f"Sign-in was rejected ({code}).")
        raise ProviderError(f"Token endpoint returned {code}.")
    access = result.get("access_token")
    if not isinstance(access, str) or not access:
        raise ProviderError("Token endpoint did not return an access token.")
    rotated = result.get("refresh_token")
    new_refresh = rotated if isinstance(rotated, str) and rotated else None
    return access, new_refresh


def access_token_for(
    kb_dir: Path,
    *,
    non_interactive: bool,
    sign_in_hook: SignInFn | None = None,
    refresh: RefreshFn | None = None,
    announce: Announce | None = None,
) -> str:
    """Return an access token. ``--non-interactive`` never calls the sign-in hook."""
    if onedrive_client(kb_dir) is None:
        raise ProviderError(_missing_client_message())
    refresh_fn = refresh or (lambda token: _default_refresh(kb_dir, token))

    def browser_sign_in() -> object:
        if sign_in_hook is not None:
            return sign_in_hook()
        return run_onedrive_sign_in(kb_dir, announce=announce)

    def use(token: str) -> str:
        last: TransientError | None = None
        for _attempt in range(3):
            try:
                access, rotated = refresh_fn(token)
            except TransientError as exc:
                last = exc
                continue
            if rotated and rotated != token:
                save_refresh_token(kb_dir, ONEDRIVE_REFRESH_TOKEN, rotated)
            return access
        assert last is not None
        raise ProviderError(str(last)) from last

    stored = onedrive_refresh_token(kb_dir)
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
    save_refresh_token(kb_dir, ONEDRIVE_REFRESH_TOKEN, token)
    return use(token)


def prepare_onedrive_binding(
    kb_dir: Path,
    *,
    name: str,
    path: str,
    non_interactive: bool,
    announce: Announce | None = None,
    sign_in_hook: SignInFn | None = None,
    refresh: RefreshFn | None = None,
) -> str:
    """Validate the token, then save the binding. A failure writes no binding."""
    binding = parse_onedrive_binding(name=name, path=path)
    access_token_for(
        kb_dir,
        non_interactive=non_interactive,
        sign_in_hook=sign_in_hook,
        refresh=refresh,
        announce=announce,
    )
    return upsert_binding(kb_dir, binding)
