from __future__ import annotations

import yaml

from openkb.config import resolve_per_request_overrides, resolve_credential_bundle


def _write_config(kb_dir: object, config: dict) -> None:
    """Write a config dict to ``.openkb/config.yaml``."""
    openkb_dir = kb_dir / ".openkb"
    openkb_dir.mkdir(parents=True, exist_ok=True)
    (openkb_dir / "config.yaml").write_text(
        yaml.safe_dump(config), encoding="utf-8"
    )


def test_litellm_extra_headers_overrides_top_level():
    """litellm.extra_headers must override the top-level extra_headers key."""
    config = {
        "extra_headers": {"Editor-Version": "top-level"},
        "litellm": {"extra_headers": {"Editor-Version": "from-litellm"}},
    }
    headers, _timeout, _litellm = resolve_per_request_overrides(config)
    assert headers == {"Editor-Version": "from-litellm"}


def test_litellm_timeout_overrides_top_level():
    """litellm.timeout must override the top-level timeout key."""
    config = {
        "timeout": 30,
        "litellm": {"timeout": 120},
    }
    _headers, timeout, _litellm = resolve_per_request_overrides(config)
    assert timeout == 120


def test_popped_keys_absent_from_litellm_settings():
    """litellm.extra_headers / litellm.timeout are popped from litellm_settings."""
    config = {
        "litellm": {
            "extra_headers": {"X": "1"},
            "timeout": 90,
            "drop_params": True,  # genuine module-level setting, must survive
        },
    }
    _h, _t, litellm_settings = resolve_per_request_overrides(config)
    assert "extra_headers" not in litellm_settings
    assert "timeout" not in litellm_settings
    assert litellm_settings == {"drop_params": True}


def test_no_litellm_block_preserves_top_level():
    """Without a litellm: block, top-level extra_headers/timeout pass through."""
    config = {"extra_headers": {"Editor-Version": "ok"}, "timeout": 45}
    headers, timeout, litellm_settings = resolve_per_request_overrides(config)
    assert headers == {"Editor-Version": "ok"}
    assert timeout == 45
    assert litellm_settings == {}


def test_bundle_reads_litellm_extra_headers(tmp_path):
    """Regression: resolve_credential_bundle must honor litellm.extra_headers.

    Previously the bundle only read top-level extra_headers, so KBs that put
    auth headers (e.g. Copilot Editor-Version) under litellm: got an empty
    bundle on REST endpoints while the CLI worked fine.
    """
    _write_config(tmp_path, {
        "litellm": {
            "extra_headers": {"Editor-Version": "github-copilot/1.0"},
            "timeout": 60,
        },
    })
    bundle = resolve_credential_bundle(tmp_path)
    assert bundle.extra_headers == {"Editor-Version": "github-copilot/1.0"}
    assert bundle.timeout == 60


def test_bundle_has_no_litellm_settings_field():
    """The bundle must not carry litellm_settings (process global, not isolatable)."""
    import dataclasses
    from openkb.config import LlmCredentialBundle

    field_names = {f.name for f in dataclasses.fields(LlmCredentialBundle)}
    assert "litellm_settings" not in field_names
