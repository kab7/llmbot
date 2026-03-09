import importlib
import sys


def _reload_config():
    sys.modules.pop("config", None)
    return importlib.import_module("config")


def test_config_reads_environment(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_API_ID", "123")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
    monkeypatch.setenv("TELEGRAM_PHONE", "+79991234567")
    monkeypatch.setenv("ADMIN_USER_ID", "77")
    monkeypatch.setenv("PRIMARY_LLM_URL", "https://example.ai/v1/chat/completions")
    monkeypatch.setenv("PRIMARY_LLM_MODEL", "provider/test-model")
    monkeypatch.setenv("PRIMARY_LLM_API_KEY", "primary-key")
    monkeypatch.setenv("FALLBACK_LLM_URL", "https://fallback.ai/v1")
    monkeypatch.setenv("FALLBACK_LLM_MODEL", "openrouter/free")
    monkeypatch.setenv("FALLBACK_LLM_TOKEN", "fallback-token")
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "17")
    monkeypatch.setenv("LLM_MAX_RETRIES", "4")
    monkeypatch.setenv("PRIMARY_FREE_MODEL_INTERVAL_SECONDS", "5")
    monkeypatch.setenv("PRIMARY_FREE_MODEL_429_BACKOFF_SECONDS", "13")
    monkeypatch.setenv("FALLBACK_FREE_MODEL_INTERVAL_SECONDS", "6")
    monkeypatch.setenv("FALLBACK_FREE_MODEL_429_BACKOFF_SECONDS", "14")

    cfg = _reload_config()

    assert cfg.TELEGRAM_BOT_TOKEN == "bot-token"
    assert cfg.TELEGRAM_API_ID == 123
    assert cfg.TELEGRAM_API_HASH == "hash"
    assert cfg.TELEGRAM_PHONE == "+79991234567"
    assert cfg.ADMIN_USER_ID == 77
    assert cfg.DEFAULT_LLM_URL == "https://example.ai/v1/chat/completions"
    assert cfg.DEFAULT_LLM_MODEL == "provider/test-model"
    assert cfg.DEFAULT_LLM_TOKEN == "primary-key"
    assert cfg.DEFAULT_FALLBACK_LLM_URL == "https://fallback.ai/v1"
    assert cfg.DEFAULT_FALLBACK_LLM_MODEL == "openrouter/free"
    assert cfg.DEFAULT_FALLBACK_LLM_TOKEN == "fallback-token"
    assert cfg.LLM_REQUEST_TIMEOUT_SECONDS == 17
    assert cfg.LLM_MAX_RETRIES == 4
    assert cfg.PRIMARY_FREE_MODEL_INTERVAL_SECONDS == 5
    assert cfg.PRIMARY_FREE_MODEL_429_BACKOFF_SECONDS == 13
    assert cfg.FALLBACK_FREE_MODEL_INTERVAL_SECONDS == 6
    assert cfg.FALLBACK_FREE_MODEL_429_BACKOFF_SECONDS == 14
    assert cfg.DEFAULT_LLM_URL.endswith("/chat/completions")
    assert "валидным JSON" in cfg.PARSER_PROMPT
    assert "историей Telegram чатов" in cfg.PROCESSOR_PROMPT


def test_config_default_primary_token_empty(monkeypatch):
    monkeypatch.delenv("PRIMARY_LLM_API_KEY", raising=False)
    cfg = _reload_config()
    assert isinstance(cfg.DEFAULT_LLM_TOKEN, str)


def test_config_get_config_issues_ok_and_optional(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_API_ID", "123")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
    monkeypatch.setenv("TELEGRAM_PHONE", "+79991234567")
    monkeypatch.setenv("ADMIN_USER_ID", "77")
    monkeypatch.setenv("PRIMARY_LLM_API_KEY", "")
    monkeypatch.setenv("FALLBACK_LLM_TOKEN", "")

    cfg = _reload_config()
    required, optional = cfg.get_config_issues()
    assert required == []
    assert optional and optional[0][0] == "PRIMARY_LLM_API_KEY/FALLBACK_LLM_TOKEN"


def test_config_get_config_issues_missing_and_invalid_ints(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_API_ID", "not-number")
    monkeypatch.setenv("TELEGRAM_API_HASH", "")
    monkeypatch.setenv("TELEGRAM_PHONE", "")
    monkeypatch.setenv("ADMIN_USER_ID", "bad-id")

    cfg = _reload_config()
    required, _ = cfg.get_config_issues()

    missing_keys = {key for key, _ in required}
    assert cfg.TELEGRAM_API_ID == 0
    assert cfg.ADMIN_USER_ID == 0
    assert {
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_PHONE",
        "ADMIN_USER_ID",
    } <= missing_keys
