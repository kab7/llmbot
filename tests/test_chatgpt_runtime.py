import json
import os
from datetime import datetime, timedelta, timezone

import pytest

import chatgpt_runtime as runtime


class FakeResponse:
    def __init__(self, status_code=200, payload=None, lines=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._lines = lines or []
        self.text = text or json.dumps(self._payload)
        self.closed = False

    def json(self):
        return self._payload

    def iter_lines(self, decode_unicode=False):
        del decode_unicode
        return iter(self._lines)

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, *, posts=None, gets=None):
        self.posts = list(posts or [])
        self.gets = list(gets or [])
        self.post_calls = []
        self.get_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.posts.pop(0)

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.gets.pop(0)


def _credentials(saved_at=None, **overrides):
    payload = {
        "email": "owner@example.com",
        "client_id": "oaiapp_test",
        "access_token": "access-old",
        "refresh_token": "refresh-old",
        "id_token": "id-token",
        "expires_in": 3600,
        "scopes": [
            "openid",
            "offline_access",
            "resource.invoke",
            "chatgpt.tokens.use.direct",
        ],
        "saved_at": saved_at or datetime.now(timezone.utc).isoformat(),
    }
    payload.update(overrides)
    return payload


def _write_credentials(path, payload=None):
    path.write_text(json.dumps(payload or _credentials()), encoding="utf-8")
    os.chmod(path, 0o600)


def test_messages_to_responses_payload_uses_supported_preview_fields():
    payload = runtime.messages_to_responses_payload(
        [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "draft"},
            {"role": "user", "content": "repair"},
        ]
    )

    assert payload == {
        "instructions": "rules",
        "input": [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "draft"},
            {"role": "user", "content": "repair"},
        ],
        "store": False,
        "stream": True,
    }
    assert "max_output_tokens" not in payload


def test_messages_to_responses_payload_rejects_unknown_role():
    with pytest.raises(ValueError, match="Неподдерживаемая роль"):
        runtime.messages_to_responses_payload([{"role": "tool", "content": "x"}])


def test_parse_responses_sse_requires_completed_and_collects_text():
    result = runtime.parse_responses_sse(
        [
            "event: response.output_text.delta",
            'data: {"type":"response.output_text.delta","delta":"hello "}',
            'data: {"type":"response.output_text.delta","delta":"world"}',
            'data: {"type":"response.completed","response":{"model":"gpt-test"}}',
            "data: [DONE]",
        ]
    )

    assert result.content == "hello world"
    assert result.model == "gpt-test"


def test_parse_responses_sse_raises_structured_failure_with_partial_text():
    with pytest.raises(runtime.ChatGPTResponseError) as error:
        runtime.parse_responses_sse(
            [
                'data: {"type":"response.output_text.delta","delta":"partial"}',
                (
                    'data: {"type":"response.failed","response":{"error":'
                    '{"code":"subscription_sharing_usage_limit_exceeded",'
                    '"message":"limit"}}}'
                ),
            ]
        )

    assert error.value.code == "subscription_sharing_usage_limit_exceeded"
    assert error.value.partial_text == "partial"


def test_parse_responses_sse_rejects_stream_without_completed():
    with pytest.raises(runtime.ChatGPTResponseError, match="response.completed"):
        runtime.parse_responses_sse(
            ['data: {"type":"response.output_text.delta","delta":"partial"}']
        )


def test_client_requires_protected_complete_plan_credentials(tmp_path):
    missing = runtime.ChatGPTSubscriptionClient(tmp_path / "missing.json")
    assert missing.is_configured() is False

    path = tmp_path / "credentials.json"
    _write_credentials(path, _credentials(scopes=["openid"]))
    client = runtime.ChatGPTSubscriptionClient(path)
    assert client.is_configured() is False
    assert "chatgpt.tokens.use.direct" in client.credential_status()["error"]


def test_get_access_token_uses_unexpired_saved_token(tmp_path):
    path = tmp_path / "credentials.json"
    _write_credentials(path)
    session = FakeSession()
    client = runtime.ChatGPTSubscriptionClient(path, session=session)

    assert client.get_access_token() == "access-old"
    assert session.post_calls == []


def test_get_access_token_refreshes_and_rotates_credentials_atomically(tmp_path):
    path = tmp_path / "credentials.json"
    expired = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    _write_credentials(path, _credentials(saved_at=expired))
    session = FakeSession(
        posts=[
            FakeResponse(
                payload={
                    "access_token": "access-new",
                    "refresh_token": "refresh-new",
                    "expires_in": 7200,
                    "scope": "openid offline_access resource.invoke chatgpt.tokens.use.direct",
                }
            )
        ]
    )
    client = runtime.ChatGPTSubscriptionClient(path, session=session)

    assert client.get_access_token() == "access-new"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["refresh_token"] == "refresh-new"
    assert saved["access_token"] == "access-new"
    assert os.stat(path).st_mode & 0o777 == 0o600
    _, call = session.post_calls[0]
    assert call["data"]["client_id"] == "oaiapp_test"
    assert "scope" not in call["data"]


def test_auto_model_uses_first_visible_account_model(tmp_path):
    path = tmp_path / "credentials.json"
    _write_credentials(path)
    session = FakeSession(
        gets=[
            FakeResponse(
                payload={
                    "models": [
                        {"slug": "hidden", "visibility": "hidden"},
                        {
                            "slug": "gpt-first",
                            "display_name": "GPT First",
                            "visibility": "list",
                        },
                        {"slug": "gpt-second", "visibility": "list"},
                    ]
                }
            )
        ]
    )
    client = runtime.ChatGPTSubscriptionClient(path, model="auto", session=session)

    assert client.resolve_model() == "gpt-first"
    assert client.resolve_model() == "gpt-first"
    assert len(session.get_calls) == 1


def test_create_response_uses_oauth_responses_contract(tmp_path):
    path = tmp_path / "credentials.json"
    _write_credentials(path)
    response = FakeResponse(
        lines=[
            'data: {"type":"response.output_text.delta","delta":"ok"}',
            'data: {"type":"response.completed","response":{"model":"gpt-test"}}',
        ]
    )
    session = FakeSession(posts=[response])
    client = runtime.ChatGPTSubscriptionClient(path, model="gpt-test", session=session)

    result = client.create_response(
        [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "hello"},
        ],
        timeout=45,
    )

    assert result.content == "ok"
    url, call = session.post_calls[0]
    assert url == runtime.OPENAI_RESPONSES_URL
    assert call["headers"]["Authorization"] == "Bearer access-old"
    assert call["json"] == {
        "model": "gpt-test",
        "instructions": "rules",
        "input": [{"role": "user", "content": "hello"}],
        "store": False,
        "stream": True,
    }
    assert call["stream"] is True
    assert response.closed is True


def test_client_rejects_multiple_configured_models(tmp_path):
    with pytest.raises(ValueError, match="только одна"):
        runtime.ChatGPTSubscriptionClient(tmp_path / "x", model="a,b")
