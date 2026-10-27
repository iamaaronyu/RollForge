"""独立 MinIO 或隔离随机 bucket 的 S3-compatible 服务真实集成测试。"""

import ipaddress
import json
import os
import secrets
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from dotenv import dotenv_values
from pydantic import SecretStr
from rollforge_object_store.bundle import BundlePublisher, canonical
from rollforge_object_store.s3 import ObjectConflict, ObjectStoreError, S3ObjectStore, digest
from rollforge_schemas.storage import ExecutionScope


class MinioServer:
    def __init__(self, binary, root):
        self.binary = binary
        self.root = root
        self.access = SecretStr(secrets.token_urlsafe(16))
        self.secret = SecretStr(secrets.token_urlsafe(32))
        self.process = None
        self.log = None
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.endpoint = f"http://127.0.0.1:{self.port}"
        self.can_restart = True

    def start(self):
        descriptor = os.open(
            self.root / "minio.local.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
        )
        self.log = os.fdopen(descriptor, "wb")
        self.process = subprocess.Popen(
            [self.binary, "server", str(self.root / "data"), "--address", f"127.0.0.1:{self.port}"],
            env={
                "PATH": os.environ.get("PATH", ""),
                "MINIO_ROOT_USER": self.access.get_secret_value(),
                "MINIO_ROOT_PASSWORD": self.secret.get_secret_value(),
                "MINIO_BROWSER": "off",
            },
            stdout=self.log,
            stderr=self.log,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("独立 MinIO 启动失败；检查本地受保护日志")
            try:
                response = httpx.get(
                    self.endpoint + "/minio/health/ready", trust_env=False, timeout=1
                )
                if response.status_code == 200:
                    return
            except httpx.RequestError:
                pass
            time.sleep(0.1)
        raise RuntimeError("独立 MinIO 就绪超时")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        if self.log:
            self.log.close()

    def store(self, bucket):
        return S3ObjectStore(self.endpoint, bucket, self.access, self.secret, allow_local_http=True)


class ExternalS3Server:
    """已运行的 loopback S3 服务；测试只创建并清理随机 bucket。"""

    can_restart = False

    def __init__(self, env_file):
        config_path = Path(env_file)
        metadata = config_path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_mode & 0o077
            or metadata.st_uid != os.geteuid()
        ):
            raise RuntimeError("外部 S3 测试配置必须是当前用户拥有的私密普通文件")
        values = dotenv_values(config_path)
        try:
            access = values["MINIO_ROOT_USER"]
            secret = values["MINIO_ROOT_PASSWORD"]
            port = int(values.get("ROLLFORGE_MINIO_HOST_PORT") or "59000")
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("外部 S3 测试配置缺少必要字段") from None
        if not access or not secret or not 1 <= port <= 65535:
            raise RuntimeError("外部 S3 测试配置值无效")
        self.endpoint = values.get("ROLLFORGE_TEST_S3_ENDPOINT") or f"http://127.0.0.1:{port}"
        parsed = urlsplit(self.endpoint)
        try:
            endpoint_port = parsed.port
            loopback = (
                parsed.hostname == "localhost"
                or ipaddress.ip_address(parsed.hostname or "").is_loopback
            )
        except ValueError:
            endpoint_port = None
            loopback = False
        if (
            parsed.scheme not in {"http", "https"}
            or not loopback
            or not endpoint_port
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError("外部 S3 测试 endpoint 必须显式指向本机 loopback")
        self.access = SecretStr(access)
        self.secret = SecretStr(secret)

    def store(self, bucket):
        return S3ObjectStore(self.endpoint, bucket, self.access, self.secret, allow_local_http=True)


def test_external_s3_config_requires_private_file(tmp_path):
    env_file = tmp_path / "infra.env"
    env_file.write_text("MINIO_ROOT_USER=test-user\nMINIO_ROOT_PASSWORD=test-secret\n")
    env_file.chmod(0o644)

    with pytest.raises(RuntimeError, match="私密普通文件"):
        ExternalS3Server(env_file)


def test_external_s3_config_rejects_non_loopback_endpoint(tmp_path):
    env_file = tmp_path / "infra.env"
    env_file.write_text(
        "MINIO_ROOT_USER=test-user\n"
        "MINIO_ROOT_PASSWORD=test-secret\n"
        "ROLLFORGE_TEST_S3_ENDPOINT=https://objects.example:443\n"
    )
    env_file.chmod(0o600)

    with pytest.raises(RuntimeError, match="loopback"):
        ExternalS3Server(env_file)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    binary = os.getenv("ROLLFORGE_TEST_MINIO_BINARY")
    if binary:
        binary = str(Path(binary).resolve(strict=True))
        instance = MinioServer(binary, tmp_path_factory.mktemp("rollforge-minio"))
        try:
            instance.start()
            yield instance
        finally:
            instance.stop()
        return
    env_file = os.getenv("ROLLFORGE_TEST_OBJECT_STORE_ENV_FILE")
    if not env_file:
        pytest.skip(
            "需要固定版本 ROLLFORGE_TEST_MINIO_BINARY 或私密 ROLLFORGE_TEST_OBJECT_STORE_ENV_FILE"
        )
    yield ExternalS3Server(env_file)


@pytest.fixture
def store(server):
    bucket = "rollforge-test-" + uuid4().hex
    client = server.store(bucket)
    created = False
    try:
        client.client.create_bucket(Bucket=bucket)
        created = True
        yield client
    finally:
        try:
            if created:
                # 只删除本测试随机新建的 bucket，绝不清理已有 bucket。
                paginator = client.client.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=bucket):
                    objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                    if objects:
                        client.client.delete_objects(Bucket=bucket, Delete={"Objects": objects})
                client.client.delete_bucket(Bucket=bucket)
        finally:
            client.close()


@pytest.fixture
def output(tmp_path):
    root = tmp_path / "native"
    root.mkdir()
    (root / "result.json").write_text(
        json.dumps(
            {
                "started_at": "2026-10-06T00:00:00Z",
                "finished_at": "2026-10-06T00:00:01Z",
                "verifier_result": {"rewards": {"reward": 0.0}},
                "exception_info": None,
            }
        )
    )
    (root / "agent").mkdir()
    (root / "agent/trajectory.json").write_text('{"steps":[]}')
    return root


@pytest.fixture
def scope():
    return ExecutionScope(job_id=uuid4(), trial_id=uuid4(), execution_id=uuid4(), fencing_token=1)


def test_real_concurrent_conditional_put_has_one_winner(store):
    def write(payload):
        try:
            store.put_immutable("race/object", payload)
            return payload
        except ObjectStoreError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [b"first", b"second"]))
    winners = [data for data in results if data is not None]
    assert len(winners) == 1
    assert store.read("race/object") == winners[0]
    store.put_immutable("race/object", winners[0])


def test_real_partial_upload_can_resume_with_fresh_client(server, store, scope, output):
    class InterruptedUpload:
        def __init__(self):
            self.count = 0

        def put_immutable(self, key, data):
            store.put_immutable(key, data)
            self.count += 1
            if self.count == 1:
                raise ObjectStoreError("注入真实写入后的响应丢失")

    with pytest.raises(ObjectStoreError):
        BundlePublisher(InterruptedUpload(), sensitive_values=()).publish(scope, output)
    with pytest.raises(ObjectStoreError):
        store.read(scope.manifest_key)
    fresh = server.store(store.bucket)
    try:
        publisher = BundlePublisher(fresh, sensitive_values=())
        accepted = publisher.publish(scope, output)
        assert accepted.rewards == {"reward": 0.0}
        assert publisher.publish(scope, output) == accepted
        shutil.rmtree(output)
        assert publisher.verify(scope, accepted.manifest_digest) == accepted
    finally:
        fresh.close()


@pytest.mark.parametrize("checkpoint", ["first-file", "manifest"])
def test_real_killed_publisher_can_recover(server, store, scope, output, checkpoint):
    # 只终止独立上传子进程；这里不模拟 Worker 或 Sandbox 生命周期。
    script = """
import json
import signal
import sys
from pathlib import Path
from pydantic import SecretStr
from rollforge_object_store.bundle import BundlePublisher
from rollforge_object_store.s3 import S3ObjectStore
from rollforge_schemas.storage import ExecutionScope

config = json.load(sys.stdin)
scope = ExecutionScope.model_validate(config['scope'])
store = S3ObjectStore(config['endpoint'], config['bucket'],
    SecretStr(config['access']), SecretStr(config['secret']), allow_local_http=True)
class CheckpointStore:
    def put_immutable(self, key, data):
        store.put_immutable(key, data)
        if config['checkpoint'] == 'first-file' or key == scope.manifest_key:
            print('checkpoint', flush=True)
            signal.pause()
BundlePublisher(CheckpointStore(), sensitive_values=()).publish(scope, Path(config['output']))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        env={"PATH": os.environ.get("PATH", "")},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        # 临时测试凭证仅通过私有管道传入，不进命令行、文件或测试输出。
        json.dump(
            {
                "endpoint": server.endpoint,
                "bucket": store.bucket,
                "access": server.access.get_secret_value(),
                "secret": server.secret.get_secret_value(),
                "scope": scope.model_dump(mode="json"),
                "output": str(output),
                "checkpoint": checkpoint,
            },
            process.stdin,
        )
        process.stdin.close()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=15), "上传子进程未到达真实写入检查点"
            assert process.stdout.readline().strip() == "checkpoint"
        process.kill()
        process.wait(timeout=10)
        assert process.returncode == -signal.SIGKILL
        publisher = BundlePublisher(store, sensitive_values=())
        expected = publisher.prepare(scope, output)
        first = expected.files[0]
        store.read_verified(scope.prefix + "/files/" + first.path, first.digest, first.size)
        if checkpoint == "first-file":
            with pytest.raises(ObjectStoreError):
                store.read(scope.manifest_key)
        fresh = server.store(store.bucket)
        try:
            restored = BundlePublisher(fresh, sensitive_values=())
            if checkpoint == "manifest":
                accepted = restored.verify(scope, digest(canonical(expected)))
            else:
                accepted = restored.publish(scope, output)
            assert accepted.rewards == {"reward": 0.0}
            shutil.rmtree(output)
            assert restored.verify(scope, accepted.manifest_digest) == accepted
        finally:
            fresh.close()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        process.stdout.close()
        if not process.stdin.closed:
            process.stdin.close()


def test_real_minio_restart_preserves_verified_result(server, store, scope, output):
    if not server.can_restart:
        pytest.skip("外部 S3 服务由部署管理；测试不会重启共享服务")
    result = BundlePublisher(store, sensitive_values=()).publish(scope, output)
    server.stop()
    server.start()
    fresh = server.store(store.bucket)
    try:
        assert (
            BundlePublisher(fresh, sensitive_values=()).verify(scope, result.manifest_digest)
            == result
        )
    finally:
        fresh.close()


def test_real_corrupt_file_and_missing_file_are_rejected(store, scope, output):
    publisher = BundlePublisher(store, sensitive_values=())
    result = publisher.publish(scope, output)
    key = scope.prefix + "/files/agent/trajectory.json"
    # 测试管理员直接写坏对象，生产权限不得允许这种无条件覆盖。
    store.client.put_object(Bucket=store.bucket, Key=key, Body=b"corrupt")
    with pytest.raises(ObjectConflict):
        publisher.verify(scope, result.manifest_digest)
    store.client.delete_object(Bucket=store.bucket, Key=key)
    with pytest.raises(ObjectStoreError):
        publisher.verify(scope, result.manifest_digest)


def test_real_retry_execution_uses_separate_object_namespace(store, scope, output):
    publisher = BundlePublisher(store, sensitive_values=())
    original = publisher.publish(scope, output)
    retry = scope.model_copy(update={"execution_id": uuid4(), "fencing_token": 2})
    replacement = publisher.publish(retry, output)
    assert original.manifest_key != replacement.manifest_key
    with pytest.raises(ObjectStoreError):
        publisher.verify(retry, original.manifest_digest)
    assert publisher.verify(scope, original.manifest_digest) == original


def test_real_harbor_output_roundtrip(store, scope):
    source = os.getenv("ROLLFORGE_TEST_NATIVE_OUTPUT")
    credential_file = os.getenv("ROLLFORGE_TEST_OUTPUT_CREDENTIAL_FILE")
    if not source or not credential_file:
        pytest.skip("需要本地真实 Harbor 输出和凭证扫描文件路径")
    values = tuple(
        SecretStr(value)
        for name, value in dotenv_values(credential_file).items()
        if value and ("KEY" in name or "TOKEN" in name)
    )
    publisher = BundlePublisher(store, sensitive_values=values)
    result = publisher.publish(scope, Path(source))
    assert result.rewards == {"reward": 1.0}
    assert publisher.verify(scope, result.manifest_digest) == result
