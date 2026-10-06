"""Viewer 的版本化白名单投影；原始 ATIF 保留在对象存储。"""

from typing import Literal

from pydantic import Field

from rollforge_schemas.execution import Contract


class Usage(Contract):
    prompt_tokens: int | None = Field(default=None, ge=0, strict=True)
    completion_tokens: int | None = Field(default=None, ge=0, strict=True)
    cached_tokens: int | None = Field(default=None, ge=0, strict=True)
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ToolAction(Contract):
    call_id: str
    name: str
    arguments_text: str


class ToolObservation(Contract):
    call_id: str | None = None
    text: str | None = None


class TimelineStep(Contract):
    step_id: int = Field(ge=1, strict=True)
    source: Literal["system", "user", "agent"]
    timestamp: str | None = None
    message: str
    reasoning: str | None = None
    usage: Usage | None = None
    tools: tuple[ToolAction, ...] = ()
    observations: tuple[ToolObservation, ...] = ()
    copied_context: bool | None = None


class TrajectoryView(Contract):
    projection_version: Literal[1] = 1
    source_version: str
    steps: tuple[TimelineStep, ...]
