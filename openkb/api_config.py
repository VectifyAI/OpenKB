"""Read/patch helpers for the ``GET``/``PATCH /api/v1/kb/config`` endpoints.

Kept out of ``api.py`` so that module stays under the per-file line limit
(``tests/test_file_size.py``). ``apply_kb_config_patch`` performs a
read-modify-write over both ``config.yaml`` and ``.env``; its caller (the PATCH
endpoint) MUST hold the per-KB mutation lock.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

from openkb.api_models import (
    _KB_CONFIG_WRITABLE_KEYS,
    KbConfigPatchRequest,
    KbConfigResponse,
    _KbConfigWritable,
)
from openkb.config import (
    DEFAULT_CONFIG,
    load_config,
    resolve_credential_bundle,
    save_config,
)
from openkb.locks import atomic_write_text

logger = logging.getLogger(__name__)


def read_kb_config(kb_dir: Path) -> KbConfigResponse:
    """Build the config response for a KB.

    Returns the ``config.yaml`` fields as-is, plus ``openai_api_base`` in
    plaintext (a config value, not a credential) and ``has_api_key`` — a boolean
    presence flag that NEVER exposes the API key value itself.
    """
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    bundle = resolve_credential_bundle(kb_dir)
    return KbConfigResponse(
        model=config.get("model", DEFAULT_CONFIG["model"]),
        language=config.get("language", DEFAULT_CONFIG["language"]),
        pageindex_threshold=config.get(
            "pageindex_threshold", DEFAULT_CONFIG["pageindex_threshold"]
        ),
        openai_api_base=bundle.base_url,
        has_api_key=bundle.api_key is not None,
    )


def apply_kb_config_patch(kb_dir: Path, request: KbConfigPatchRequest) -> None:
    """Apply a JSON Merge Patch (RFC 7386) to a KB's ``config.yaml`` and ``.env``.

    The caller MUST hold the per-KB mutation lock: this is a read-modify-write
    over both files, so two concurrent patches without the lock would silently
    drop one's fields. Merge-patch semantics rely on ``model_fields_set`` so an
    ABSENT field is left unchanged while an explicit ``null`` CLEARS it — a plain
    ``is None`` check cannot tell the two apart. An unknown ``config`` key is a
    400 (not a silent no-op). Credential values are never logged.
    """
    fields_set = request.model_fields_set

    if request.config is not None:
        unknown = set(request.config) - _KB_CONFIG_WRITABLE_KEYS
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown config field(s): {', '.join(sorted(unknown))}",
            )
        # Validate VALUE types before touching disk: a wrong-typed value is a
        # client error (400), and persisting it would 500 every future GET.
        try:
            validated = _KbConfigWritable.model_validate(request.config)
        except ValidationError as exc:
            first = exc.errors()[0]
            field = ".".join(str(p) for p in first.get("loc", ())) or "config"
            raise HTTPException(
                status_code=400,
                detail=f"Invalid type for config field '{field}': {first['msg']}",
            ) from exc
        config_path = kb_dir / ".openkb" / "config.yaml"
        config = load_config(config_path)
        # Persist the VALIDATED/coerced values (e.g. "20"→20), not the raw dict:
        # a coercible-but-wrong-typed value (numeric string, bool) would otherwise
        # land on disk verbatim and crash downstream int comparisons. exclude_unset
        # keeps merge-patch semantics — only the keys the client sent get written.
        config.update(validated.model_dump(exclude_unset=True))
        save_config(config_path, config)

    if "api_key" in fields_set or "openai_api_base" in fields_set:
        env_path = kb_dir / ".env"
        env_lines: dict[str, str] = {}
        if env_path.exists():
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _eq, v = line.partition("=")
                    env_lines[k.strip()] = v
        if "api_key" in fields_set:
            if request.api_key is None:
                env_lines.pop("LLM_API_KEY", None)
            else:
                env_lines["LLM_API_KEY"] = request.api_key.get_secret_value()
        if "openai_api_base" in fields_set:
            if request.openai_api_base is None:
                env_lines.pop("OPENAI_API_BASE", None)
            else:
                env_lines["OPENAI_API_BASE"] = request.openai_api_base
        # .env holds the sensitive LLM_API_KEY, so the write must be atomic
        # (crash-safe, matching config.yaml) AND the key must never be
        # world-readable on disk — not even briefly. atomic_write_text copies
        # the target's *current* mode onto its private temp file before the
        # rename (see locks._target_mode), so tighten the target to 0o600 first,
        # creating it restricted when absent: the temp file then inherits 0o600
        # and os.replace lands a 0o600 .env with no widen-then-chmod gap.
        env_path.touch(mode=0o600, exist_ok=True)
        env_path.chmod(0o600)
        atomic_write_text(env_path, "".join(f"{k}={v}\n" for k, v in env_lines.items()))
        logger.info(
            "kb/config credential rotation: kb=%s fields=%s",
            request.kb,
            sorted(f for f in ("api_key", "openai_api_base") if f in fields_set),
        )
