from openkb.config import (
    DEFAULT_CONFIG,
    get_extra_headers,
    load_config,
    resolve_extra_headers,
    save_config,
    set_extra_headers,
)


def test_default_config_keys():
    assert "model" in DEFAULT_CONFIG
    assert "language" in DEFAULT_CONFIG
    assert "pageindex_threshold" in DEFAULT_CONFIG


def test_default_config_values():
    assert DEFAULT_CONFIG["model"] == "gpt-5.4-mini"
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
    config = {"extra_headers": {
        "Editor-Version": "vscode/1.95.0",
        "Copilot-Integration-Id": "vscode-chat",
    }}
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
    config = {"extra_headers": {
        "Good": "value",
        "": "empty-key-skipped",
        "NoneValue": None,
        "ListValue": ["a"],
        123: "non-string-key-skipped",
    }}
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
