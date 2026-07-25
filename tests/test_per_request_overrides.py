from __future__ import annotations

import yaml

from openkb.config import resolve_credential_bundle, resolve_per_request_overrides


def _write_config(kb_dir: object, config: dict) -> None:
    """Write a config dict to ``.openkb/config.yaml``."""
    openkb_dir = kb_dir / ".openkb"
    openkb_dir.mkdir(parents=True, exist_ok=True)
    (openkb_dir / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")


def test_litellm_extra_headers_overrides_top_level():
    """litellm.extra_headers must override the top-level extra_headers key."""
    config = {
        "extra_headers": {"Editor-Version": "top-level"},
        "litellm": {"extra_headers": {"Editor-Version": "from-litellm"}},
    }
    headers, _timeout, _api_base, _litellm = resolve_per_request_overrides(config)
    assert headers == {"Editor-Version": "from-litellm"}


def test_litellm_timeout_overrides_top_level():
    """litellm.timeout must override the top-level timeout key."""
    config = {
        "timeout": 30,
        "litellm": {"timeout": 120},
    }
    _headers, timeout, _api_base, _litellm = resolve_per_request_overrides(config)
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
    _h, _t, _api_base, litellm_settings = resolve_per_request_overrides(config)
    assert "extra_headers" not in litellm_settings
    assert "timeout" not in litellm_settings
    assert litellm_settings == {"drop_params": True}


def test_no_litellm_block_preserves_top_level():
    """Without a litellm: block, top-level extra_headers/timeout pass through."""
    config = {"extra_headers": {"Editor-Version": "ok"}, "timeout": 45}
    headers, timeout, _api_base, litellm_settings = resolve_per_request_overrides(config)
    assert headers == {"Editor-Version": "ok"}
    assert timeout == 45
    assert litellm_settings == {}


def test_bundle_reads_litellm_extra_headers(tmp_path):
    """Regression: resolve_credential_bundle must honor litellm.extra_headers.

    Previously the bundle only read top-level extra_headers, so KBs that put
    auth headers (e.g. Copilot Editor-Version) under litellm: got an empty
    bundle on REST endpoints while the CLI worked fine.
    """
    _write_config(
        tmp_path,
        {
            "parallel_tool_calls": True,
            "litellm": {
                "extra_headers": {"Editor-Version": "github-copilot/1.0"},
                "timeout": 60,
            },
        },
    )
    bundle = resolve_credential_bundle(tmp_path)
    assert bundle.extra_headers == {"Editor-Version": "github-copilot/1.0"}
    assert bundle.timeout == 60
    assert bundle.parallel_tool_calls is True
    assert bundle.parallel_tool_calls_explicit is True


def test_bundle_has_no_litellm_settings_field():
    """The bundle must not carry litellm_settings (process global, not isolatable)."""
    import dataclasses

    from openkb.config import LlmCredentialBundle

    field_names = {f.name for f in dataclasses.fields(LlmCredentialBundle)}
    assert "litellm_settings" not in field_names


def test_litellm_api_base_popped_and_returned():
    """litellm.api_base is popped from litellm_settings (so the CLI path does
    not set the deepseek-ignored litellm.api_base module global) and returned
    as the third element for the bundle / set_base_url routing."""
    config = {"litellm": {"api_base": "https://ark.example/api/v3", "drop_params": True}}
    _h, _t, api_base, litellm_settings = resolve_per_request_overrides(config)
    assert api_base == "https://ark.example/api/v3"
    assert "api_base" not in litellm_settings
    assert litellm_settings == {"drop_params": True}


def test_bundle_routes_litellm_api_base_into_base_url(tmp_path, monkeypatch):
    """litellm.api_base flows into bundle.base_url so provider-prefixed models
    (e.g. deepseek/...) honor it via the explicit base_url kwarg."""
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_DIR", tmp_path / "global")
    _write_config(tmp_path, {"litellm": {"api_base": "https://ark.example/api/v3"}})
    bundle = resolve_credential_bundle(tmp_path)
    assert bundle.base_url == "https://ark.example/api/v3"


def test_bundle_openai_api_base_env_wins_over_litellm_api_base(tmp_path, monkeypatch):
    """OPENAI_API_BASE (env) takes precedence over litellm.api_base (config)."""
    _write_config(tmp_path, {"litellm": {"api_base": "https://from-config/v3"}})
    monkeypatch.setenv("OPENAI_API_BASE", "https://from-env/v3")
    bundle = resolve_credential_bundle(tmp_path)
    assert bundle.base_url == "https://from-env/v3"


def test_bundle_no_api_base_when_neither_configured(tmp_path, monkeypatch):
    """With neither litellm.api_base nor OPENAI_API_BASE, bundle.base_url is None."""
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_DIR", tmp_path / "global")
    _write_config(tmp_path, {"model": "deepseek/deepseek-v4-flash"})
    bundle = resolve_credential_bundle(tmp_path)
    assert bundle.base_url is None
