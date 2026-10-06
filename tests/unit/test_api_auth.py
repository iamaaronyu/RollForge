import hashlib
import json
import secrets
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from rollforge_api.auth import Authenticator
from rollforge_api.main import create_app
from rollforge_common.settings import Settings


def configuration():
    user, worker = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    data = [
        {
            "subject_id": str(uuid4()),
            "role": role,
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        }
        for role, token in (("USER", user), ("WORKER", worker))
    ]
    settings = Settings(
        _env_file=None,
        database_url=SecretStr("sqlite+aiosqlite:///:memory:"),
        api_credentials=SecretStr(json.dumps(data)),
        control_plane_writes_enabled=False,
    )
    return settings, user, worker


@pytest.mark.parametrize(
    "header", [None, "Bearer short", "Basic invalid", "Bearer " + secrets.token_urlsafe(32)]
)
def test_unauthenticated_requests_fail_closed(header):
    settings, _, _ = configuration()
    with TestClient(create_app(settings)) as client:
        headers = {"Authorization": header} if header else {}
        response = client.get(f"/api/v1/jobs/{uuid4()}", headers=headers)
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHORIZED"
        assert response.headers["www-authenticate"] == "Bearer"


def test_roles_and_default_write_gate():
    settings, user, worker = configuration()
    with TestClient(create_app(settings)) as client:
        user_header = {"Authorization": "Bearer " + user}
        worker_header = {"Authorization": "Bearer " + worker}
        response = client.post("/api/v1/worker/leases/claim", json={}, headers=user_header)
        assert response.status_code == 403
        assert client.get(f"/api/v1/jobs/{uuid4()}", headers=worker_header).status_code == 403
        response = client.post("/api/v1/worker/leases/claim", json={}, headers=worker_header)
        assert response.status_code == 503 and response.json()["code"] == "WRITES_DISABLED"
        assert client.get("/api/v1/platform").json()["execution_enabled"] is False
        assert user not in response.text and worker not in response.text


def test_config_errors_do_not_echo_secrets_and_empty_auth_blocks_writes():
    marker = secrets.token_urlsafe(32)
    with pytest.raises(ValueError) as exc:
        Authenticator(marker)
    assert marker not in str(exc.value)
    with pytest.raises(ValueError, match="必须配置鉴权"):
        create_app(
            Settings(
                _env_file=None, api_credentials=SecretStr("[]"), control_plane_writes_enabled=True
            )
        )


def test_duplicate_hashes_cannot_assign_two_roles():
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    config = json.dumps(
        [
            {"subject_id": str(uuid4()), "role": role, "token_sha256": digest}
            for role in ("USER", "WORKER")
        ]
    )
    with pytest.raises(ValueError, match="鉴权配置无效"):
        Authenticator(config)
