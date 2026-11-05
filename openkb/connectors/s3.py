"""Amazon S3 list and download. boto3 is imported only when a client is built."""

from __future__ import annotations

from pathlib import Path

from openkb.connectors.backend import ProviderError, RemoteObject, TransientError
from openkb.connectors.model import S3Binding, s3_object_identity

_TRANSIENT_STATUS = {429, 500, 502, 503, 504}
_TRANSIENT_CODES = {"SlowDown", "RequestTimeout", "Throttling", "RequestTimeoutException"}


def _load_boto3():
    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ProviderError(
            'Amazon S3 support is not installed. Install it with: pip install "openkb[s3]"'
        ) from exc
    return boto3


def _raise_translated(exc: Exception) -> None:
    try:
        from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-not-found]
    except ImportError:
        raise ProviderError(str(exc)) from exc
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {}) if isinstance(exc.response, dict) else {}
        meta = exc.response.get("ResponseMetadata", {}) if isinstance(exc.response, dict) else {}
        status = meta.get("HTTPStatusCode")
        code = str(error.get("Code", ""))
        if status in _TRANSIENT_STATUS or code in _TRANSIENT_CODES:
            raise TransientError(f"S3 transient error ({code or status})") from exc
        raise ProviderError(f"S3 error ({code or status})") from exc
    if isinstance(exc, BotoCoreError):
        name = type(exc).__name__
        if "Timeout" in name or "Connection" in name:
            raise TransientError(f"S3 transient error ({name})") from exc
        raise ProviderError(f"S3 error ({name})") from exc
    raise exc


class S3Backend:
    """List and download one bucket prefix with caller-supplied keys."""

    def __init__(
        self,
        binding: S3Binding,
        access_key: str,
        secret_key: str,
        *,
        client=None,
    ) -> None:
        self._binding = binding
        self._access_key = access_key
        self._secret_key = secret_key
        self._client = client

    def _s3(self):
        if self._client is not None:
            return self._client
        boto3 = _load_boto3()
        return boto3.client(
            "s3",
            region_name=self._binding.region,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
        )

    def list_objects(self) -> list[RemoteObject]:
        client = self._s3()
        kwargs: dict[str, str] = {"Bucket": self._binding.bucket}
        if self._binding.prefix:
            kwargs["Prefix"] = self._binding.prefix
        found: list[RemoteObject] = []
        try:
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(**kwargs):
                for item in page.get("Contents") or []:
                    key = item.get("Key")
                    if not isinstance(key, str) or not key or key.endswith("/"):
                        continue
                    name = key.rsplit("/", 1)[-1]
                    found.append(
                        RemoteObject(
                            identity=s3_object_identity(self._binding.bucket, key),
                            name=name,
                        )
                    )
        except Exception as exc:
            if isinstance(exc, (ProviderError, TransientError)):
                raise
            _raise_translated(exc)
        return found

    def download(self, obj: RemoteObject, dest: Path) -> None:
        prefix = f"s3://{self._binding.bucket}/"
        if not obj.identity.startswith(prefix):
            raise ProviderError(f"Object {obj.identity} is outside bucket {self._binding.bucket}.")
        key = obj.identity[len(prefix) :]
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._s3().download_file(self._binding.bucket, key, str(dest))
        except Exception as exc:
            if isinstance(exc, (ProviderError, TransientError)):
                raise
            _raise_translated(exc)
