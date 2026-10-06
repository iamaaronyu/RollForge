import secrets
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from rollforge_hub_sdk.client import HubClient, HubError, HubProtocolError, HubTransportError


async def test_sdk_does_not_follow_redirects_or_echo_error_body():
    token = secrets.token_urlsafe(32)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            302,
            headers={"Location": "https://other.test/" + token},
            json={"code": token, "message": token},
        )

    async with HubClient(
        "https://hub.test", SecretStr(token), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(HubError) as exc:
            await client.get_job(uuid4())
        assert exc.value.status_code == 302 and exc.value.code is None
        assert token not in str(exc.value)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [200, 500])
async def test_malformed_responses_are_sanitized(status):
    marker = secrets.token_urlsafe(32)
    async with HubClient(
        "https://hub.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(status, content=marker),
        ),
    ) as client:
        expected = HubProtocolError if status == 200 else HubError
        with pytest.raises(expected) as exc:
            await client.health()
        assert marker not in str(exc.value)


async def test_network_errors_are_sanitized():
    marker = secrets.token_urlsafe(32)

    def handler(_request):
        raise httpx.ConnectError(marker)

    async with HubClient("https://hub.test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(HubTransportError) as exc:
            await client.health()
        assert marker not in str(exc.value)


def test_sdk_rejects_credentials_in_base_url():
    marker = secrets.token_urlsafe(32)
    for address in (
        f"https://user:{marker}@hub.test",
        f"http://hub.test:{marker}",
        f"https://user:{marker}＠hub.test",
    ):
        with pytest.raises(ValueError) as exc:
            HubClient(address)
        assert marker not in str(exc.value)
