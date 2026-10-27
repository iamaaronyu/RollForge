"""基于 Harbor 0.24.0 ATIF 模型的有界只读投影，不加载运行时依赖。"""

from __future__ import annotations

import json
import math
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from rollforge_schemas.trajectory import (
    MediaReference,
    SubtrajectoryView,
    TimelineStep,
    ToolAction,
    ToolObservation,
    TrajectoryView,
    Usage,
)


class UnsupportedTrajectory(ValueError):
    pass


class Input(BaseModel):
    # 只投影明确支持的字段；未知扩展仍保留在原始文件中。
    model_config = ConfigDict(strict=True)


class Part(Input):
    type: Literal["text", "image", "audio"]
    text: str | None = None
    source: dict[str, Any] | None = None

    model_config = ConfigDict(strict=True, extra="forbid")

    @model_validator(mode="after")
    def validate_content(self):
        if self.type == "text":
            if self.text is None or self.source is not None:
                raise ValueError("ATIF 文本内容结构无效")
        elif self.source is None or self.text is not None:
            raise ValueError("ATIF 媒体内容结构无效")
        return self


IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
AUDIO_ALIASES = {
    "audio/mp3": "audio/mpeg",
    "audio/mpga": "audio/mpeg",
    "audio/x-mpeg": "audio/mpeg",
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/vnd.wave": "audio/wav",
    "audio/x-m4a": "audio/mp4",
    "audio/m4a": "audio/mp4",
    "audio/x-aac": "audio/aac",
    "audio/x-flac": "audio/flac",
    "audio/x-aiff": "audio/aiff",
}
AUDIO_TYPES = {
    "audio/wav",
    "audio/mpeg",
    "audio/mp4",
    "audio/aac",
    "audio/ogg",
    "audio/flac",
    "audio/webm",
    "audio/aiff",
}


def text_content(value: str | list[Part] | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    parts = []
    for part in value:
        if part.type == "text":
            if part.text is None:
                raise UnsupportedTrajectory()
            parts.append(part.text)
        else:
            parts.append("[图片附件]" if part.type == "image" else "[音频附件]")
    return "\n".join(parts)


def media_content(
    value: str | list[Part] | None, available_paths: set[str]
) -> tuple[MediaReference, ...]:
    if not isinstance(value, list):
        return ()
    references = []
    for part in value:
        if part.type == "text":
            continue
        source = part.source
        if not isinstance(source, dict):
            raise UnsupportedTrajectory()
        media_type = source.get("media_type")
        path = source.get("path")
        if not isinstance(media_type, str) or not isinstance(path, str):
            raise UnsupportedTrajectory()
        allowed_source_keys = (
            {"media_type", "path", "duration_sec"}
            if part.type == "audio"
            else {"media_type", "path"}
        )
        if set(source) - allowed_source_keys:
            raise UnsupportedTrajectory()
        media_type = media_type.strip().lower()
        if part.type == "audio":
            media_type = AUDIO_ALIASES.get(media_type, media_type)
        if (part.type == "image" and media_type not in IMAGE_TYPES) or (
            part.type == "audio" and media_type not in AUDIO_TYPES
        ):
            raise UnsupportedTrajectory()
        duration = source.get("duration_sec") if part.type == "audio" else None
        if duration is not None and (
            not isinstance(duration, (int, float))
            or isinstance(duration, bool)
            or not math.isfinite(duration)
            or duration < 0
        ):
            raise UnsupportedTrajectory()
        # ATIF paths may be URLs or host paths. Only expose a path already
        # present in the accepted execution manifest; never pass remote URLs to
        # the browser or turn them into server-side fetches.
        artifact_path = path if path in available_paths else None
        references.append(
            MediaReference(
                kind=part.type,
                media_type=media_type,
                artifact_path=artifact_path,
                duration_sec=duration,
            )
        )
    return tuple(references)


def manifest_json_path(path: str | None, available_paths: set[str]) -> bool:
    return (
        path is not None
        and path in available_paths
        and PurePosixPath(path).suffix.lower() == ".json"
    )


class Call(Input):
    tool_call_id: str
    function_name: str
    arguments: dict[str, Any]


class Observation(Input):
    source_call_id: str | None = None
    content: str | list[Part] | None = None
    subagent_trajectory_ref: list[SubagentTrajectoryRef] | None = None


class SubagentTrajectoryRef(Input):
    trajectory_id: str | None = Field(default=None, max_length=256)
    session_id: str | None = Field(default=None, max_length=256)
    trajectory_path: str | None = Field(default=None, max_length=768)

    @model_validator(mode="after")
    def require_resolvable_reference(self):
        if self.trajectory_id is None and self.trajectory_path is None:
            raise ValueError("ATIF 子轨迹引用不可解析")
        return self


class Feedback(Input):
    results: list[Observation]


class Step(Input):
    step_id: int = Field(ge=1)
    source: Literal["system", "user", "agent"]
    timestamp: str | None = None
    message: str | list[Part]
    reasoning_content: str | None = None
    metrics: dict[str, Any] | None = None
    tool_calls: list[Call] | None = None
    observation: Feedback | None = None
    is_copied_context: bool | None = None


class Document(Input):
    schema_version: str = Field(pattern=r"^ATIF-v1\.[0-8]$")
    steps: list[Step] = Field(min_length=1, max_length=1000)
    trajectory_id: str | None = None
    agent: dict[str, Any] | None = None
    subagent_trajectories: list[Document] | None = Field(default=None, max_length=16)


MAX_SUBAGENT_DEPTH = 3
MAX_TOTAL_SUBAGENTS = 64
MAX_TOTAL_STEPS = 5000


def project_steps(document: Document, media_paths: set[str], budget: dict, child_ids: set[str]):
    if len(document.steps) + budget["steps"] > MAX_TOTAL_STEPS:
        raise UnsupportedTrajectory()
    budget["steps"] += len(document.steps)
    projected = []
    for ordinal, step in enumerate(document.steps, 1):
        if step.step_id != ordinal:
            raise UnsupportedTrajectory()
        calls = step.tool_calls or []
        call_ids = {call.tool_call_id for call in calls}
        if len(call_ids) != len(calls):
            raise UnsupportedTrajectory()
        observations = step.observation.results if step.observation else []
        if any(
            item.source_call_id is not None and item.source_call_id not in call_ids
            for item in observations
        ):
            raise UnsupportedTrajectory()
        usage = None
        if step.metrics is not None:
            usage = Usage.model_validate(
                {name: step.metrics.get(name) for name in Usage.model_fields}
            )
        projected_observations = []
        for item in observations:
            refs = item.subagent_trajectory_ref or []
            referenced_ids = tuple(
                dict.fromkeys(
                    reference.trajectory_id
                    for reference in refs
                    if reference.trajectory_id in child_ids
                )
            )
            projected_observations.append(
                ToolObservation(
                    call_id=item.source_call_id,
                    text=text_content(item.content),
                    subagent_trajectory_ids=referenced_ids,
                    external_subtrajectory_paths=tuple(
                        dict.fromkeys(
                            reference.trajectory_path
                            for reference in refs
                            if manifest_json_path(reference.trajectory_path, media_paths)
                            and reference.trajectory_id not in child_ids
                        )
                    ),
                    unresolved_subagent_count=sum(
                        reference.trajectory_id not in child_ids
                        and not manifest_json_path(reference.trajectory_path, media_paths)
                        for reference in refs
                    ),
                )
            )
        projected.append(
            TimelineStep(
                step_id=step.step_id,
                source=step.source,
                timestamp=step.timestamp,
                message=text_content(step.message),
                reasoning=step.reasoning_content,
                usage=usage,
                copied_context=step.is_copied_context,
                tools=tuple(
                    ToolAction(
                        call_id=call.tool_call_id,
                        name=call.function_name,
                        arguments_text=json.dumps(
                            call.arguments, ensure_ascii=False, allow_nan=False
                        ),
                    )
                    for call in calls
                ),
                observations=tuple(projected_observations),
                media=(
                    *media_content(step.message, media_paths),
                    *(
                        reference
                        for item in observations
                        for reference in media_content(item.content, media_paths)
                    ),
                ),
            )
        )
    return tuple(projected)


def project_subagents(document: Document, media_paths: set[str], budget: dict, depth: int = 1):
    children = document.subagent_trajectories or []
    if not children:
        return ()
    if depth > MAX_SUBAGENT_DEPTH or budget["subagents"] + len(children) > MAX_TOTAL_SUBAGENTS:
        raise UnsupportedTrajectory()
    ids = [child.trajectory_id for child in children]
    if any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids):
        raise UnsupportedTrajectory()
    if any(
        not isinstance(child.agent, dict)
        or not isinstance(child.agent.get("name"), str)
        or not child.agent["name"]
        for child in children
    ):
        raise UnsupportedTrajectory()
    budget["subagents"] += len(children)
    return tuple(
        SubtrajectoryView(
            trajectory_id=child.trajectory_id,
            agent_name=child.agent["name"],
            source_version=child.schema_version,
            steps=project_steps(
                child,
                media_paths,
                budget,
                {
                    sub.trajectory_id
                    for sub in child.subagent_trajectories or []
                    if sub.trajectory_id
                },
            ),
            subagents=project_subagents(child, media_paths, budget, depth + 1),
        )
        for child in children
    )


def project_trajectory(
    value: str, *, available_media_paths: set[str] | None = None
) -> TrajectoryView:
    """拒绝不支持/损坏的结构，不回传解析错误中的原始内容。"""
    try:
        document = Document.model_validate_json(value)
        media_paths = available_media_paths or set()
        direct_ids = {
            child.trajectory_id
            for child in document.subagent_trajectories or []
            if child.trajectory_id
        }
        budget = {"steps": 0, "subagents": 0}
        steps = project_steps(document, media_paths, budget, direct_ids)
        subagents = project_subagents(document, media_paths, budget)
        return TrajectoryView(
            source_version=document.schema_version,
            steps=steps,
            subagents=subagents,
        )
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise UnsupportedTrajectory("轨迹格式不受支持，请查看原始文件") from None
