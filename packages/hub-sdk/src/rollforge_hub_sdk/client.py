from typing import TypeVar
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, SecretStr
from rollforge_schemas.api import (
    ApiError,
    ClaimRequest,
    ErrorCode,
    FinishRequest,
    JobCreateRequest,
    RenewRequest,
)
from rollforge_schemas.domain import HealthResponse
from rollforge_schemas.execution import ExecutionList, JobList, JobView, Lease, TrialView
from rollforge_schemas.registry import AssetRevision, RevisionCreate, RevisionList

Model = TypeVar("Model", bound=BaseModel)


class HubError(Exception):
    def __init__(self, status_code: int, code: ErrorCode | None = None):
        self.status_code = status_code
        self.code = code
        # 不把原始响应、请求头、认证或连接信息附加到异常。
        super().__init__(f"Hub 请求失败（HTTP {status_code}）")


class HubTransportError(Exception):
    pass


class HubProtocolError(Exception):
    pass


class HubClient:
    def __init__(
        self,
        base_url: str,
        token: SecretStr | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        try:
            url = urlsplit(base_url)
            # 提前解析端口，避免 HTTPX 的 URL 错误回显意外放入地址的敏感内容。
            _port = url.port
        except ValueError:
            raise ValueError("Hub 地址格式无效") from None
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise ValueError("Hub 地址必须为不含凭证的 HTTP(S) 地址")
        headers = {"Authorization": f"Bearer {token.get_secret_value()}"} if token else {}
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=10,
            headers=headers,
            transport=transport,
            follow_redirects=False,
        )

    async def _request(self, method: str, path: str, body=None, *, params=None):
        try:
            response = await self._client.request(method, path, json=body, params=params)
        except httpx.RequestError:
            raise HubTransportError("Hub 网络请求失败") from None
        if not 200 <= response.status_code < 300:
            try:
                code = ApiError.model_validate(response.json()).code
            except ValueError:
                code = None
            raise HubError(response.status_code, code)
        return response

    @staticmethod
    def _decode(response: httpx.Response, model: type[Model]) -> Model:
        try:
            return model.model_validate(response.json())
        except ValueError:
            raise HubProtocolError("Hub 响应与契约不符") from None

    async def health(self) -> HealthResponse:
        response = await self._request("GET", "/health/live")
        return self._decode(response, HealthResponse)

    async def create_job(self, body: JobCreateRequest) -> JobView:
        response = await self._request("POST", "/api/v1/jobs", body.model_dump(mode="json"))
        return self._decode(response, JobView)

    async def get_job(self, job_id: UUID) -> JobView:
        response = await self._request("GET", f"/api/v1/jobs/{job_id}")
        return self._decode(response, JobView)

    async def list_jobs(self, *, limit: int = 20, after: UUID | None = None) -> JobList:
        params = {"limit": limit}
        if after is not None:
            params["after"] = str(after)
        return self._decode(await self._request("GET", "/api/v1/jobs", params=params), JobList)

    async def list_executions(
        self, job_id: UUID, *, limit: int = 20, after: int = 0
    ) -> ExecutionList:
        return self._decode(
            await self._request(
                "GET", f"/api/v1/jobs/{job_id}/executions", params={"limit": limit, "after": after}
            ),
            ExecutionList,
        )

    async def create_revision(self, body: RevisionCreate) -> AssetRevision:
        response = await self._request(
            "POST", "/api/v1/registry/revisions", body.model_dump(mode="json")
        )
        return self._decode(response, AssetRevision)

    async def get_revision(self, asset_id: UUID, revision: int) -> AssetRevision:
        return self._decode(
            await self._request("GET", f"/api/v1/registry/assets/{asset_id}/revisions/{revision}"),
            AssetRevision,
        )

    async def list_revisions(
        self, asset_id: UUID, *, limit: int = 20, after: int = 0
    ) -> RevisionList:
        return self._decode(
            await self._request(
                "GET",
                f"/api/v1/registry/assets/{asset_id}/revisions",
                params={"limit": limit, "after": after},
            ),
            RevisionList,
        )

    async def claim(self, body: ClaimRequest | None = None) -> Lease | None:
        response = await self._request(
            "POST", "/api/v1/worker/leases/claim", (body or ClaimRequest()).model_dump(mode="json")
        )
        return None if response.status_code == 204 else self._decode(response, Lease)

    async def renew(self, body: RenewRequest) -> Lease:
        response = await self._request(
            "POST", "/api/v1/worker/leases/renew", body.model_dump(mode="json")
        )
        return self._decode(response, Lease)

    async def finish(self, body: FinishRequest) -> TrialView:
        response = await self._request(
            "POST", "/api/v1/worker/leases/finish", body.model_dump(mode="json")
        )
        return self._decode(response, TrialView)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()
