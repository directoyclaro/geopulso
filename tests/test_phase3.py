import pytest

from geopulse.collectors.base import AccountManager
from geopulse.collectors.facebook import _dedupe, _proxy_config
from geopulse.config import Config
from geopulse.utils.retry import retry_call, safe_call


def test_retry_call_succeeds_after_failures():
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] < 3:
            raise ValueError("boom")
        return "ok"

    result = retry_call(flaky, retries=3, backoff_range=(0, 0))
    assert result == "ok"
    assert state["n"] == 3


def test_retry_call_raises_after_max():
    def always_fail():
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        retry_call(always_fail, retries=2, backoff_range=(0, 0))


def test_safe_call_returns_default():
    assert safe_call(lambda: 1 / 0, default="fallback") == "fallback"


def test_proxy_config_with_auth():
    cfg = _proxy_config("http://user:pass@host:8080")
    assert cfg == {"server": "http://host:8080", "username": "user", "password": "pass"}


def test_proxy_config_none():
    assert _proxy_config(None) is None


def test_dedupe_records():
    records = [
        {"url": "https://x/1", "text": "a"},
        {"url": "https://x/1", "text": "a"},
        {"url": "https://x/2", "text": "b"},
    ]
    assert len(_dedupe(records)) == 2


def test_collection_config_defaults():
    cfg = CollectionConfigSafe()
    assert cfg.max_posts_per_source >= 1
    assert isinstance(cfg.backoff_seconds, tuple)
    assert cfg.proxies == []


def test_account_manager_validate_structure():
    manager = AccountManager(Config())
    problems = manager.validate()
    assert isinstance(problems, dict)
    if manager.accounts:
        assert "__file__" not in problems


def CollectionConfigSafe():
    return Config().collection
