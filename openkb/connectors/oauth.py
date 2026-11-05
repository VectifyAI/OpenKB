"""Loopback OAuth sign-in shared by Drive and, later, OneDrive.

The listener binds ``127.0.0.1`` only. Google's desktop client uses
``http://127.0.0.1:7567/callback``. Microsoft's portal text box accepts
``http://localhost`` rather than an ``http://127.0.0.1`` URI, so OneDrive
sends that redirect string; the browser still reaches this listener because
``localhost`` resolves here. ``--non-interactive`` never calls :func:`sign_in`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import portalocker

from openkb.connectors.backend import ProviderError, TransientError
from openkb.connectors.state import BUSY_MESSAGE, LOCK_TIMEOUT_SECONDS, KbBusyError
from openkb.envfile import merge_env_file
from openkb.locks import kb_lock

LOOPBACK_HOST = "127.0.0.1"
LOOPBACK_PORT = 7567
CALLBACK_PATH = "/callback"
SIGN_IN_TIMEOUT_SECONDS = 300
GDRIVE_REDIRECT_URI = "http://127.0.0.1:7567/callback"

_AUTH_LOCK = threading.Lock()
Announce = Callable[[str], None]


class AuthRejected(ProviderError):
    """The token endpoint rejected the refresh token or the client."""


class _CallbackResult:
    def __init__(self) -> None:
        self.query: dict[str, list[str]] | None = None


class _CallbackServer(HTTPServer):
    def __init__(self, address: tuple[str, int], result: _CallbackResult) -> None:
        self.result = result
        super().__init__(address, _CallbackHandler)


class _CallbackHandler(BaseHTTPRequestHandler):
    server: _CallbackServer

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_error(404)
            return
        self.server.result.query = urllib.parse.parse_qs(parsed.query)
        body = b"OpenKB sign-in complete. You can close this window."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _post_form(url: str, fields: dict[str, str]) -> dict[str, Any]:
    payload = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if exc.code in {429, 500, 502, 503, 504}:
            raise TransientError(f"Token endpoint returned HTTP {exc.code}") from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            _raise_oauth_error(parsed, exc.code)
        raise ProviderError(f"Token endpoint returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise TransientError("Token endpoint could not be reached") from exc
    if not isinstance(body, dict):
        raise ProviderError("Token endpoint returned a response that is not an object.")
    _raise_oauth_error(body, 200)
    return body


def _raise_oauth_error(body: dict[str, Any], status: int) -> None:
    error = body.get("error")
    if not error:
        return
    code = str(error)
    if code in {"invalid_grant", "invalid_client", "interaction_required", "consent_required"}:
        raise AuthRejected(f"Sign-in was rejected ({code}).")
    raise ProviderError(f"Token endpoint returned {code} (HTTP {status}).")


def refresh_access_token(
    *,
    token_url: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    scopes: str,
) -> tuple[str, str | None]:
    """Exchange a refresh token. Returns ``(access_token, new_refresh_or_none)``."""
    body = _post_form(
        token_url,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scopes,
        },
    )
    access = body.get("access_token")
    if not isinstance(access, str) or not access:
        raise ProviderError("Token endpoint did not return an access token.")
    rotated = body.get("refresh_token")
    new_refresh = rotated if isinstance(rotated, str) and rotated else None
    return access, new_refresh


def save_refresh_token(kb_dir: Path, key: str, token: str) -> None:
    """Write one refresh token into ``<kb>/.env`` at mode ``0600``."""
    try:
        with kb_lock(kb_dir / ".openkb", exclusive=True, timeout=LOCK_TIMEOUT_SECONDS):
            merge_env_file(kb_dir / ".env", {key: token})
    except TimeoutError as exc:
        raise KbBusyError(BUSY_MESSAGE) from exc
    except portalocker.exceptions.LockException as exc:
        raise KbBusyError(BUSY_MESSAGE) from exc


def sign_in(
    *,
    authorize_url: str,
    token_url: str,
    client_id: str,
    client_secret: str,
    scopes: str,
    redirect_uri: str,
    extra_params: dict[str, str] | None = None,
    announce: Announce | None = None,
    host: str = LOOPBACK_HOST,
    port: int = LOOPBACK_PORT,
    timeout: float = SIGN_IN_TIMEOUT_SECONDS,
) -> str:
    """Open a loopback sign-in and return the refresh token.

    ``announce`` receives the authorize URL before the wait, so a headless
    session can print it. The browser wait does not hold the ingest lock.
    A second call waits until the first listener is done.
    """
    if host != LOOPBACK_HOST:
        raise ProviderError("Sign-in listens on 127.0.0.1 only.")
    if not _AUTH_LOCK.acquire(blocking=True):  # pragma: no cover - blocking acquire waits
        raise ProviderError("Another sign-in is already waiting for the browser.")
    try:
        return _sign_in_locked(
            authorize_url=authorize_url,
            token_url=token_url,
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
            redirect_uri=redirect_uri,
            extra_params=extra_params or {},
            announce=announce,
            host=host,
            port=port,
            timeout=timeout,
        )
    finally:
        _AUTH_LOCK.release()


def _sign_in_locked(
    *,
    authorize_url: str,
    token_url: str,
    client_id: str,
    client_secret: str,
    scopes: str,
    redirect_uri: str,
    extra_params: dict[str, str],
    announce: Announce | None,
    host: str,
    port: int,
    timeout: float,
) -> str:
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    query.update(extra_params)
    url = authorize_url + "?" + urllib.parse.urlencode(query)
    result = _CallbackResult()
    try:
        server = _CallbackServer((host, port), result)
    except OSError as exc:
        raise ProviderError(
            f"Sign-in port {port} is already in use. Close the other OpenKB sign-in and retry."
        ) from exc
    server.timeout = 0.25
    if announce is not None:
        announce(url)
    deadline = time.monotonic() + timeout
    try:
        while result.query is None and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if result.query is None:
        raise ProviderError("Sign-in timed out.")
    returned_state = (result.query.get("state") or [""])[0]
    if returned_state != state:
        raise ProviderError("Sign-in failed because the returned state did not match.")
    if result.query.get("error"):
        raise ProviderError(f"Sign-in failed ({result.query['error'][0]}).")
    code = (result.query.get("code") or [""])[0]
    if not code:
        raise ProviderError("Sign-in failed because the provider returned no code.")
    body = _post_form(
        token_url,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": verifier,
        },
    )
    refresh = body.get("refresh_token")
    if not isinstance(refresh, str) or not refresh:
        raise ProviderError("The provider did not return a refresh token.")
    return refresh
