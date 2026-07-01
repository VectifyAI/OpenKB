"""User-provided ``litellm:`` settings are applied verbatim onto the litellm module.

``litellm:`` in config.yaml is a free-form passthrough of LiteLLM module-level
globals (``drop_params``, ``modify_params``, ``ssl_verify``, ...).
``cli._apply_litellm_settings`` assigns each key onto the ``litellm`` module so
it takes effect process-wide for every call routed through LiteLLM. It warns,
without blocking, for a key the installed LiteLLM doesn't expose (typo / version
mismatch) and refuses to overwrite a LiteLLM *function*. Settings are sticky:
applied, never reset — see ``test_apply_is_sticky_not_reset``.
"""

from __future__ import annotations

import logging

import litellm
import pytest

from openkb.cli import _KNOWN_PROVIDER_KEYS, _apply_litellm_settings, _setup_llm_key


@pytest.fixture(autouse=True)
def _restore_litellm_globals():
    """Snapshot and restore the litellm globals these tests mutate.

    setattr on the litellm module is process-wide, so without this an applied
    value would leak into every later test sharing the interpreter. ``api_key``
    is included because the end-to-end test exercises full ``_setup_llm_key``.
    """
    keys = ("drop_params", "modify_params", "ssl_verify", "api_key")
    saved = {k: getattr(litellm, k) for k in keys}
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(litellm, k, v)


def test_apply_sets_known_global_verbatim():
    litellm.drop_params = False
    _apply_litellm_settings({"drop_params": True})
    assert litellm.drop_params is True


def test_apply_forwards_values_as_is_no_coercion():
    _apply_litellm_settings({"ssl_verify": False, "modify_params": True})
    assert litellm.ssl_verify is False
    assert litellm.modify_params is True


def test_apply_skips_unknown_key_with_warning(caplog):
    bogus = "definitely_not_a_litellm_setting_xyz"
    assert not hasattr(litellm, bogus)
    with caplog.at_level(logging.WARNING, logger="openkb.cli"):
        _apply_litellm_settings({bogus: 123})
    # Not silently created as a dead attribute…
    assert not hasattr(litellm, bogus)
    # …and the user is told (on the logger, like the sibling resolvers).
    assert bogus in caplog.text
    assert "ignoring it" in caplog.text


def test_apply_refuses_to_overwrite_callable(caplog):
    """A key naming a LiteLLM *function* (hasattr is True) must NOT be clobbered
    — overwriting litellm.completion with a scalar would brick every later call.
    """
    assert callable(litellm.completion)
    with caplog.at_level(logging.WARNING, logger="openkb.cli"):
        _apply_litellm_settings({"completion": 5})
    assert callable(litellm.completion)  # untouched
    assert "completion" in caplog.text
    assert "function" in caplog.text


def test_apply_applies_known_even_when_another_key_is_unknown():
    litellm.drop_params = False
    _apply_litellm_settings({"nope_not_real_xyz": 1, "drop_params": True})
    assert litellm.drop_params is True


def test_apply_empty_is_noop():
    litellm.drop_params = False
    _apply_litellm_settings({})
    assert litellm.drop_params is False


def test_apply_is_sticky_not_reset():
    """Documented contract: settings are applied, never reset. Applying {} after
    a real setting leaves the earlier value in place (it does NOT revert to the
    LiteLLM default) — unlike timeout/extra_headers. Pins the intentional
    process-wide stickiness so a future 'reset on empty' change is caught.
    """
    litellm.drop_params = False
    _apply_litellm_settings({"drop_params": True})
    _apply_litellm_settings({})  # a later config without a litellm: block
    assert litellm.drop_params is True  # stays set, not reset to default


def test_setup_llm_key_applies_litellm_block_from_config(tmp_path, monkeypatch):
    """End-to-end: a ``litellm:`` block in config.yaml lands on the module the
    next time any command runs _setup_llm_key.

    Env is cleared so full ``_setup_llm_key`` doesn't set litellm.api_key /
    provider env vars from a key in the dev environment — which would leak
    process-wide state into other tests.
    """
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    for key in _KNOWN_PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)

    openkb_dir = tmp_path / ".openkb"
    openkb_dir.mkdir(parents=True)
    (openkb_dir / "config.yaml").write_text(
        "model: gpt-4o-mini\nlitellm:\n  drop_params: true\n", encoding="utf-8"
    )
    litellm.drop_params = False
    _setup_llm_key(tmp_path)
    assert litellm.drop_params is True


def _write_kb_config(tmp_path, body: str):
    openkb_dir = tmp_path / ".openkb"
    openkb_dir.mkdir(parents=True)
    (openkb_dir / "config.yaml").write_text(body, encoding="utf-8")


def _isolate_env(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    for key in _KNOWN_PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_litellm_block_routes_timeout_and_extra_headers_per_call(tmp_path, monkeypatch):
    """`timeout` / `extra_headers` inside the litellm: block route to the
    per-call stashes (not litellm module globals); the rest stay globals.
    """
    from openkb.config import get_extra_headers, get_timeout

    _isolate_env(monkeypatch)
    _write_kb_config(
        tmp_path,
        "model: gpt-4o-mini\n"
        "litellm:\n"
        "  timeout: 1200\n"
        "  drop_params: true\n"
        "  extra_headers:\n"
        "    Editor-Version: vscode/1.95.0\n",
    )
    litellm.drop_params = False
    _setup_llm_key(tmp_path)
    assert get_timeout() == 1200.0
    assert get_extra_headers() == {"Editor-Version": "vscode/1.95.0"}
    assert litellm.drop_params is True
    # timeout was routed per-call, NOT setattr'd onto the module: litellm.timeout
    # is still its function (a global 1200.0 would have replaced it).
    assert callable(litellm.timeout)


def test_litellm_block_timeout_wins_over_legacy_toplevel(tmp_path, monkeypatch):
    """The litellm: block value wins over the legacy top-level key."""
    from openkb.config import get_timeout

    _isolate_env(monkeypatch)
    _write_kb_config(
        tmp_path,
        "model: gpt-4o-mini\n"
        "timeout: 30\n"  # legacy top-level
        "litellm:\n"
        "  timeout: 1200\n",  # canonical — wins
    )
    _setup_llm_key(tmp_path)
    assert get_timeout() == 1200.0


def test_legacy_toplevel_timeout_still_works(tmp_path, monkeypatch):
    """Back-compat: a top-level `timeout:` (no litellm: block) is still honored."""
    from openkb.config import get_timeout

    _isolate_env(monkeypatch)
    _write_kb_config(tmp_path, "model: gpt-4o-mini\ntimeout: 900\n")
    _setup_llm_key(tmp_path)
    assert get_timeout() == 900.0


def test_litellm_block_extra_headers_win_over_legacy_toplevel(tmp_path, monkeypatch):
    """Symmetric with the timeout precedence test: a litellm: block extra_headers
    replaces the legacy top-level extra_headers.
    """
    from openkb.config import get_extra_headers

    _isolate_env(monkeypatch)
    _write_kb_config(
        tmp_path,
        "model: gpt-4o-mini\n"
        "extra_headers:\n"
        "  X-Top: toplevel\n"
        "litellm:\n"
        "  extra_headers:\n"
        "    X-Block: blockval\n",
    )
    _setup_llm_key(tmp_path)
    assert get_extra_headers() == {"X-Block": "blockval"}


def test_litellm_block_empty_extra_headers_clears_legacy(tmp_path, monkeypatch):
    """Regression: an explicit empty `litellm: {extra_headers: {}}` CLEARS the
    legacy top-level headers, rather than silently reverting to them.
    """
    from openkb.config import get_extra_headers

    _isolate_env(monkeypatch)
    _write_kb_config(
        tmp_path,
        "model: gpt-4o-mini\nextra_headers:\n  X-Top: toplevel\nlitellm:\n  extra_headers: {}\n",
    )
    _setup_llm_key(tmp_path)
    assert get_extra_headers() == {}
