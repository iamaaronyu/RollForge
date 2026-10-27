from fastapi.testclient import TestClient
from pydantic import SecretStr
from rollforge_api.main import create_app
from rollforge_common.settings import Settings


def test_scaffold_health_and_readiness():
    settings = Settings(database_url=SecretStr("sqlite+aiosqlite:///:memory:"))
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").json()["status"] == "ready"
        platform = client.get("/api/v1/platform").json()
        assert platform["execution_enabled"] is False
        assert platform["global_execution_limit"] == 1


def test_unavailable_database_does_not_expose_credentials():
    settings = Settings(database_url=SecretStr("sqlite+aiosqlite:////nonexistent/secret.db"))
    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}
