import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import deploy_check, telegram_auth


def fake_telethon(monkeypatch, *, authorized=True, identity=None, start_error=None):
    calls = []

    class Client:
        def __init__(self, session, *args, **kwargs):
            Path(f'{session}.session').touch()
            calls.append('create')

        async def connect(self):
            calls.append('connect')

        async def is_user_authorized(self):
            return authorized

        async def start(self, **kwargs):
            calls.append('start')
            assert callable(kwargs['code_callback'])
            assert callable(kwargs['password'])
            if start_error:
                raise start_error

        async def get_me(self):
            return identity or SimpleNamespace(bot=False)

        async def disconnect(self):
            calls.append('disconnect')

    monkeypatch.setattr(telegram_auth, 'TelegramClient', Client)
    monkeypatch.setattr(deploy_check, 'TelegramClient', Client)
    return calls


def test_telegram_setup_reuses_session_without_login_prompts(tmp_path, monkeypatch):
    calls = fake_telethon(monkeypatch)
    path = asyncio.run(telegram_auth.authorize(str(tmp_path / 'user'), 1, 'hash', '+70000000000'))

    assert 'start' not in calls
    assert calls[-1] == 'disconnect'
    assert path.stat().st_mode & 0o777 == 0o600


def test_telegram_setup_disconnects_and_protects_session_after_login_failure(tmp_path, monkeypatch):
    calls = fake_telethon(monkeypatch, authorized=False, start_error=RuntimeError('failed'))
    monkeypatch.setattr(telegram_auth.sys.stdin, 'isatty', lambda: True)
    session = tmp_path / 'user'

    with pytest.raises(RuntimeError, match='failed'):
        asyncio.run(telegram_auth.authorize(str(session), 1, 'hash', '+70000000000'))

    assert calls[-1] == 'disconnect'
    assert session.with_suffix('.session').stat().st_mode & 0o777 == 0o600


def test_telegram_setup_refuses_interactive_login_without_terminal(tmp_path, monkeypatch):
    calls = fake_telethon(monkeypatch, authorized=False)
    monkeypatch.setattr(telegram_auth.sys.stdin, 'isatty', lambda: False)

    with pytest.raises(telegram_auth.TelegramSetupError, match='interactive terminal'):
        asyncio.run(telegram_auth.authorize(str(tmp_path / 'user'), 1, 'hash', '+70000000000'))

    assert 'start' not in calls
    assert calls[-1] == 'disconnect'


def test_telegram_setup_rejects_bot_account_session(tmp_path, monkeypatch):
    fake_telethon(monkeypatch, identity=SimpleNamespace(bot=True))

    with pytest.raises(telegram_auth.TelegramSetupError, match='user account'):
        asyncio.run(telegram_auth.authorize(str(tmp_path / 'user'), 1, 'hash', '+70000000000'))


def test_preflight_missing_session_does_not_create_one(tmp_path, monkeypatch):
    def unexpected_client(*args, **kwargs):
        pytest.fail('Missing-session check must not open a Telethon client')

    monkeypatch.setattr(deploy_check, 'TelegramClient', unexpected_client)
    settings = SimpleNamespace(SESSION_NAME=str(tmp_path / 'missing'))

    with pytest.raises(deploy_check.PreflightError, match='missing'):
        asyncio.run(deploy_check.check_telegram_user(settings))

    assert list(tmp_path.iterdir()) == []


def test_preflight_unauthorized_session_never_prompts_for_login(tmp_path, monkeypatch):
    session = tmp_path / 'user'
    session.with_suffix('.session').touch()
    calls = fake_telethon(monkeypatch, authorized=False)
    settings = SimpleNamespace(SESSION_NAME=str(session), TELEGRAM_API_ID=1, TELEGRAM_API_HASH='hash')

    with pytest.raises(deploy_check.PreflightError, match='not authorized'):
        asyncio.run(deploy_check.check_telegram_user(settings))

    assert 'start' not in calls
    assert calls[-1] == 'disconnect'


def test_preflight_checks_visible_model_and_completed_smoke_response(tmp_path, monkeypatch, capsys):
    import chatgpt_runtime

    async def noop(_settings):
        return None

    calls = []

    class ChatGPT:
        def __init__(self, credentials, model):
            self.model = model

        def list_models(self):
            calls.append('models')
            return [{'slug': 'gpt-available'}]

        def create_response(self, messages, *, requested_model, timeout):
            calls.append(requested_model)
            return SimpleNamespace(content='OK', model=requested_model)

    monkeypatch.setattr(deploy_check, 'check_telegram_user', noop)
    monkeypatch.setattr(deploy_check, 'check_bot_api', noop)
    monkeypatch.setattr(chatgpt_runtime, 'ChatGPTSubscriptionClient', ChatGPT)
    settings = SimpleNamespace(
        get_config_issues=lambda: ([], []), CHATGPT_CREDENTIALS_FILE=str(tmp_path / 'credentials'), CHATGPT_MODEL='auto'
    )

    asyncio.run(deploy_check.run_checks(settings, smoke_test=True))

    assert calls == ['models', 'gpt-available']
    assert 'completed inference: OK' in capsys.readouterr().out


def test_preflight_rejects_unavailable_pinned_model_before_inference(monkeypatch):
    import chatgpt_runtime

    async def noop(_settings):
        return None

    class ChatGPT:
        def __init__(self, credentials, model):
            self.model = model

        def list_models(self):
            return [{'slug': 'gpt-available'}]

        def create_response(self, *args, **kwargs):
            pytest.fail('Unavailable model must not be invoked')

    monkeypatch.setattr(deploy_check, 'check_telegram_user', noop)
    monkeypatch.setattr(deploy_check, 'check_bot_api', noop)
    monkeypatch.setattr(chatgpt_runtime, 'ChatGPTSubscriptionClient', ChatGPT)
    settings = SimpleNamespace(
        get_config_issues=lambda: ([], []), CHATGPT_CREDENTIALS_FILE='unused', CHATGPT_MODEL='gpt-missing'
    )

    with pytest.raises(deploy_check.PreflightError, match='not visible'):
        asyncio.run(deploy_check.run_checks(settings, smoke_test=True))


def test_telegram_cli_does_not_print_authentication_exception_body(monkeypatch, capsys):
    import sys

    async def failing_authorize(*args):
        raise RuntimeError('secret-login-value')

    settings = SimpleNamespace(
        SESSION_NAME='unused', TELEGRAM_API_ID=1, TELEGRAM_API_HASH='hash', TELEGRAM_PHONE='+70000000000'
    )
    monkeypatch.setitem(sys.modules, 'config', settings)
    monkeypatch.setattr(sys, 'argv', ['telegram_auth.py'])
    monkeypatch.setattr(telegram_auth, 'authorize', failing_authorize)
    monkeypatch.setattr(telegram_auth.os, 'umask', lambda _mode: None)

    assert telegram_auth.main() == 1
    captured = capsys.readouterr()
    assert 'RuntimeError' in captured.err
    assert 'secret-login-value' not in captured.out + captured.err


def test_preflight_cli_does_not_print_credential_bearing_network_error(monkeypatch, capsys):
    import sys

    async def failing_checks(*args, **kwargs):
        raise RuntimeError('https://api.telegram.org/botsecret-token/getMe')

    monkeypatch.setitem(sys.modules, 'config', SimpleNamespace())
    monkeypatch.setattr(sys, 'argv', ['deploy_check.py'])
    monkeypatch.setattr(deploy_check, 'run_checks', failing_checks)

    assert deploy_check.main() == 1
    captured = capsys.readouterr()
    assert 'RuntimeError' in captured.err
    assert 'secret-token' not in captured.out + captured.err
