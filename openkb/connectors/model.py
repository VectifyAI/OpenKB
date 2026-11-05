"""Binding records and remote object identities."""

from __future__ import annotations

import re
from dataclasses import dataclass

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


@dataclass(frozen=True)
class S3Binding:
    """One Amazon S3 prefix bound to a knowledge base."""

    name: str
    region: str
    bucket: str
    prefix: str = ""

    provider: str = "s3"

    def location(self) -> dict[str, str]:
        return {"region": self.region, "bucket": self.bucket, "prefix": self.prefix}

    def describe(self) -> str:
        prefix = self.prefix or ""
        return f"s3://{self.bucket}/{prefix}"

    def page_prefix(self) -> str:
        """Registry prefix for pages compiled from this binding."""
        return f"s3://{self.bucket}/{self.prefix}"


def s3_object_identity(bucket: str, key: str) -> str:
    """Registry identity for one S3 object. A rename is a different key."""
    return f"s3://{bucket}/{key}"


def validate_binding_name(name: str) -> str:
    cleaned = name.strip()
    if not _NAME_RE.fullmatch(cleaned):
        raise ValueError(
            "Binding name must be 1-64 characters of letters, digits, '_' or '-', "
            "and must start with a letter or digit."
        )
    return cleaned


def parse_s3_binding(
    *,
    name: str,
    region: str,
    bucket: str,
    prefix: str = "",
) -> S3Binding:
    cleaned_name = validate_binding_name(name)
    cleaned_region = region.strip()
    cleaned_bucket = bucket.strip()
    if not cleaned_region:
        raise ValueError("S3 region is required.")
    if not cleaned_bucket or "/" in cleaned_bucket:
        raise ValueError("S3 bucket is required and cannot contain '/'.")
    cleaned_prefix = prefix.strip()
    if cleaned_prefix.startswith("/"):
        raise ValueError("S3 prefix must not start with '/'. An empty prefix is the bucket root.")
    return S3Binding(
        name=cleaned_name,
        region=cleaned_region,
        bucket=cleaned_bucket,
        prefix=cleaned_prefix,
    )


_FOLDER_IN_URL = re.compile(r"(?:/folders/|[?&]id=)([A-Za-z0-9_-]+)")
_FOLDER_ID = re.compile(r"[A-Za-z0-9_-]{10,}")


@dataclass(frozen=True)
class GDriveBinding:
    """One folder in the user's own Google Drive."""

    name: str
    folder_id: str
    provider: str = "gdrive"

    def location(self) -> dict[str, str]:
        return {"folder_id": self.folder_id}

    def describe(self) -> str:
        return f"gdrive://{self.folder_id}/"

    def page_prefix(self) -> str:
        return f"gdrive://{self.folder_id}/"


def gdrive_object_identity(folder_id: str, relative_path: str) -> str:
    """Registry identity. ``relative_path`` is the path under the bound folder."""
    return f"gdrive://{folder_id}/{relative_path}"


def parse_gdrive_folder(folder: str) -> str:
    """Accept a folder URL or a raw folder id."""
    text = folder.strip()
    found = _FOLDER_IN_URL.search(text)
    if found:
        return found.group(1)
    if _FOLDER_ID.fullmatch(text):
        return text
    raise ValueError("Drive folder must be a folder URL or a folder id.")


def parse_gdrive_binding(*, name: str, folder: str) -> GDriveBinding:
    return GDriveBinding(name=validate_binding_name(name), folder_id=parse_gdrive_folder(folder))


@dataclass(frozen=True)
class OneDriveBinding:
    """One path in the user's own OneDrive, personal or work/school."""

    name: str
    path: str
    provider: str = "onedrive"

    def location(self) -> dict[str, str]:
        return {"path": self.path}

    def describe(self) -> str:
        return f"onedrive://{self.path}/"

    def page_prefix(self) -> str:
        return f"onedrive://{self.path}/"


def parse_onedrive_path(path: str) -> str:
    """A path from the drive root, without a leading slash."""
    text = path.strip()
    if text.startswith("/") or "\\" in text or not text:
        raise ValueError(
            "OneDrive path must start at the drive root and must not begin with a slash."
        )
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("OneDrive path must not contain empty, '.' or '..' segments.")
    return text


def parse_onedrive_binding(*, name: str, path: str) -> OneDriveBinding:
    return OneDriveBinding(name=validate_binding_name(name), path=parse_onedrive_path(path))
