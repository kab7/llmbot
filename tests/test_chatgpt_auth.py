import pytest

from scripts import chatgpt_auth


def test_select_host_id_creates_and_reuses_stable_value():
    created = chatgpt_auth._select_host_id({}, None)

    assert created.startswith("urn:uuid:")
    assert chatgpt_auth._select_host_id({"ext_agent_host_id": created}, None) == created


def test_select_host_id_rejects_replacing_existing_host():
    with pytest.raises(RuntimeError, match="differs"):
        chatgpt_auth._select_host_id(
            {"ext_agent_host_id": "urn:uuid:one"}, "urn:uuid:two"
        )


def test_validate_returning_identity_accepts_same_subject():
    chatgpt_auth._validate_returning_identity(
        {"client_id": "oaiapp_test", "subject": "account-1"},
        {"sub": "account-1"},
    )


@pytest.mark.parametrize("returned_subject", ["account-2", ""])
def test_validate_returning_identity_rejects_account_switch(returned_subject):
    with pytest.raises(RuntimeError, match="different ChatGPT account"):
        chatgpt_auth._validate_returning_identity(
            {"client_id": "oaiapp_test", "subject": "account-1"},
            {"sub": returned_subject},
        )


def test_validate_returning_identity_requires_saved_subject():
    with pytest.raises(RuntimeError, match="validated subject"):
        chatgpt_auth._validate_returning_identity(
            {"client_id": "oaiapp_test"},
            {"sub": "account-1"},
        )
