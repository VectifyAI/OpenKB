import logging

from openkb.config import (
    DEFAULT_CONFIG,
    get_extra_headers,
    get_parallel_tool_calls,
    get_timeout,
    load_config,
    resolve_extra_headers,
    resolve_litellm_settings,
    resolve_model_settings,
    resolve_parallel_tool_calls,
    resolve_timeout,
    save_config,
    set_extra_headers,
    set_parallel_tool_calls,
    set_timeout,
)

# --- parallel_tool_calls ------------------------------------------------------
#
# (value, was_explicit) distinguishes "not configured" (each agent uses its own
# default) from an explicit true/false/null (overrides every agent uniformly).


def test_parallel_tool_calls_not_in_default_config():
    # No single default fits every agent (see module docstring above), so this
    # key is intentionally absent from DEFAULT_CONFIG — load_config's merge
    # must not mask "the user's config.yaml doesn't mention this key".
    assert "parallel_tool_calls" not in DEFAULT_CONFIG


def test_resolve_parallel_tool_calls_absent_is_unset():
    assert resolve_parallel_tool_calls({}) == (None, False)


def test_resolve_parallel_tool_calls_explicit_bools():
    assert resolve_parallel_tool_calls({"parallel_tool_calls": True}) == (True, True)
    assert resolve_parallel_tool_calls({"parallel_tool_calls": False}) == (False, True)


def test_resolve_parallel_tool_calls_null_means_omit(caplog):
    # Explicit null = "don't send the param" (provider default). This is the
    # escape hatch for Amazon Bedrock, and is silent (not an invalid value) —
    # explicit and distinct from "absent" even though both currently carry a
    # value of None; was_explicit is what tells them apart.
    with caplog.at_level(logging.WARNING, logger="openkb.config"):
        assert resolve_parallel_tool_calls({"parallel_tool_calls": None}) == (None, True)
    assert caplog.text == ""


def test_resolve_parallel_tool_calls_rejects_non_bool(caplog):
    # An invalid value (not true/false/null) degrades to the one value known
    # to never break any provider — omit the setting — rather than to a fixed
    # bool that could reproduce the exact failure (e.g. Amazon Bedrock) the
    # user may have been trying to escape via this exact key.
    with caplog.at_level(logging.WARNING, logger="openkb.config"):
        assert resolve_parallel_tool_calls({"parallel_tool_calls": "true"}) == (None, True)
    assert "parallel_tool_calls" in caplog.text


def test_parallel_tool_calls_stash_roundtrip():
    set_parallel_tool_calls(False, True)
    assert get_parallel_tool_calls() == (False, True)
    set_parallel_tool_calls(True, True)
    assert get_parallel_tool_calls() == (True, True)
    set_parallel_tool_calls(None, True)
    assert get_parallel_tool_calls() == (None, True)
    set_parallel_tool_calls(None, False)
    assert get_parallel_tool_calls() == (None, False)


def test_parallel_tool_calls_stash_default_is_unset():
    # The raw stash default must mean "not configured", matching an absent key,
    # so an agent built before _setup_llm_key runs defers to its own default.
    set_parallel_tool_calls(None, False)
    assert get_parallel_tool_calls() == resolve_parallel_tool_calls({})


# --- resolve_model_settings ---------------------------------------------------


def test_resolve_model_settings_uses_own_default_when_unset():
    set_extra_headers({})
    set_timeout(None)
    set_parallel_tool_calls(None, False)
    assert resolve_model_settings() == {
        "extra_headers": None,
        "extra_args": None,
        "parallel_tool_calls": False,  # the function's own default
    }
    assert resolve_model_settings(default_parallel_tool_calls=None) == {
        "extra_headers": None,
        "extra_args": None,
        "parallel_tool_calls": None,
    }
    assert resolve_model_settings(default_parallel_tool_calls=True) == {
        "extra_headers": None,
        "extra_args": None,
        "parallel_tool_calls": True,
    }


def test_resolve_model_settings_explicit_value_overrides_every_default():
    # An explicit config choice always wins over whatever default a specific
    # caller would otherwise apply — the whole point of the escape hatch is
    # that it works uniformly, regardless of which agent is asking.
    set_extra_headers({"X-A": "1"})
    set_timeout(1200.0)
    set_parallel_tool_calls(None, True)  # explicit null: omit, for everyone
    for default in (False, True, None):
        assert resolve_model_settings(default_parallel_tool_calls=default) == {
            "extra_headers": {"X-A": "1"},
            "extra_args": {"timeout": 1200.0},
            "parallel_tool_calls": None,
        }

    set_parallel_tool_calls(True, True)  # explicit true: allow parallel, for everyone
    for default in (False, True, None):
        assert (
            resolve_model_settings(default_parallel_tool_calls=default)["parallel_tool_calls"]
            is True
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
