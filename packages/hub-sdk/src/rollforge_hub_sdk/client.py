import httpx
from rollforge_schemas.domain import HealthResponse


class HubClient:
    def __init__(self, base_url: str):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10)

    async def health(self) -> HealthResponse:
        response = await self._client.get("/health/live")
        response.raise_for_status()
        return HealthResponse.model_validate(response.json())

    async def close(self) -> None:
        await self._client.aclose()
