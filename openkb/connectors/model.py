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
