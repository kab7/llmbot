import importlib.util
import logging
from pathlib import Path

import bot


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts/scrub_logs.py"
SPEC = importlib.util.spec_from_file_location("scrub_logs", SCRIPT_PATH)
scrub_logs = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(scrub_logs)


def test_runtime_formatter_redacts_tokens_headers_queries_and_tracebacks():
    telegram_token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    opaque_secret = "opaque-yandex-secret-value"
    bot.register_sensitive_log_value(opaque_secret)
    formatter = bot.RedactingFormatter("%(levelname)s %(message)s")

    try:
        raise RuntimeError(f"failed with {opaque_secret}")
    except RuntimeError:
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg=(
                f"https://api.telegram.org/bot{telegram_token}/getMe "
                "Authorization: Bearer bearer-secret "
                "https://example.test/?access_token=query-secret "
                "sk-or-v1-openrouter-secret"
            ),
            args=(),
            exc_info=__import__("sys").exc_info(),
        )

    rendered = formatter.format(record)

    for secret in (
        telegram_token,
        opaque_secret,
        "bearer-secret",
        "query-secret",
        "sk-or-v1-openrouter-secret",
    ):
        assert secret not in rendered
    assert rendered.count(bot.LOG_REDACTION_MARKER) >= 5


def test_http_client_request_logging_is_not_info():
    for logger_name in ("httpx", "httpcore", "urllib3", "requests"):
        assert logging.getLogger(logger_name).getEffectiveLevel() >= logging.WARNING


def test_scrub_logs_loads_env_secrets_and_preserves_nonsecret_text(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TELEGRAM_BOT_TOKEN=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi\n"
        "PRIMARY_LLM_API_KEY='opaque-primary-secret'\n"
        "NORMAL_VALUE=keep-me\n",
        encoding="utf-8",
    )
    log_path = tmp_path / "bot.log"
    log_path.write_text(
        "normal line\n"
        "https://api.telegram.org/"
        "bot123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi/getMe\n"
        "Authorization: Api-Key opaque-primary-secret\n"
        "https://example.test/?token=query-secret\n",
        encoding="utf-8",
    )

    secrets = scrub_logs.load_env_secrets(env_path)
    replacements = scrub_logs.scrub_file(log_path, secrets)
    result = log_path.read_text(encoding="utf-8")

    assert replacements == 3
    assert "normal line" in result
    assert "keep-me" not in secrets
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in result
    assert "opaque-primary-secret" not in result
    assert "query-secret" not in result
    assert result.count(scrub_logs.REDACTION_MARKER) == 3


def test_scrub_logs_is_idempotent(tmp_path):
    log_path = tmp_path / "bot.log"
    log_path.write_text("already safe [REDACTED]\n", encoding="utf-8")

    assert scrub_logs.scrub_file(log_path, set()) == 0
    assert log_path.read_text(encoding="utf-8") == "already safe [REDACTED]\n"
