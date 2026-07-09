import importlib
import os

import pytest


@pytest.fixture
def reload_config(monkeypatch):
    def _reload(**env):
        monkeypatch.setenv("BOT_TOKEN", "test-token")
        monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:test@localhost:55432/animebot_test")
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        import config
        return importlib.reload(config)
    return _reload


def test_admin_ids_parses_comma_separated(reload_config):
    config = reload_config(ADMIN_IDS="123456789, 42")
    assert config.ADMIN_IDS == {123456789, 42}


def test_admin_ids_defaults_to_empty(reload_config):
    config = reload_config()
    assert config.ADMIN_IDS == set()


def test_max_cache_bytes_default_is_small_fixed_value(reload_config):
    config = reload_config()
    assert config.MAX_CACHE_BYTES == 5 * 1024**3


def test_database_url_required(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import config
    with pytest.raises(RuntimeError):
        importlib.reload(config)
