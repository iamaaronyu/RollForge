"""基于 Harbor 0.24.0 ATIF 模型的有界只读投影，不加载运行时依赖。"""

import json
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from rollforge_schemas.trajectory import (
    MediaReference,
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


class Call(Input):
    tool_call_id: str
    function_name: str
    arguments: dict[str, Any]


class Observation(Input):
    source_call_id: str | None = None
    content: str | list[Part] | None = None


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


def project_trajectory(
    value: str, *, available_media_paths: set[str] | None = None
) -> TrajectoryView:
    """拒绝不支持/损坏的结构，不回传解析错误中的原始内容。"""
    try:
        document = Document.model_validate_json(value)
        projected = []
        media_paths = available_media_paths or set()
        for ordinal, step in enumerate(document.steps, 1):
            if step.step_id != ordinal:
                raise UnsupportedTrajectory()
            calls = step.tool_calls or []
            ids = {call.tool_call_id for call in calls}
            if len(ids) != len(calls):
                raise UnsupportedTrajectory()
            observations = step.observation.results if step.observation else []
            if any(
                item.source_call_id is not None and item.source_call_id not in ids
                for item in observations
            ):
                raise UnsupportedTrajectory()
            usage = None
            if step.metrics is not None:
                usage = Usage.model_validate(
                    {name: step.metrics.get(name) for name in Usage.model_fields}
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
                    observations=tuple(
                        ToolObservation(
                            call_id=item.source_call_id, text=text_content(item.content)
                        )
                        for item in observations
                    ),
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
        return TrajectoryView(source_version=document.schema_version, steps=tuple(projected))
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise UnsupportedTrajectory("轨迹格式不受支持，请查看原始文件") from None
