"""执行产物契约；不含凭证或宿主绝对路径。"""

from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from rollforge_schemas.execution import Contract
from rollforge_schemas.runtime import NativeResultSummary


class ExecutionScope(Contract):
    job_id: UUID
    trial_id: UUID
    execution_id: UUID
    fencing_token: int = Field(ge=1)

    @property
    def prefix(self) -> str:
        return f"jobs/{self.job_id}/trials/{self.trial_id}/executions/{self.execution_id}"

    @property
    def manifest_key(self) -> str:
        return self.prefix + "/manifest.json"


class StoredFile(Contract):
    path: str = Field(min_length=1, max_length=768)
    size: int = Field(ge=0, le=64 * 1024 * 1024)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def relative_path(cls, value):
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or path.as_posix() != value
            or any(part in {".", ".."} for part in path.parts)
            or "\\" in value
            or any(ord(char) < 32 for char in value)
            or value == "."
            or len(value.encode()) > 768
        ):
            raise ValueError("对象路径必须为规范的相对文件路径")
        return value


class ArtifactManifest(Contract):
    schema_version: Literal["1"] = "1"
    scope: ExecutionScope
    summary: NativeResultSummary
    files: tuple[StoredFile, ...] = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def unique_ordered_paths(self):
        names = [file.path for file in self.files]
        if names != sorted(set(names)) or "result.json" not in names:
            raise ValueError("Manifest 文件必须唯一、有序且包含原始 result.json")
        if sum(file.size for file in self.files) > 128 * 1024 * 1024:
            raise ValueError("执行输出总大小超过 128 MiB")
        return self
