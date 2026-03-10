from dataclasses import dataclass
from urllib.parse import urlparse


DEFAULT_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
DEFAULT_FALLBACK_MODEL = "openrouter/free"


@dataclass
class LLMSettings:
    url: str
    token: str
    model: str
    scope: str = "primary"

    def masked_token(self) -> str:
        if not self.token:
            return "(not set)"
        if len(self.token) <= 10:
            return "*" * len(self.token)
        return f"{self.token[:4]}...{self.token[-4:]}"


def normalize_chat_completions_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        raise ValueError("URL не может быть пустым")

    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL должен начинаться с http:// или https://")

    clean = value.rstrip("/")
    if clean.endswith("/chat/completions"):
        return clean
    if clean.endswith("/v1") or clean.endswith("/api/v1"):
        return f"{clean}/chat/completions"
    return clean


class LLMRuntimeConfig:
    def __init__(
        self,
        url: str,
        token: str,
        model: str,
        fallback_url: str | None = None,
        fallback_token: str | None = None,
        fallback_model: str | None = None,
    ):
        self._fallback_inherits_primary = fallback_token is None
        self._settings = LLMSettings(
            url=normalize_chat_completions_url(url),
            token=(token or "").strip(),
            model=self._normalize_model(model),
        )
        fallback_token_value = (
            self._settings.token
            if self._fallback_inherits_primary
            else (fallback_token or "").strip()
        )
        self._fallback_settings = LLMSettings(
            url=normalize_chat_completions_url(fallback_url or DEFAULT_OPENROUTER_URL),
            token=fallback_token_value,
            model=self._normalize_model(fallback_model or DEFAULT_FALLBACK_MODEL),
        )

    @staticmethod
    def _normalize_model(model: str) -> str:
        parts = [part.strip() for part in (model or "").split(",")]
        normalized_parts = [part for part in parts if part]
        if not normalized_parts:
            raise ValueError("Модель не может быть пустой")
        return ",".join(normalized_parts)

    @staticmethod
    def _split_models(model: str) -> list[str]:
        return [part.strip() for part in (model or "").split(",") if part.strip()]

    def set_url(self, url: str) -> str:
        self._settings.url = normalize_chat_completions_url(url)
        return self._settings.url

    def set_fallback_url(self, url: str) -> str:
        self._fallback_settings.url = normalize_chat_completions_url(url)
        return self._fallback_settings.url

    def set_token(self, token: str) -> str:
        value = (token or "").strip()
        if not value:
            raise ValueError("Токен не может быть пустым")
        self._settings.token = value
        if self._fallback_inherits_primary:
            self._fallback_settings.token = value
        return self._settings.masked_token()

    def set_fallback_token(self, token: str) -> str:
        value = (token or "").strip()
        if not value:
            raise ValueError("Токен не может быть пустым")
        self._fallback_settings.token = value
        self._fallback_inherits_primary = False
        return self._fallback_settings.masked_token()

    def set_model(self, model: str) -> str:
        self._settings.model = self._normalize_model(model)
        return self._settings.model

    def set_fallback_model(self, model: str) -> str:
        self._fallback_settings.model = self._normalize_model(model)
        return self._fallback_settings.model

    def has_token(self) -> bool:
        return bool(self._settings.token)

    def has_any_token(self) -> bool:
        return bool(self._settings.token or self._fallback_settings.token)

    def get_settings(self) -> LLMSettings:
        return LLMSettings(
            url=self._settings.url,
            token=self._settings.token,
            model=self._settings.model,
            scope="primary",
        )

    def get_fallback_settings(self) -> LLMSettings:
        return LLMSettings(
            url=self._fallback_settings.url,
            token=self._fallback_settings.token,
            model=self._fallback_settings.model,
            scope="fallback",
        )

    def get_candidate_settings(self) -> list[LLMSettings]:
        primary = self.get_settings()
        fallback = self.get_fallback_settings()
        candidates: list[LLMSettings] = []
        seen: set[tuple[str, str, str]] = set()
        for model_name in self._split_models(primary.model):
            candidate = LLMSettings(
                primary.url, primary.token, model_name, scope="primary"
            )
            key = (candidate.url, candidate.model, candidate.token)
            if key not in seen:
                candidates.append(candidate)
                seen.add(key)
        for model_name in self._split_models(fallback.model):
            candidate = LLMSettings(
                fallback.url, fallback.token, model_name, scope="fallback"
            )
            key = (candidate.url, candidate.model, candidate.token)
            if key not in seen:
                candidates.append(candidate)
                seen.add(key)
        return candidates
