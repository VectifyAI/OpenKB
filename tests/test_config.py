import logging

from openkb.config import (
    DEFAULT_CONFIG,
    get_extra_headers,
    get_timeout,
    load_config,
    resolve_extra_headers,
    resolve_litellm_settings,
    resolve_timeout,
    save_config,
    set_extra_headers,
    set_timeout,
)


def test_default_config_keys():
    assert "model" in DEFAULT_CONFIG
    assert "language" in DEFAULT_CONFIG
    assert "pageindex_threshold" in DEFAULT_CONFIG


def test_default_config_values():
    assert DEFAULT_CONFIG["model"] == "gpt-5.4"
    assert DEFAULT_CONFIG["language"] == "en"
    assert DEFAULT_CONFIG["pageindex_threshold"] == 20


def test_load_missing_file_returns_defaults(tmp_path):
    missing = tmp_path / "nonexistent" / "config.yaml"
    config = load_config(missing)
    assert config == DEFAULT_CONFIG


def test_save_creates_parent_dirs(tmp_path):
    config_path = tmp_path / "nested" / "dir" / "config.yaml"
    save_config(config_path, DEFAULT_CONFIG)
    assert config_path.exists()


def test_save_load_roundtrip(tmp_path):
    config_path = tmp_path / "config.yaml"
    custom = {"model": "gpt-3.5-turbo", "language": "fr"}
    save_config(config_path, custom)
    loaded = load_config(config_path)
    # Custom values override defaults
    assert loaded["model"] == "gpt-3.5-turbo"
    assert loaded["language"] == "fr"
    # Defaults fill in missing keys
    assert loaded["pageindex_threshold"] == DEFAULT_CONFIG["pageindex_threshold"]


def test_load_overrides_defaults(tmp_path):
    config_path = tmp_path / "config.yaml"
    save_config(config_path, {"model": "claude-3", "pageindex_threshold": 100})
    loaded = load_config(config_path)
    assert loaded["model"] == "claude-3"
    assert loaded["pageindex_threshold"] == 100
    # Non-overridden defaults still present
    assert loaded["language"] == "en"


# --- extra_headers -----------------------------------------------------------


def test_resolve_extra_headers_absent_returns_empty():
    assert resolve_extra_headers({}) == {}


def test_resolve_extra_headers_valid_mapping():
    config = {
        "extra_headers": {
            "Editor-Version": "vscode/1.95.0",
            "Copilot-Integration-Id": "vscode-chat",
        }
    }
    assert resolve_extra_headers(config) == {
        "Editor-Version": "vscode/1.95.0",
        "Copilot-Integration-Id": "vscode-chat",
    }


def test_resolve_extra_headers_stringifies_scalar_values():
    # YAML may parse version-ish values as numbers.
    config = {"extra_headers": {"X-Api-Version": 2024, "X-Ratio": 1.5}}
    assert resolve_extra_headers(config) == {"X-Api-Version": "2024", "X-Ratio": "1.5"}


def test_resolve_extra_headers_non_mapping_ignored():
    assert resolve_extra_headers({"extra_headers": ["Editor-Version: x"]}) == {}
    assert resolve_extra_headers({"extra_headers": "Editor-Version: x"}) == {}


def test_resolve_extra_headers_skips_bad_entries():
    config = {
        "extra_headers": {
            "Good": "value",
            "": "empty-key-skipped",
            "NoneValue": None,
            "ListValue": ["a"],
            123: "non-string-key-skipped",
        }
    }
    assert resolve_extra_headers(config) == {"Good": "value"}


def test_extra_headers_stash_roundtrip_and_isolation():
    set_extra_headers({"A": "1"})
    got = get_extra_headers()
    assert got == {"A": "1"}
    # Mutating the returned copy must not affect the stash.
    got["B"] = "2"
    assert get_extra_headers() == {"A": "1"}
    set_extra_headers({})
    assert get_extra_headers() == {}


# --- timeout -----------------------------------------------------------------


def test_resolve_timeout_absent_returns_none():
    assert resolve_timeout({}) is None


def test_resolve_timeout_int_and_float():
    assert resolve_timeout({"timeout": 1200}) == 1200.0
    assert resolve_timeout({"timeout": 0.5}) == 0.5


def test_resolve_timeout_numeric_string_coerced():
    assert resolve_timeout({"timeout": "1200"}) == 1200.0


def test_resolve_timeout_rejects_non_positive():
    assert resolve_timeout({"timeout": 0}) is None
    assert resolve_timeout({"timeout": -10}) is None


def test_resolve_timeout_rejects_bool():
    # bool is a subclass of int; True/False are not durations.
    assert resolve_timeout({"timeout": True}) is None


def test_resolve_timeout_rejects_non_numeric():
    assert resolve_timeout({"timeout": "soon"}) is None
    assert resolve_timeout({"timeout": [1200]}) is None


def test_resolve_timeout_rejects_nan_and_inf():
    # nan/inf pass a naive `<= 0` check; YAML's .nan/.inf yield real floats.
    assert resolve_timeout({"timeout": float("inf")}) is None
    assert resolve_timeout({"timeout": float("nan")}) is None
    assert resolve_timeout({"timeout": "inf"}) is None
    assert resolve_timeout({"timeout": "nan"}) is None


def test_timeout_stash_roundtrip_and_reset():
    set_timeout(1200.0)
    assert get_timeout() == 1200.0
    set_timeout(None)
    assert get_timeout() is None


def test_resolve_litellm_settings_absent_returns_empty():
    assert resolve_litellm_settings({}) == {}


def test_resolve_litellm_settings_passes_mapping_through_verbatim():
    # Values are forwarded as-is — no validation or coercion.
    config = {"litellm": {"drop_params": True, "num_retries": 3, "ssl_verify": False}}
    assert resolve_litellm_settings(config) == {
        "drop_params": True,
        "num_retries": 3,
        "ssl_verify": False,
    }


def test_resolve_litellm_settings_non_mapping_ignored():
    assert resolve_litellm_settings({"litellm": ["drop_params"]}) == {}
    assert resolve_litellm_settings({"litellm": "drop_params=true"}) == {}
    assert resolve_litellm_settings({"litellm": True}) == {}


def test_resolve_litellm_settings_drops_non_string_keys():
    assert resolve_litellm_settings({"litellm": {5: "x", "drop_params": True}}) == {
        "drop_params": True
    }


def test_resolve_litellm_settings_warns_on_non_mapping(caplog):
    with caplog.at_level(logging.WARNING, logger="openkb.config"):
        assert resolve_litellm_settings({"litellm": ["drop_params"]}) == {}
    assert "must be a mapping" in caplog.text


def test_resolve_litellm_settings_warns_on_non_string_key(caplog):
    with caplog.at_level(logging.WARNING, logger="openkb.config"):
        resolve_litellm_settings({"litellm": {5: "x", "drop_params": True}})
    assert "non-string key" in caplog.text
