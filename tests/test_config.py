import importlib
import sys


def _reload_config():
    sys.modules.pop("config", None)
    return importlib.import_module("config")


def test_config_reads_environment(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda: False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_API_ID", "123")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
    monkeypatch.setenv("TELEGRAM_PHONE", "+79991234567")
    monkeypatch.setenv("ADMIN_USER_ID", "77")
    monkeypatch.setenv("CHATGPT_CREDENTIALS_FILE", "/data/oauth.json")
    monkeypatch.setenv("CHATGPT_MODEL", "gpt-test")
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "17")
    monkeypatch.setenv("COMBINED_LLM_REQUEST_TIMEOUT_SECONDS", "71")
    monkeypatch.setenv("LLM_MAX_RETRIES", "4")

    cfg = _reload_config()

    assert cfg.TELEGRAM_BOT_TOKEN == "bot-token"
    assert cfg.TELEGRAM_API_ID == 123
    assert cfg.TELEGRAM_API_HASH == "hash"
    assert cfg.TELEGRAM_PHONE == "+79991234567"
    assert cfg.ADMIN_USER_ID == 77
    assert cfg.CHATGPT_CREDENTIALS_FILE == "/data/oauth.json"
    assert cfg.CHATGPT_MODEL == "gpt-test"
    assert cfg.LLM_REQUEST_TIMEOUT_SECONDS == 17
    assert cfg.COMBINED_LLM_REQUEST_TIMEOUT_SECONDS == 71
    assert cfg.LLM_MAX_RETRIES == 4
    assert "валидным JSON" in cfg.PARSER_PROMPT
    assert "историей Telegram чатов" in cfg.PROCESSOR_PROMPT


def test_config_defaults_to_auto_chatgpt_model(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda: False)
    monkeypatch.delenv("CHATGPT_MODEL", raising=False)
    monkeypatch.delenv("CHATGPT_CREDENTIALS_FILE", raising=False)

    cfg = _reload_config()

    assert cfg.CHATGPT_MODEL == "auto"
    assert cfg.CHATGPT_CREDENTIALS_FILE == "chatgpt_credentials.json"


def test_combined_timeout_cannot_be_shorter_than_regular_timeout(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda: False)
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("COMBINED_LLM_REQUEST_TIMEOUT_SECONDS", "30")

    cfg = _reload_config()

    assert cfg.LLM_REQUEST_TIMEOUT_SECONDS == 120
    assert cfg.COMBINED_LLM_REQUEST_TIMEOUT_SECONDS == 120


def test_config_get_config_issues_ok_and_missing_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr("dotenv.load_dotenv", lambda: False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_API_ID", "123")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
    monkeypatch.setenv("TELEGRAM_PHONE", "+79991234567")
    monkeypatch.setenv("ADMIN_USER_ID", "77")
    credentials = tmp_path / "credentials.json"
    monkeypatch.setenv("CHATGPT_CREDENTIALS_FILE", str(credentials))

    cfg = _reload_config()
    required, optional = cfg.get_config_issues()
    assert required == []
    assert optional and optional[0][0] == "CHATGPT_CREDENTIALS_FILE"

    credentials.write_text("{}", encoding="utf-8")
    _, optional = cfg.get_config_issues()
    assert optional == []


def test_config_get_config_issues_missing_and_invalid_ints(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda: False)
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
