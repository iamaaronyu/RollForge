"""真实 PostgreSQL/MinIO 的产物所有权与完整性边界。"""

import json
from uuid import uuid4

import test_object_store_minio as storage_tests
from rollforge_object_store.bundle import BundlePublisher
from rollforge_schemas.api import FinishRequest, JobCreateRequest
from rollforge_schemas.storage import ExecutionScope
from test_job_api_postgres import clients, reference
from test_worker_postgres import request

server = storage_tests.server
store = storage_tests.store
output = storage_tests.output


async def publish(engine, server, store, output, sdk):
    job = await sdk["user"].create_job(
        JobCreateRequest(job_id=uuid4(), snapshot=request().snapshot)
    )
    lease = await sdk["worker"].claim()
    scope = ExecutionScope(
        job_id=job.job_id,
        trial_id=lease.trial_id,
        execution_id=lease.execution_id,
        fencing_token=lease.fencing_token,
    )
    result = BundlePublisher(store, sensitive_values=()).publish(scope, output)
    await sdk["worker"].finish(FinishRequest(lease=reference(lease), result=result))
    return scope, result


def storage_settings(app, server, store):
    app.state.settings = app.state.settings.model_copy(
        update={
            "object_storage_endpoint": server.endpoint,
            "object_storage_bucket": store.bucket,
            "object_storage_access_key": server.access,
            "object_storage_secret_key": server.secret,
            "object_storage_allow_local_http": True,
        }
    )


async def test_artifact_owner_and_corruption(engine, server, store, output):
    async with clients(engine) as (app, sdk, raw, _ids, tokens):
        storage_settings(app, server, store)
        scope, _result = await publish(engine, server, store, output, sdk)
        base = f"/api/v1/jobs/{scope.job_id}/executions/{scope.execution_id}"
        headers = {"Authorization": "Bearer " + tokens["user"]}
        index = await raw.get(base + "/artifacts", headers=headers)
        assert index.status_code == 200
        assert {item["path"] for item in index.json()["files"]} == {
            "result.json",
            "agent/trajectory.json",
        }
        assert index.json()["result"]["rewards"] == {"reward": 0}
        for name, expected in [("other_user", 404), ("worker", 403)]:
            assert (
                await raw.get(
                    base + "/artifacts", headers={"Authorization": "Bearer " + tokens[name]}
                )
            ).status_code == expected
        assert (
            await raw.get(base + "/artifact-text", headers=headers, params={"path": "../private"})
        ).status_code == 404
        store.client.put_object(
            Bucket=store.bucket, Key=scope.prefix + "/files/result.json", Body=b"changed"
        )
        assert (
            await raw.get(base + "/artifact-text", headers=headers, params={"path": "result.json"})
        ).status_code == 503
        store.client.put_object(Bucket=store.bucket, Key=scope.manifest_key, Body=b"changed")
        assert (await raw.get(base + "/artifacts", headers=headers)).status_code == 503


async def test_preview_bounds_plaintext_and_cross_execution(engine, server, store, output):
    (output / "html.txt").write_text('<script>alert("synthetic")</script>')
    (output / "large.txt").write_bytes(b"x" * (1024 * 1024 + 1))
    (output / "binary.dat").write_bytes(b"\xff\x00")
    async with clients(engine) as (app, sdk, raw, _ids, tokens):
        storage_settings(app, server, store)
        scope, _result = await publish(engine, server, store, output, sdk)
        base = f"/api/v1/jobs/{scope.job_id}/executions/{scope.execution_id}"
        headers = {"Authorization": "Bearer " + tokens["user"]}
        for path, status in [("large.txt", 413), ("binary.dat", 422)]:
            assert (
                await raw.get(base + "/artifact-text", headers=headers, params={"path": path})
            ).status_code == status
        response = await raw.get(
            base + "/artifact-text", headers=headers, params={"path": "html.txt"}
        )
        assert (
            response.status_code == 200 and response.text == '<script>alert("synthetic")</script>'
        )
        assert response.headers["content-type"].startswith("text/plain")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
        assert (
            await raw.get(
                f"/api/v1/jobs/{scope.job_id}/executions/{uuid4()}/artifacts", headers=headers
            )
        ).status_code == 404


async def test_trajectory_authorization_and_integrity(engine, server, store, output):
    trajectory = {
        "schema_version": "ATIF-v1.8",
        "steps": [
            {
                "step_id": 1,
                "source": "agent",
                "message": "<script>synthetic</script>",
                "metrics": {"completion_tokens": 0},
            }
        ],
    }
    (output / "agent/trajectory.json").write_text(json.dumps(trajectory))
    async with clients(engine) as (app, sdk, raw, _ids, tokens):
        storage_settings(app, server, store)
        scope, _ = await publish(engine, server, store, output, sdk)
        endpoint = f"/api/v1/jobs/{scope.job_id}/executions/{scope.execution_id}/trajectory"
        headers = {"Authorization": "Bearer " + tokens["user"]}
        response = await raw.get(endpoint, headers=headers)
        assert response.status_code == 200
        step = response.json()["steps"][0]
        assert step["usage"]["completion_tokens"] == 0
        assert step["usage"]["prompt_tokens"] is None and step["reasoning"] is None
        for name, status in [("other_user", 404), ("worker", 403)]:
            assert (
                await raw.get(endpoint, headers={"Authorization": "Bearer " + tokens[name]})
            ).status_code == status
        store.client.put_object(
            Bucket=store.bucket, Key=scope.prefix + "/files/agent/trajectory.json", Body=b"changed"
        )
        assert (await raw.get(endpoint, headers=headers)).status_code == 503


async def test_unsupported_trajectory_keeps_raw_preview(engine, server, store, output):
    async with clients(engine) as (app, sdk, raw, _ids, tokens):
        storage_settings(app, server, store)
        scope, _ = await publish(engine, server, store, output, sdk)
        base = f"/api/v1/jobs/{scope.job_id}/executions/{scope.execution_id}"
        headers = {"Authorization": "Bearer " + tokens["user"]}
        assert (await raw.get(base + "/trajectory", headers=headers)).status_code == 422
        response = await raw.get(
            base + "/artifact-text", headers=headers, params={"path": "agent/trajectory.json"}
        )
        assert response.status_code == 200 and json.loads(response.text) == {"steps": []}
