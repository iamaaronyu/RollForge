"""先上传不可变文件，再发布 Manifest；进程重启可重放整个上传。"""

import json
import os
import stat
import tempfile
from pathlib import Path

from pydantic import SecretStr
from rollforge_harbor_adapter.results import summarize_result
from rollforge_schemas.execution import FailureReason, ResultCommit
from rollforge_schemas.runtime import ResultOutcome
from rollforge_schemas.storage import ArtifactManifest, ExecutionScope, StoredFile

from rollforge_object_store.s3 import MAX_OBJECT_BYTES, ObjectConflict, ObjectStoreError, digest


def canonical(manifest: ArtifactManifest) -> bytes:
    return json.dumps(
        manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def result_commit(manifest: ArtifactManifest, manifest_digest: str) -> ResultCommit:
    summary = manifest.summary
    reason = None
    if summary.outcome == ResultOutcome.UNVERIFIED:
        reason = FailureReason.UNVERIFIED
    elif summary.outcome == ResultOutcome.RUNTIME_FAILED:
        name = summary.exception_type or ""
        reason = (
            FailureReason.AGENT_ERROR
            if "Agent" in name
            else FailureReason.VERIFIER_ERROR
            if "Verifier" in name or "Reward" in name
            else FailureReason.INFRA_ERROR
        )
    return ResultCommit(
        outcome=summary.outcome,
        rewards=summary.rewards if summary.outcome == ResultOutcome.SCORED else None,
        failure_reason=reason,
        manifest_key=manifest.scope.manifest_key,
        manifest_digest=manifest_digest,
    )


def _read_safe(root: Path, relative: str) -> bytes:
    """每个路径组件都拒绝符号链接；非普通文件不读取。"""
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = relative.split("/")
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(leaf, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("执行输出仅支持普通文件")
            data = stream.read(MAX_OBJECT_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) > MAX_OBJECT_BYTES:
        raise ValueError("单个执行输出超过 64 MiB")
    return data


def read_safe(root: Path, relative: str) -> bytes:
    try:
        return _read_safe(root, relative)
    except OSError:
        raise ObjectStoreError("执行输出路径无法安全读取") from None


def checked_summary(result_bytes: bytes, paths: set[str]):
    with tempfile.TemporaryDirectory(prefix="rollforge-result-") as temporary:
        frozen = Path(temporary) / "result.json"
        descriptor = os.open(frozen, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(result_bytes)
        try:
            summary = summarize_result(Path(temporary))
        except (ValueError, TypeError, KeyError):
            raise ObjectStoreError("原始结果不符合固定 Runtime 契约") from None
    return summary.model_copy(
        update={
            "trajectory_state": "PRESENT" if "agent/trajectory.json" in paths else "MISSING",
            "artifact_manifest_state": "PRESENT"
            if "artifacts/manifest.json" in paths
            else "MISSING",
        }
    )


class BundlePublisher:
    def __init__(self, store, *, sensitive_values: tuple[SecretStr, ...]):
        self.store = store
        self.secrets = tuple(
            value.get_secret_value().encode()
            for value in sensitive_values
            if value.get_secret_value()
        )

    def prepare(self, scope: ExecutionScope, root: Path) -> ArtifactManifest:
        if not root.is_dir() or root.is_symlink():
            raise ValueError("执行输出目录不安全")
        records = []
        result_bytes = None
        total = 0
        # 全部预检完成前不上传，避免晚发现凭证时已有输出进入远端。
        for path in sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix()):
            if path.is_symlink():
                raise ValueError("执行输出不允许符号链接")
            if path.is_dir():
                continue
            relative = path.relative_to(root).as_posix()
            if any(value in relative.encode() for value in self.secrets):
                raise ValueError("执行输出存在凭证命中，拒绝上传")
            if (
                path.name == ".env"
                or path.name.startswith(".env.")
                or path.suffix in {".key", ".pem"}
            ):
                raise ValueError("执行输出包含凭证文件")
            data = read_safe(root, relative)
            if any(value in data for value in self.secrets):
                raise ValueError("执行输出存在凭证命中，拒绝上传")
            total += len(data)
            if total > 128 * 1024 * 1024 or len(records) >= 512:
                raise ValueError("执行输出总大小或文件数超限")
            records.append(
                StoredFile(
                    path=relative,
                    size=len(data),
                    digest=digest(data),
                )
            )
            if relative == "result.json":
                result_bytes = data
        if result_bytes is None:
            raise ValueError("缺少原始 result.json")
        # 从已经检查过的字节解析，避免摘要对应的数据与动态文件内容分离。
        paths = {file.path for file in records}
        summary = checked_summary(result_bytes, paths)
        return ArtifactManifest(scope=scope, summary=summary, files=tuple(records))

    def publish(self, scope: ExecutionScope, root: Path) -> ResultCommit:
        manifest = self.prepare(scope, root)
        for file in manifest.files:
            data = read_safe(root, file.path)
            if len(data) != file.size or digest(data) != file.digest:
                raise ObjectConflict("执行输出在上传期间改变")
            self.store.put_immutable(scope.prefix + "/files/" + file.path, data)
        payload = canonical(manifest)
        # Manifest 是本次上传的完成标记；前面的任意文件失败均不会发布它。
        self.store.put_immutable(scope.manifest_key, payload)
        return self.verify(scope, digest(payload))

    def inspect(self, scope: ExecutionScope, expected_digest: str) -> ArtifactManifest:
        payload = self.store.read(scope.manifest_key, maximum=4 * 1024 * 1024)
        if digest(payload) != expected_digest:
            raise ObjectConflict("Manifest 摘要不一致")
        try:
            manifest = ArtifactManifest.model_validate_json(payload)
        except ValueError:
            raise ObjectStoreError("Manifest 契约无效") from None
        if manifest.scope != scope or canonical(manifest) != payload:
            raise ObjectConflict("Manifest 执行身份或规范格式不一致")
        return manifest

    def verify(self, scope: ExecutionScope, expected_digest: str) -> ResultCommit:
        manifest = self.inspect(scope, expected_digest)
        result_bytes = None
        for file in manifest.files:
            data = self.store.read_verified(
                scope.prefix + "/files/" + file.path, file.digest, file.size
            )
            if file.path == "result.json":
                result_bytes = data
        summary = checked_summary(result_bytes, {file.path for file in manifest.files})
        if summary != manifest.summary:
            raise ObjectConflict("Manifest 汇总与原始 Runtime 结果不一致")
        return result_commit(manifest, expected_digest)
