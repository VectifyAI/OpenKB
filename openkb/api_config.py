"""Read/patch helpers for the ``GET``/``PATCH /api/v1/kb/config`` endpoints.

Kept out of ``api.py`` so that module stays under the per-file line limit
(``tests/test_file_size.py``). ``apply_kb_config_patch`` performs a
read-modify-write over both ``config.yaml`` and ``.env``; its caller (the PATCH
endpoint) MUST hold the per-KB mutation lock.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from fastapi import HTTPException
from pydantic import ValidationError

from openkb import config as _config_module
from openkb.api_models import (
    _KB_CONFIG_WRITABLE_KEYS,
    GlobalConfigPatchRequest,
    GlobalConfigResponse,
    GlobalConfigValues,
    KbConfigPatchRequest,
    KbConfigResponse,
    _KbConfigWritable,
)
from openkb.config import (
    DEFAULT_CONFIG,
    _atomic_yaml_dump,
    _load_global_config_unlocked,
    _with_global_config_lock,
    load_global_config,
    resolve_credential_bundle,
    resolve_effective_config,
    save_config,
)
from openkb.locks import atomic_write_text

logger = logging.getLogger(__name__)


def read_kb_config(kb_dir: Path) -> KbConfigResponse:
    """Build the config response for a KB.

    Reports the EFFECTIVE scalar values (DEFAULT -> global.yaml -> KB config.yaml
    via resolve_effective_config), plus per-field ``sources`` and the raw
    ``global_values`` so the UI can render 继承(全局/默认) vs 本库覆盖. Credentials
    (openai_api_base plaintext + has_api_key presence flag) are bundle-resolved
    and unchanged; the API key value is NEVER exposed.
    """
    effective, sources = resolve_effective_config(kb_dir)
    bundle = resolve_credential_bundle(kb_dir)
    global_config = load_global_config()
    return KbConfigResponse(
        model=effective["model"],
        language=effective["language"],
        pageindex_threshold=effective["pageindex_threshold"],
        openai_api_base=bundle.base_url,
        has_api_key=bundle.api_key is not None,
        sources=sources,
        global_values=GlobalConfigValues(
            model=global_config.get("model"),
            language=global_config.get("language"),
            pageindex_threshold=global_config.get("pageindex_threshold"),
        ),
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
        # RAW read of the KB's own config.yaml — NOT load_config(), which
        # merges in DEFAULT_CONFIG. Merging defaults here would materialize
        # every default key (model/language/pageindex_threshold/...) into
        # config.yaml on the very next save_config() below, permanently
        # KB-pinning them to their default values and breaking
        # resolve_effective_config's global/default inheritance for every
        # scalar the client didn't ask to change (see resolve_effective_config
        # in config.py, which does the same raw read for its null-inherit gate).
        if config_path.exists():
            with config_path.open("r", encoding="utf-8") as fh:
                config = yaml.safe_load(fh) or {}
        else:
            config = {}
        # Persist the VALIDATED/coerced values (e.g. "20"→20), not the raw dict:
        # a coercible-but-wrong-typed value (numeric string, bool) would otherwise
        # land on disk verbatim and crash downstream int comparisons. exclude_unset
        # keeps merge-patch semantics — only the keys the client sent get touched.
        # RFC 7386: an explicit null REMOVES the key (revert to inherited),
        # while an absent field is left unchanged. model_fields_set (via
        # exclude_unset) distinguishes the two; a None value = explicit null.
        dumped = validated.model_dump(exclude_unset=True)
        for key, value in dumped.items():
            if value is None:
                config.pop(key, None)
            else:
                config[key] = value
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


def read_global_config() -> GlobalConfigResponse:
    """The global default scalars, DEFAULT_CONFIG-filled where global.yaml is
    silent, plus the global-default credentials read from the global ``.env``.

    Credentials come from ``GLOBAL_CONFIG_DIR / ".env"`` (the lowest-precedence
    source in ``resolve_credential_bundle``): ``openai_api_base`` is reported
    plaintext, ``has_api_key`` is a presence flag only — the key value is NEVER
    exposed. An empty string in the ``.env`` counts as unset.
    """
    from dotenv import dotenv_values

    gc = load_global_config()
    # GLOBAL_CONFIG_DIR is read through the module object (not imported by name)
    # because tests monkeypatch openkb.config.GLOBAL_CONFIG_DIR per test.
    env_path = _config_module.GLOBAL_CONFIG_DIR / ".env"
    env_values: dict[str, str | None] = {}
    if env_path.exists():
        env_values = dict(dotenv_values(str(env_path)))
    return GlobalConfigResponse(
        model=gc.get("model", DEFAULT_CONFIG["model"]),
        language=gc.get("language", DEFAULT_CONFIG["language"]),
        pageindex_threshold=gc.get("pageindex_threshold", DEFAULT_CONFIG["pageindex_threshold"]),
        openai_api_base=(env_values.get("OPENAI_API_BASE") or None),
        has_api_key=bool(env_values.get("LLM_API_KEY")),
    )


def apply_global_config_patch(request: GlobalConfigPatchRequest) -> None:
    """Apply an RFC 7386 merge-patch to the whitelisted scalar keys of
    global.yaml. Reuses _KbConfigWritable for VALUE-type validation (a wrong
    type is a 400, never persisted) and MUST preserve the non-scalar global keys
    (known_kbs / kb_aliases / default_kb) by merging into the loaded dict.

    The read-modify-write is done under config._with_global_config_lock() held
    across the ENTIRE section — not just the write — mirroring exactly how
    config.register_kb/register_kb_alias mutate global.yaml: load via the
    UNLOCKED _load_global_config_unlocked() and write DIRECTLY with
    _atomic_yaml_dump while still holding the lock. Without this, a concurrent
    registry mutation (e.g. ``openkb kb add`` -> register_kb, or another PATCH)
    landing between an unlocked read and a separately-locked save would be
    silently clobbered by this endpoint's stale in-memory dict, wiping the KB
    registry. This deliberately does NOT call load_global_config()/
    save_global_config() here: save_global_config() re-acquires the same
    (non-reentrant, flock-based) lock internally, which would deadlock if
    invoked from inside a lock we're already holding.

    Credentials (``api_key`` / ``openai_api_base``) are written to the global
    ``.env`` (``GLOBAL_CONFIG_DIR / ".env"``) with the SAME secure, atomic,
    0o600 read-modify-write ``apply_kb_config_patch`` uses for a KB's ``.env``,
    also under the global lock so a concurrent registry mutation cannot race.
    The api_key value is never returned or logged.

    Validation happens BEFORE the lock is touched, so a bad request still
    returns 400 without acquiring the lock or reading/writing disk.
    """
    fields_set = request.model_fields_set
    write_env = "api_key" in fields_set or "openai_api_base" in fields_set

    # Validate scalar VALUE types before touching disk (a wrong type is a 400,
    # never persisted). Done outside the lock.
    dumped: dict[str, object] | None = None
    if request.config is not None:
        unknown = set(request.config) - _KB_CONFIG_WRITABLE_KEYS
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown config field(s): {', '.join(sorted(unknown))}",
            )
        try:
            validated = _KbConfigWritable.model_validate(request.config)
        except ValidationError as exc:
            first = exc.errors()[0]
            field = ".".join(str(p) for p in first.get("loc", ())) or "config"
            raise HTTPException(
                status_code=400,
                detail=f"Invalid type for config field '{field}': {first['msg']}",
            ) from exc
        dumped = validated.model_dump(exclude_unset=True)

    if dumped is None and not write_env:
        return

    with _with_global_config_lock():
        if dumped is not None:
            gc = _load_global_config_unlocked()
            for key, value in dumped.items():
                if value is None:
                    gc.pop(key, None)  # RFC 7386 removal -> back to DEFAULT_CONFIG
                else:
                    gc[key] = value
            # GLOBAL_CONFIG_PATH is read through the module object (not imported
            # by name) because tests monkeypatch openkb.config.GLOBAL_CONFIG_PATH
            # per test; a `from openkb.config import GLOBAL_CONFIG_PATH` would
            # freeze a stale Path captured at first import.
            _atomic_yaml_dump(_config_module.GLOBAL_CONFIG_PATH, gc)
        if write_env:
            _write_global_env(request, fields_set)


def _write_global_env(request: GlobalConfigPatchRequest, fields_set: set[str]) -> None:
    """Read-modify-write the global ``.env`` credential file, mirroring the
    per-KB ``.env`` write in ``apply_kb_config_patch``.

    MUST be called while holding ``_with_global_config_lock()`` (a
    read-modify-write). RFC 7386: an explicit ``null`` removes the key while an
    absent field is left unchanged (``model_fields_set`` distinguishes them).
    The write is atomic and the file is tightened to 0o600 BEFORE writing so the
    LLM key is never world-readable — not even briefly. The key VALUE is never
    logged.
    """
    # Module object, not a by-name import: tests monkeypatch GLOBAL_CONFIG_DIR.
    env_path = _config_module.GLOBAL_CONFIG_DIR / ".env"
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
    # See apply_kb_config_patch: touch(0o600)+chmod(0o600) BEFORE the atomic
    # write so the temp file inherits 0o600 and os.replace lands a 0o600 .env
    # with no widen-then-chmod gap. The lock's mkdir already created the dir.
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.touch(mode=0o600, exist_ok=True)
    env_path.chmod(0o600)
    atomic_write_text(env_path, "".join(f"{k}={v}\n" for k, v in env_lines.items()))
    logger.info(
        "global/config credential rotation: fields=%s",
        sorted(f for f in ("api_key", "openai_api_base") if f in fields_set),
    )
