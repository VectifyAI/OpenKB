import logging
from pathlib import Path

import pytest

from openkb.config import (
    DEFAULT_CONFIG,
    GLOBAL_SCALAR_KEYS,
    VALID_INSERT_MODES,
    get_extra_headers,
    get_parallel_tool_calls,
    get_timeout,
    kb_root_dir,
    load_config,
    registered_kbs,
    resolve_concurrency,
    resolve_effective_config,
    resolve_extra_headers,
    resolve_init_kb_dir,
    resolve_insert_mode,
    resolve_litellm_settings,
    resolve_model_settings,
    resolve_parallel_tool_calls,
    resolve_timeout,
    save_config,
    save_global_config,
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


def test_concurrency_not_in_default_config():
    # Like the other optional tuning knobs (timeout, extra_headers,
    # parallel_tool_calls), concurrency stays out of DEFAULT_CONFIG —
    # resolve_concurrency reads it via .get(), so an absent key resolves to
    # None without relying on load_config's merge.
    assert "concurrency" not in DEFAULT_CONFIG


def test_load_concurrency_override(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("concurrency: 12\n", encoding="utf-8")
    assert load_config(config_path)["concurrency"] == 12


def test_resolve_concurrency_absent_is_none():
    assert resolve_concurrency({}) is None


def test_resolve_concurrency_valid_value():
    assert resolve_concurrency({"concurrency": 3}) == 3


def test_resolve_concurrency_rejects_bool(caplog):
    with caplog.at_level(logging.WARNING, logger="openkb.config"):
        result = resolve_concurrency({"concurrency": True})
    assert result is None
    assert "concurrency" in caplog.text


def test_resolve_concurrency_rejects_non_positive(caplog):
    with caplog.at_level(logging.WARNING, logger="openkb.config"):
        assert resolve_concurrency({"concurrency": 0}) is None
    assert "concurrency" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="openkb.config"):
        assert resolve_concurrency({"concurrency": -1}) is None
    assert "concurrency" in caplog.text


def test_resolve_concurrency_rejects_non_int():
    assert resolve_concurrency({"concurrency": "3"}) is None


def test_resolve_concurrency_none_is_silent(caplog):
    # Explicit null / absent is the normal "unset — caller applies its own
    # default, or omits the setting entirely" case — no warning.
    with caplog.at_level(logging.WARNING, logger="openkb.config"):
        assert resolve_concurrency({"concurrency": None}) is None
    assert caplog.text == ""


def test_resolve_insert_mode_absent_is_normal():
    assert resolve_insert_mode({}) == "normal"


def test_resolve_insert_mode_valid_values():
    assert set(VALID_INSERT_MODES) == {"normal", "fail-fast", "fail-at-end"}
    for mode in VALID_INSERT_MODES:
        assert resolve_insert_mode({"insert_mode": mode}) == mode


def test_resolve_insert_mode_rejects_invalid(caplog):
    with caplog.at_level(logging.WARNING, logger="openkb.config"):
        result = resolve_insert_mode({"insert_mode": "yolo"})
    assert result == "normal"
    assert "insert_mode" in caplog.text


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


# --- resolve_effective_config -------------------------------------------------


def _kb(tmp_path: Path) -> Path:
    (tmp_path / ".openkb").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def _isolated_global(monkeypatch, tmp_path):
    gdir = tmp_path / "global-config"
    gdir.mkdir()
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_DIR", gdir)
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_PATH", gdir / "global.yaml")
    return gdir


def test_effective_defaults_only(_isolated_global, tmp_path):
    eff, src = resolve_effective_config(_kb(tmp_path / "kb"))
    assert eff["model"] == DEFAULT_CONFIG["model"]
    assert eff["language"] == DEFAULT_CONFIG["language"]
    assert eff["pageindex_threshold"] == DEFAULT_CONFIG["pageindex_threshold"]
    assert src == {k: "default" for k in GLOBAL_SCALAR_KEYS}


def test_effective_global_only(_isolated_global, tmp_path):
    save_global_config({"model": "global-model", "known_kbs": ["/a"]})
    eff, src = resolve_effective_config(_kb(tmp_path / "kb"))
    assert eff["model"] == "global-model"
    assert src["model"] == "global"
    assert src["language"] == "default"


def test_effective_kb_override_wins_over_global(_isolated_global, tmp_path):
    save_global_config({"model": "global-model", "language": "fr"})
    kb = _kb(tmp_path / "kb")
    save_config(kb / ".openkb" / "config.yaml", {"model": "kb-model"})
    eff, src = resolve_effective_config(kb)
    assert eff["model"] == "kb-model"
    assert src["model"] == "kb"
    # language is global (KB silent on it)
    assert eff["language"] == "fr"
    assert src["language"] == "global"


def test_effective_three_layer_precedence(_isolated_global, tmp_path):
    save_global_config({"model": "global-model"})  # global sets only model
    kb = _kb(tmp_path / "kb")
    # kb sets only threshold
    save_config(kb / ".openkb" / "config.yaml", {"pageindex_threshold": 7})
    eff, src = resolve_effective_config(kb)
    assert (eff["model"], src["model"]) == ("global-model", "global")
    assert (eff["pageindex_threshold"], src["pageindex_threshold"]) == (7, "kb")
    assert (eff["language"], src["language"]) == (DEFAULT_CONFIG["language"], "default")


def test_effective_scalar_null_in_kb_inherits_not_clobbers(_isolated_global, tmp_path):
    save_global_config({"model": "global-model"})
    kb = _kb(tmp_path / "kb")
    save_config(kb / ".openkb" / "config.yaml", {"model": None})
    eff, src = resolve_effective_config(kb)
    assert eff["model"] == "global-model"  # null means inherit, not None
    assert src["model"] == "global"


def test_effective_preserves_non_scalar_kb_keys_including_meaningful_null(
    _isolated_global, tmp_path
):
    kb = _kb(tmp_path / "kb")
    save_config(
        kb / ".openkb" / "config.yaml",
        {"litellm": {"drop_params": True}, "parallel_tool_calls": None},
    )
    eff, _ = resolve_effective_config(kb)
    assert eff["litellm"] == {"drop_params": True}
    # a non-scalar null is meaningful (Bedrock escape hatch) and must survive
    assert "parallel_tool_calls" in eff
    assert eff["parallel_tool_calls"] is None


def test_effective_config_degrades_on_non_dict_yaml(_isolated_global, tmp_path):
    """A malformed global.yaml OR KB config.yaml that parses to a list/scalar
    (not a mapping) must degrade to defaults, not raise AttributeError on every
    command's hot path (the global case is KB-wide). Mirrors the empty-file
    tolerance (`safe_load(...) or {}`)."""
    # global.yaml parses to a bare list; KB config.yaml to a bare scalar string.
    (_isolated_global / "global.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
    kb = _kb(tmp_path / "kb")
    (kb / ".openkb" / "config.yaml").write_text("just-a-string\n", encoding="utf-8")

    eff, src = resolve_effective_config(kb)

    assert eff["model"] == DEFAULT_CONFIG["model"]
    assert eff["language"] == DEFAULT_CONFIG["language"]
    assert eff["pageindex_threshold"] == DEFAULT_CONFIG["pageindex_threshold"]
    assert src == {k: "default" for k in GLOBAL_SCALAR_KEYS}


def test_converter_uses_global_threshold(_isolated_global, tmp_path, monkeypatch):
    """A global.yaml pageindex_threshold drives convert_document's long/short
    -doc routing when the KB config is silent on it — exercised through the
    real production call site (``convert_document``), not just the resolver
    in isolation. This proves ``convert_document`` actually consumes
    ``resolve_effective_config(kb_dir)`` rather than, say, an empty dict."""
    import pymupdf

    import openkb.converter as conv

    # Global threshold of 1: even a single-page PDF counts as "long". The
    # default threshold (20) would NOT trigger long-doc routing for 1 page,
    # so this value can only reach convert_document via the resolver.
    save_global_config({"pageindex_threshold": 1})

    kb_dir = _kb(tmp_path / "kb")
    real_resolve = conv.resolve_effective_config
    calls: list[Path] = []

    def _spy_resolve(kb_dir_arg):
        calls.append(kb_dir_arg)
        return real_resolve(kb_dir_arg)

    monkeypatch.setattr(conv, "resolve_effective_config", _spy_resolve)

    src = tmp_path / "doc.pdf"
    pdf = pymupdf.open()
    pdf.new_page()
    pdf.save(str(src))
    pdf.close()

    result = conv.convert_document(src, kb_dir)

    # The resolver seam was actually invoked by convert_document, with this KB.
    assert calls == [kb_dir]
    # And the threshold it returned actually drove the routing decision: a
    # 1-page PDF only takes the long-doc branch if the *global* threshold (1)
    # was used instead of the default (20).
    assert result.is_long_doc is True


def test_query_agent_uses_global_language(_isolated_global, tmp_path, monkeypatch):
    """run_query resolves its language via resolve_effective_config(kb_dir) —
    exercised through the real production call site (``run_query``), not by
    calling the resolver directly (that would only re-test Task 1's resolver).

    Spies on ``resolve_effective_config`` as invoked *by* run_query and records
    the ``kb_dir`` it was called with, then confirms the global-only language
    value actually reaches ``build_query_agent``. If agent/query.py were
    reverted to ``load_config(openkb_dir / "config.yaml")``/``{}``, the spy
    would never fire (or ``load_config`` on a KB with no config.yaml would
    yield DEFAULT_CONFIG's language "en") and this test would fail.
    """
    import asyncio

    import openkb.agent.query as q
    import openkb.config as cfg

    save_global_config({"language": "de"})
    kb = _kb(tmp_path / "kb")

    real_resolve = cfg.resolve_effective_config
    calls: list[Path] = []

    def _spy_resolve(kb_dir_arg):
        calls.append(kb_dir_arg)
        return real_resolve(kb_dir_arg)

    monkeypatch.setattr(cfg, "resolve_effective_config", _spy_resolve)

    captured = {}

    def _fake_build(wiki_root, model, *, language, bundle=None):
        captured["language"] = language
        raise RuntimeError("stop-after-build")  # short-circuit before any LLM call

    monkeypatch.setattr(q, "build_query_agent", _fake_build)

    with pytest.raises(RuntimeError, match="stop-after-build"):
        asyncio.run(q.run_query("q", kb, "some-model", stream=False))

    # The resolver seam was actually invoked by run_query, with this KB dir.
    assert calls == [kb]
    # And the language it resolved actually reached build_query_agent.
    assert captured["language"] == "de"


# --- scalar-whitelist parity --------------------------------------------------


def test_global_scalar_keys_match_api_writable_keys():
    # GLOBAL_SCALAR_KEYS (resolver) and _KB_CONFIG_WRITABLE_KEYS (config PATCH)
    # must stay identical: a scalar added to only one would let the global PATCH
    # persist a key the resolver ignores (or vice-versa). Guard the drift here
    # rather than merging the two sources (lower-risk).
    from openkb.api_models import _KB_CONFIG_WRITABLE_KEYS

    assert set(GLOBAL_SCALAR_KEYS) == set(_KB_CONFIG_WRITABLE_KEYS)


# --- kb_root_dir precedence / resolve_init_kb_dir / registered_kbs ------------


def test_kb_root_dir_env_wins_over_global(_isolated_global, monkeypatch, tmp_path):
    save_global_config({"kb_root": str(tmp_path / "from-global")})
    monkeypatch.setenv("OPENKB_KB_ROOT", str(tmp_path / "from-env"))
    assert kb_root_dir() == (tmp_path / "from-env").resolve()


def test_kb_root_dir_global_when_no_env(_isolated_global, monkeypatch, tmp_path):
    monkeypatch.delenv("OPENKB_KB_ROOT", raising=False)
    save_global_config({"kb_root": str(tmp_path / "from-global")})
    assert kb_root_dir() == (tmp_path / "from-global").resolve()


def test_kb_root_dir_default_when_neither(_isolated_global, monkeypatch):
    monkeypatch.delenv("OPENKB_KB_ROOT", raising=False)
    # global.yaml is absent under _isolated_global -> the built-in default root.
    assert kb_root_dir() == (_isolated_global / "kbs").resolve()


def test_kb_root_dir_blank_global_falls_through_to_default(_isolated_global, monkeypatch):
    monkeypatch.delenv("OPENKB_KB_ROOT", raising=False)
    save_global_config({"kb_root": "   "})  # whitespace-only == unset
    assert kb_root_dir() == (_isolated_global / "kbs").resolve()


def test_resolve_init_kb_dir_default_uses_root(_isolated_global, monkeypatch):
    monkeypatch.delenv("OPENKB_KB_ROOT", raising=False)
    assert resolve_init_kb_dir("my-kb") == (_isolated_global / "kbs" / "my-kb").resolve()


def test_resolve_init_kb_dir_absolute_path_used_verbatim(tmp_path):
    custom = tmp_path / "elsewhere" / "kb"
    assert resolve_init_kb_dir("my-kb", str(custom)) == custom.resolve()


def test_resolve_init_kb_dir_rejects_relative_path():
    import pytest as _pytest

    with _pytest.raises(ValueError, match="absolute"):
        resolve_init_kb_dir("my-kb", "relative/dir")


def test_registered_kbs_unions_and_dedupes_by_path(_isolated_global, tmp_path):
    kb_a = tmp_path / "a"
    kb_b = tmp_path / "b"
    # kb_a is both aliased AND in known_kbs; aliases are visited first, so it
    # keeps its alias name and is not double-counted. kb_b (bare known_kbs) uses
    # its directory name.
    save_global_config(
        {
            "kb_aliases": {"alias-a": str(kb_a)},
            "known_kbs": [str(kb_a), str(kb_b)],
        }
    )
    result = dict(registered_kbs())
    assert result == {"alias-a": kb_a.resolve(), "b": kb_b.resolve()}


def test_validate_kb_name_accepts_unicode_and_rejects_unsafe():
    from openkb.config import validate_kb_name

    # Non-ASCII (e.g. CJK) letters are valid so a Chinese-named KB is addressable.
    assert validate_kb_name("笔记") == "笔记"
    assert validate_kb_name("my-kb_2") == "my-kb_2"
    # Path separators, dots, and spaces stay rejected (traversal-safe).
    for bad in ("a/b", "a.b", "a b", "../x", ""):
        with pytest.raises(ValueError):
            validate_kb_name(bad)


def test_resolve_kb_alias_resolves_known_kbs_by_basename(_isolated_global, tmp_path):
    from openkb.config import resolve_kb_alias

    # A KB registered in known_kbs (not an alias) that lives outside kb_root and
    # has a non-ASCII directory name must resolve by its basename — this is what
    # the KB list surfaces and what the frontend addresses it by.
    kb = tmp_path / "笔记"
    kb.mkdir()
    save_global_config({"known_kbs": [str(kb)]})
    assert resolve_kb_alias("笔记") == kb.resolve()


def _make_kb_dir(path: Path) -> Path:
    """Create the minimal KB shape (`.openkb` + `wiki`) recognized as a KB dir."""
    (path / ".openkb").mkdir(parents=True)
    (path / "wiki").mkdir(parents=True)
    return path


def test_resolve_kb_alias_root_child_wins_over_off_root_known_kbs(
    _isolated_global, monkeypatch, tmp_path
):
    from openkb.config import resolve_kb_alias

    # A root-level KB "notes" and an off-root known_kbs KB with the SAME basename.
    root = tmp_path / "root"
    monkeypatch.setenv("OPENKB_KB_ROOT", str(root))
    root_notes = _make_kb_dir(root / "notes")
    off_notes = tmp_path / "x" / "notes"
    off_notes.mkdir(parents=True)
    save_global_config({"known_kbs": [str(off_notes)]})

    # The root child WINS — the off-root KB must never shadow it (regression:
    # the old known_kbs basename loop ran first and returned the off-root path).
    assert resolve_kb_alias("notes") == root_notes.resolve()


def test_registered_kbs_hides_bare_known_kbs_colliding_with_root_child(
    _isolated_global, monkeypatch, tmp_path, caplog
):
    root = tmp_path / "root"
    monkeypatch.setenv("OPENKB_KB_ROOT", str(root))
    _make_kb_dir(root / "notes")
    off_notes = tmp_path / "x" / "notes"
    off_notes.mkdir(parents=True)
    save_global_config({"known_kbs": [str(off_notes)]})

    with caplog.at_level(logging.WARNING, logger="openkb.config"):
        result = registered_kbs()

    # The colliding off-root "notes" is HIDDEN (root child reserves the name),
    # logged rather than emitted as a duplicate/shadowing entry.
    assert all(name != "notes" for name, _ in result)
    assert "hidden" in caplog.text


def test_resolve_kb_alias_degrades_on_non_mapping_global_yaml(
    _isolated_global, monkeypatch, tmp_path
):
    from openkb.config import resolve_kb_alias

    # A hand-corrupted global.yaml (bare list) must not 500 resolution: the
    # loader coerces it to {} so resolve falls back to <kb_root>/<name>.
    monkeypatch.delenv("OPENKB_KB_ROOT", raising=False)
    (_isolated_global / "global.yaml").write_text("- a\n- b\n", encoding="utf-8")
    assert resolve_kb_alias("mykb") == (_isolated_global / "kbs" / "mykb").resolve()


# --- delete_kb / unregister_kb -----------------------------------------------


def test_delete_kb_physical_removal_and_unregister(_isolated_global, tmp_path):
    from openkb.config import register_kb_alias, registered_kbs
    from openkb.kb_admin import delete_kb

    kb = _make_kb_dir(tmp_path / "doomed")
    register_kb_alias("doomed", kb)
    assert any(n == "doomed" for n, _ in registered_kbs())

    delete_kb(kb)
    assert not kb.exists()  # directory physically removed
    assert all(n != "doomed" for n, _ in registered_kbs())  # unregistered


def test_delete_kb_refuses_non_kb_directory(_isolated_global, tmp_path):
    from openkb.kb_admin import delete_kb

    plain = tmp_path / "not-a-kb"
    plain.mkdir()
    (plain / "important.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(ValueError, match="not a knowledge base"):
        delete_kb(plain)
    assert (plain / "important.txt").exists()  # refused — nothing removed


def test_delete_kb_tolerates_ghost_registry_entry(_isolated_global, tmp_path):
    from openkb.config import register_kb_alias, registered_kbs
    from openkb.kb_admin import delete_kb

    ghost = tmp_path / "ghost"  # registered but its directory never existed
    register_kb_alias("ghost", ghost)
    assert any(n == "ghost" for n, _ in registered_kbs())

    delete_kb(ghost)  # no dir to rmtree → just unregister, no error
    assert all(n != "ghost" for n, _ in registered_kbs())
