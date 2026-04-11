"""Tests for storage_backend config option."""
from __future__ import annotations

from pathlib import Path

from openkb.config import DEFAULT_CONFIG, load_config, save_config


def test_default_config_has_storage_backend():
    """DEFAULT_CONFIG should include storage_backend key."""
    assert "storage_backend" in DEFAULT_CONFIG


def test_default_storage_backend_is_sqlite():
    """Default storage_backend should be 'sqlite'."""
    assert DEFAULT_CONFIG["storage_backend"] == "sqlite"


def test_load_config_includes_storage_backend(tmp_path):
    """load_config should return storage_backend from config file."""
    config_path = tmp_path / "config.yaml"
    save_config(config_path, {"storage_backend": "json"})
    loaded = load_config(config_path)
    assert loaded["storage_backend"] == "json"


def test_storage_backend_valid_values(tmp_path):
    """storage_backend should accept 'sqlite' or 'json'."""
    config_path = tmp_path / "config.yaml"
    
    save_config(config_path, {"storage_backend": "sqlite"})
    loaded = load_config(config_path)
    assert loaded["storage_backend"] == "sqlite"
    
    save_config(config_path, {"storage_backend": "json"})
    loaded = load_config(config_path)
    assert loaded["storage_backend"] == "json"
