"""基于 Harbor 0.24.0 ATIF 模型的有界只读投影，不加载运行时依赖。"""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from rollforge_schemas.trajectory import (
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
            parts.append("[图片未展开]" if part.type == "image" else "[音频未展开]")
    return "\n".join(parts)


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


def project_trajectory(value: str) -> TrajectoryView:
    """拒绝不支持/损坏的结构，不回传解析错误中的原始内容。"""
    try:
        document = Document.model_validate_json(value)
        projected = []
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
                )
            )
        return TrajectoryView(source_version=document.schema_version, steps=tuple(projected))
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise UnsupportedTrajectory("轨迹格式不受支持，请查看原始文件") from None
