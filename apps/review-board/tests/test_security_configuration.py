"""How this application wires itself to the shared security assembly.

The rules themselves are the library's and are tested there. What belongs here
is that this application hands over the two names that are its own, and asks
for the hardened profile rather than some other one.
"""

from __future__ import annotations

import json

import app as sample
import pytest

REQUIRED = {
    "KEYCLOAK_REALM_URL": "http://identity.invalid/realms/app",
    "KEYCLOAK_USER_AUDIENCE": "a-browser-client",
    "KEYCLOAK_M2M_CLIENT_ID": "review-board-service",
    "OPENFGA_API_URL": "http://authz.invalid:9080",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(sample.M2M_SECRET_ENV, "not-a-real-secret")
    monkeypatch.setenv(sample.OPENFGA_TOKEN_ENV, "not-a-real-token")
    for name in (
        "KEYCLOAK_M2M_REALM_URL",
        "KEYCLOAK_M2M_AUDIENCE",
        sample.DELEGATION_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_this_application_asks_for_the_hardened_profile_with_its_own_names(
    env: pytest.MonkeyPatch,
) -> None:
    config = sample._security_configuration()

    assert config.profile == "c3"
    assert config.m2m.secret_env_var == sample.M2M_SECRET_ENV
    assert config.rebac is not None
    assert config.rebac.token_env_var == sample.OPENFGA_TOKEN_ENV
    # A first-party application reads the shared model; it never owns it.
    assert not config.rebac.create_store_if_needed


def test_this_application_reads_its_delegation_block(env: pytest.MonkeyPatch) -> None:
    assert sample._security_configuration().delegation.in_use is False

    env.setenv(
        sample.DELEGATION_ENV,
        json.dumps({"accept_delegated_calls": True, "service_accounts_only": True}),
    )

    delegation = sample._security_configuration().delegation
    assert delegation.accept_delegated_calls is True
    assert delegation.act_for_people is False
    assert delegation.service_accounts_only is True
