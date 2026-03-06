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
        self._settings = LLMSettings(
            url=normalize_chat_completions_url(url),
            token=(token or "").strip(),
            model=self._normalize_model(model),
        )
        self._fallback_settings = LLMSettings(
            url=normalize_chat_completions_url(fallback_url or DEFAULT_OPENROUTER_URL),
            token=(token if fallback_token is None else fallback_token or "").strip(),
            model=self._normalize_model(fallback_model or DEFAULT_FALLBACK_MODEL),
        )

    @staticmethod
    def _normalize_model(model: str) -> str:
        value = (model or "").strip()
        if not value:
            raise ValueError("Модель не может быть пустой")
        return value

    def set_url(self, url: str) -> str:
        self._settings.url = normalize_chat_completions_url(url)
        return self._settings.url

    def set_token(self, token: str) -> str:
        value = (token or "").strip()
        if not value:
            raise ValueError("Токен не может быть пустым")
        self._settings.token = value
        return self._settings.masked_token()

    def set_model(self, model: str) -> str:
        self._settings.model = self._normalize_model(model)
        return self._settings.model

    def has_token(self) -> bool:
        return bool(self._settings.token)

    def has_any_token(self) -> bool:
        return bool(self._settings.token or self._fallback_settings.token)

    def get_settings(self) -> LLMSettings:
        return LLMSettings(
            url=self._settings.url,
            token=self._settings.token,
            model=self._settings.model,
        )

    def get_fallback_settings(self) -> LLMSettings:
        return LLMSettings(
            url=self._fallback_settings.url,
            token=self._fallback_settings.token,
            model=self._fallback_settings.model,
        )

    def get_candidate_settings(self) -> list[LLMSettings]:
        primary = self.get_settings()
        fallback = self.get_fallback_settings()
        candidates = [primary]
        if (fallback.url, fallback.model, fallback.token) != (
            primary.url,
            primary.model,
            primary.token,
        ):
            candidates.append(fallback)
        return candidates
