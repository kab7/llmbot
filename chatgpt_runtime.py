from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests


OPENAI_API_BASE = "https://api.openai.com/v1"
OPENAI_RESPONSES_URL = f"{OPENAI_API_BASE}/responses"
OPENAI_MODELS_URL = f"{OPENAI_API_BASE}/models"
OPENAI_TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
OPENAI_RESOURCE = OPENAI_API_BASE
REQUIRED_PLAN_SCOPE = "chatgpt.tokens.use.direct"


class ChatGPTCredentialsError(RuntimeError):
    pass


class ChatGPTResponseError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "unknown_error",
        status_code: int | None = None,
        partial_text: str = "",
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.partial_text = partial_text


@dataclass(frozen=True)
class ChatGPTCandidate:
    model: str
    scope: str = "chatgpt_subscription"


@dataclass(frozen=True)
class ChatGPTResponse:
    content: str
    model: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_saved_at(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ChatGPTCredentialsError("В credentials отсутствует saved_at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ChatGPTCredentialsError("Некорректный saved_at в credentials") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _scope_set(credentials: dict[str, Any]) -> set[str]:
    raw = credentials.get("scopes", credentials.get("scope", []))
    if isinstance(raw, str):
        return {item for item in raw.split() if item}
    if isinstance(raw, list):
        return {str(item).strip() for item in raw if str(item).strip()}
    return set()


def _error_details(response: requests.Response) -> tuple[str, str]:
    try:
        payload = response.json()
    except Exception:
        return "http_error", (response.text or "").strip()[:500]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        code = str(error.get("code") or "http_error")
        message = str(error.get("message") or error)
        return code, message[:500]
    return "http_error", str(payload)[:500]


def messages_to_responses_payload(messages: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Convert the bot's chat-style messages to the SIWC Responses subset."""
    instructions: list[str] = []
    inputs: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "").strip().lower()
        content = str(message.get("content") or "")
        if role in {"system", "developer"}:
            if content:
                instructions.append(content)
            continue
        if role not in {"user", "assistant"}:
            raise ValueError(f"Неподдерживаемая роль LLM-сообщения: {role or '(empty)'}")
        inputs.append({"role": role, "content": content})

    if not inputs:
        raise ValueError("LLM-запрос не содержит user/assistant сообщений")
    payload: dict[str, Any] = {"input": inputs, "store": False, "stream": True}
    if instructions:
        payload["instructions"] = "\n\n".join(instructions)
    return payload


def _extract_completed_output_text(response_payload: Any) -> str:
    if not isinstance(response_payload, dict):
        return ""
    chunks: list[str] = []
    for item in response_payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") == "output_text" and content.get("text"):
                chunks.append(str(content["text"]))
    return "".join(chunks)


def parse_responses_sse(lines: Iterable[str | bytes]) -> ChatGPTResponse:
    """Parse a Responses API SSE stream and require response.completed."""
    text_parts: list[str] = []
    completed = False
    completed_response: dict[str, Any] = {}
    pending_event = ""

    for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else raw_line
        line = line.rstrip("\r\n")
        if not line or line.startswith(":"):
            continue
        if line.startswith("event:"):
            pending_event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        raw_data = line[5:].strip()
        if raw_data == "[DONE]":
            continue
        try:
            event = json.loads(raw_data)
        except json.JSONDecodeError as exc:
            raise ChatGPTResponseError(
                "Responses API вернул некорректное SSE-событие",
                code="invalid_sse",
                partial_text="".join(text_parts),
            ) from exc

        event_type = str(event.get("type") or pending_event)
        pending_event = ""
        if event_type == "response.output_text.delta":
            text_parts.append(str(event.get("delta") or ""))
        elif event_type == "response.completed":
            completed = True
            completed_response = event.get("response") or {}
        elif event_type in {"response.failed", "response.incomplete", "error"}:
            response_obj = event.get("response") or {}
            error_obj = event.get("error") or response_obj.get("error") or {}
            incomplete = response_obj.get("incomplete_details") or {}
            if not isinstance(error_obj, dict):
                error_obj = {}
            if not isinstance(incomplete, dict):
                incomplete = {}
            code = str(
                error_obj.get("code")
                or event.get("code")
                or incomplete.get("reason")
                or event_type.replace("response.", "")
            )
            message = str(error_obj.get("message") or event.get("message") or code)
            raise ChatGPTResponseError(
                f"Responses API завершил запрос с ошибкой: {message}",
                code=code,
                partial_text="".join(text_parts),
            )

    if not completed:
        raise ChatGPTResponseError(
            "Поток Responses API завершился без response.completed",
            code="stream_not_completed",
            partial_text="".join(text_parts),
        )

    content = "".join(text_parts) or _extract_completed_output_text(completed_response)
    if not content:
        raise ChatGPTResponseError(
            "Responses API вернул пустой завершённый ответ",
            code="empty_response",
        )
    model = str(completed_response.get("model") or "").strip()
    return ChatGPTResponse(content=content, model=model)


class ChatGPTSubscriptionClient:
    def __init__(
        self,
        credentials_path: str | Path,
        model: str = "auto",
        *,
        session: requests.Session | None = None,
    ):
        self.credentials_path = Path(credentials_path)
        self.model = self._normalize_model(model)
        self._session = session or requests.Session()
        self._refresh_lock = threading.RLock()
        self._cached_auto_model: str | None = None

    @staticmethod
    def _normalize_model(model: str) -> str:
        value = (model or "").strip()
        if not value:
            raise ValueError("Модель не может быть пустой")
        if "," in value:
            raise ValueError("Поддерживается только одна ChatGPT-модель")
        return value

    def set_model(self, model: str) -> str:
        self.model = self._normalize_model(model)
        self._cached_auto_model = None
        return self.model

    def is_configured(self) -> bool:
        try:
            self._load_credentials()
        except ChatGPTCredentialsError:
            return False
        return True

    def credential_status(self) -> dict[str, Any]:
        try:
            credentials = self._load_credentials()
        except ChatGPTCredentialsError as exc:
            return {"configured": False, "error": str(exc)}
        return {
            "configured": True,
            "email": str(credentials.get("email") or "").strip(),
            "client_id": str(credentials.get("client_id") or "").strip(),
            "expires_at": self._expires_at(credentials).isoformat(),
        }

    def _load_credentials(self) -> dict[str, Any]:
        try:
            raw = self.credentials_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ChatGPTCredentialsError(
                f"Файл ChatGPT credentials не найден: {self.credentials_path}"
            ) from exc
        except OSError as exc:
            raise ChatGPTCredentialsError(
                f"Не удалось прочитать ChatGPT credentials: {exc}"
            ) from exc
        try:
            credentials = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ChatGPTCredentialsError("ChatGPT credentials содержит невалидный JSON") from exc
        if not isinstance(credentials, dict):
            raise ChatGPTCredentialsError("ChatGPT credentials должен быть JSON-объектом")
        for key in ("client_id", "access_token", "refresh_token", "saved_at"):
            if not str(credentials.get(key) or "").strip():
                raise ChatGPTCredentialsError(f"В ChatGPT credentials отсутствует {key}")
        if REQUIRED_PLAN_SCOPE not in _scope_set(credentials):
            raise ChatGPTCredentialsError(
                f"ChatGPT credentials не содержит scope {REQUIRED_PLAN_SCOPE}"
            )
        return credentials

    @staticmethod
    def _expires_at(credentials: dict[str, Any]) -> datetime:
        saved_at = _parse_saved_at(credentials.get("saved_at"))
        try:
            expires_in = int(credentials.get("expires_in") or 0)
        except (TypeError, ValueError) as exc:
            raise ChatGPTCredentialsError("Некорректный expires_in в credentials") from exc
        if expires_in <= 0:
            raise ChatGPTCredentialsError("В credentials отсутствует положительный expires_in")
        return saved_at + timedelta(seconds=expires_in)

    def _save_credentials(self, credentials: dict[str, Any]) -> None:
        self.credentials_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.credentials_path.with_name(f".{self.credentials_path.name}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        descriptor = os.open(tmp_path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(credentials, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.credentials_path)
            os.chmod(self.credentials_path, 0o600)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def get_access_token(self, *, force_refresh: bool = False) -> str:
        with self._refresh_lock:
            credentials = self._load_credentials()
            if not force_refresh and self._expires_at(credentials) > _utc_now() + timedelta(seconds=90):
                return str(credentials["access_token"])

            response = self._session.post(
                OPENAI_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": credentials["client_id"],
                    "refresh_token": credentials["refresh_token"],
                    "resource": OPENAI_RESOURCE,
                },
                timeout=30,
            )
            if response.status_code != 200:
                code, details = _error_details(response)
                raise ChatGPTCredentialsError(
                    f"Не удалось обновить ChatGPT OAuth session ({response.status_code}, {code}): {details}"
                )
            try:
                refreshed = response.json()
            except Exception as exc:
                raise ChatGPTCredentialsError(
                    "OpenAI token endpoint вернул невалидный JSON"
                ) from exc
            if not isinstance(refreshed, dict) or not refreshed.get("access_token"):
                raise ChatGPTCredentialsError(
                    "OpenAI token endpoint не вернул access_token"
                )

            updated = dict(credentials)
            for key in (
                "access_token",
                "refresh_token",
                "id_token",
                "token_type",
                "expires_in",
            ):
                if refreshed.get(key) is not None:
                    updated[key] = refreshed[key]
            if refreshed.get("scope") is not None:
                updated["scopes"] = sorted(_scope_set({"scope": refreshed["scope"]}))
            updated["saved_at"] = _utc_now().isoformat()
            if REQUIRED_PLAN_SCOPE not in _scope_set(updated):
                raise ChatGPTCredentialsError(
                    f"Обновлённая OAuth session потеряла scope {REQUIRED_PLAN_SCOPE}"
                )
            self._save_credentials(updated)
            return str(updated["access_token"])

    def list_models(self) -> list[dict[str, str]]:
        token = self.get_access_token()
        response = self._session.get(
            OPENAI_MODELS_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if response.status_code == 401:
            token = self.get_access_token(force_refresh=True)
            response = self._session.get(
                OPENAI_MODELS_URL,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
        if response.status_code != 200:
            code, details = _error_details(response)
            raise ChatGPTResponseError(
                f"Не удалось получить список моделей ({response.status_code}, {code}): {details}",
                code=code,
                status_code=response.status_code,
            )
        payload = response.json()
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            raise ChatGPTResponseError(
                "OpenAI models endpoint вернул неожиданный формат",
                code="invalid_models_response",
            )
        result: list[dict[str, str]] = []
        for item in models:
            if not isinstance(item, dict) or item.get("visibility") != "list":
                continue
            slug = str(item.get("slug") or "").strip()
            if slug:
                result.append(
                    {
                        "slug": slug,
                        "display_name": str(item.get("display_name") or slug),
                    }
                )
        return result

    def resolve_model(self, requested_model: str | None = None) -> str:
        selected = self._normalize_model(requested_model or self.model)
        if selected.casefold() != "auto":
            return selected
        if self._cached_auto_model:
            return self._cached_auto_model
        models = self.list_models()
        if not models:
            raise ChatGPTResponseError(
                "Подписка не вернула ни одной доступной ChatGPT-модели",
                code="no_available_models",
            )
        self._cached_auto_model = models[0]["slug"]
        return self._cached_auto_model

    def create_response(
        self,
        messages: list[dict[str, Any]],
        *,
        requested_model: str | None = None,
        timeout: int = 90,
    ) -> ChatGPTResponse:
        model = self.resolve_model(requested_model)
        payload = messages_to_responses_payload(messages)
        payload["model"] = model

        for auth_attempt in range(2):
            token = self.get_access_token(force_refresh=auth_attempt == 1)
            response = self._session.post(
                OPENAI_RESPONSES_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=(10, max(1, int(timeout))),
                stream=True,
            )
            if response.status_code == 401 and auth_attempt == 0:
                response.close()
                continue
            if response.status_code != 200:
                code, details = _error_details(response)
                response.close()
                raise ChatGPTResponseError(
                    f"Responses API вернул ошибку {response.status_code}: {details}",
                    code=code,
                    status_code=response.status_code,
                )
            try:
                parsed = parse_responses_sse(response.iter_lines(decode_unicode=True))
            finally:
                response.close()
            return ChatGPTResponse(
                content=parsed.content,
                model=parsed.model or model,
            )
        raise ChatGPTCredentialsError("ChatGPT OAuth access token отклонён после refresh")
