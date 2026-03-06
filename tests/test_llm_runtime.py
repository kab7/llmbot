import pytest

from llm_runtime import (
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_OPENROUTER_URL,
    LLMRuntimeConfig,
    LLMSettings,
    normalize_chat_completions_url,
)


def test_defaults_and_masking():
    cfg = LLMRuntimeConfig(
        DEFAULT_OPENROUTER_URL, "supersecrettoken", DEFAULT_OPENROUTER_MODEL
    )
    settings = cfg.get_settings()
    fallback = cfg.get_fallback_settings()

    assert settings.url == DEFAULT_OPENROUTER_URL
    assert settings.model == DEFAULT_OPENROUTER_MODEL
    assert settings.masked_token() == "supe...oken"
    assert cfg.has_token() is True
    assert cfg.has_any_token() is True
    assert fallback.url == DEFAULT_OPENROUTER_URL
    assert fallback.model == DEFAULT_FALLBACK_MODEL
    assert fallback.token == "supersecrettoken"


def test_masked_token_variants():
    assert LLMSettings("u", "", "m").masked_token() == "(not set)"
    assert LLMSettings("u", "short", "m").masked_token() == "*****"
    assert LLMSettings("u", "abcdefghijk", "m").masked_token() == "abcd...hijk"


def test_normalize_chat_completions_url_variants():
    assert normalize_chat_completions_url(
        "https://openrouter.ai/api/v1/chat/completions"
    ) == ("https://openrouter.ai/api/v1/chat/completions")
    assert normalize_chat_completions_url("https://openrouter.ai/api/v1/") == (
        "https://openrouter.ai/api/v1/chat/completions"
    )
    assert normalize_chat_completions_url("https://example.com/v1") == (
        "https://example.com/v1/chat/completions"
    )
    assert normalize_chat_completions_url("https://example.com/custom-endpoint") == (
        "https://example.com/custom-endpoint"
    )


def test_normalize_chat_completions_url_validation():
    with pytest.raises(ValueError, match="пустым"):
        normalize_chat_completions_url("")

    with pytest.raises(ValueError, match="http:// или https://"):
        normalize_chat_completions_url("openrouter.ai/api/v1")


def test_runtime_setters_and_get_copy():
    cfg = LLMRuntimeConfig(
        "https://openrouter.ai/api/v1", "tokentoken12345", "model/one"
    )

    assert cfg.set_url("https://host/v1") == "https://host/v1/chat/completions"
    assert cfg.set_model(" model/two ") == "model/two"
    assert cfg.set_fallback_model(" fallback/model ") == "fallback/model"
    assert cfg.set_token(" new-token-123456 ") == "new-...3456"

    snapshot = cfg.get_settings()
    snapshot.url = "mutated"
    snapshot.model = "mutated"
    snapshot.token = "mutated"

    stable = cfg.get_settings()
    assert stable.url == "https://host/v1/chat/completions"
    assert stable.model == "model/two"
    assert stable.token == "new-token-123456"

    fallback = cfg.get_fallback_settings()
    assert fallback.token == "new-token-123456"
    assert fallback.model == "fallback/model"


def test_runtime_fallback_overrides_and_candidates():
    cfg = LLMRuntimeConfig(
        "https://primary/v1",
        "primary-token",
        "primary/model",
        fallback_url="https://fallback/v1",
        fallback_token="fallback-token",
        fallback_model="fallback/model",
    )

    fallback = cfg.get_fallback_settings()
    assert fallback.url == "https://fallback/v1/chat/completions"
    assert fallback.model == "fallback/model"
    assert fallback.token == "fallback-token"

    candidates = cfg.get_candidate_settings()
    assert len(candidates) == 2
    assert candidates[0].model == "primary/model"
    assert candidates[1].model == "fallback/model"


def test_runtime_candidates_deduplicate_and_any_token():
    cfg = LLMRuntimeConfig(
        "https://same/v1",
        "token",
        "model",
        fallback_url="https://same/v1",
        fallback_token="token",
        fallback_model="model",
    )
    assert len(cfg.get_candidate_settings()) == 1
    assert cfg.has_any_token() is True

    no_token_cfg = LLMRuntimeConfig(
        "https://same/v1",
        "",
        "model",
        fallback_url="https://same/v1",
        fallback_token="",
        fallback_model="model",
    )
    assert no_token_cfg.has_token() is False
    assert no_token_cfg.has_any_token() is False


def test_runtime_setters_validation():
    cfg = LLMRuntimeConfig(
        "https://openrouter.ai/api/v1", "tokentoken12345", "model/one"
    )

    with pytest.raises(ValueError, match="пустым"):
        cfg.set_token("   ")

    with pytest.raises(ValueError, match="пустой"):
        cfg.set_model("  ")
    with pytest.raises(ValueError, match="пустой"):
        cfg.set_fallback_model("  ")

    with pytest.raises(ValueError, match="http:// или https://"):
        cfg.set_url("bad-url")

    with pytest.raises(ValueError, match="пустым"):
        cfg.set_fallback_token("   ")

    with pytest.raises(ValueError, match="http:// или https://"):
        cfg.set_fallback_url("bad-url")


def test_runtime_fallback_token_can_be_set_independently():
    cfg = LLMRuntimeConfig(
        "https://openrouter.ai/api/v1", "primary-token-1", "model/one"
    )
    cfg.set_fallback_token("fallback-token-2")
    cfg.set_token("primary-token-3")

    assert cfg.get_settings().token == "primary-token-3"
    assert cfg.get_fallback_settings().token == "fallback-token-2"


def test_runtime_fallback_url_setter():
    cfg = LLMRuntimeConfig(
        "https://openrouter.ai/api/v1", "primary-token-1", "model/one"
    )
    assert (
        cfg.set_fallback_url("https://fallback.example/v1")
        == "https://fallback.example/v1/chat/completions"
    )
