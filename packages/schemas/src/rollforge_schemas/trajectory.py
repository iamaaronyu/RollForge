"""Viewer 的版本化白名单投影；原始 ATIF 保留在对象存储。"""

from __future__ import annotations

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
    subagent_trajectory_ids: tuple[str, ...] = ()
    external_subtrajectory_paths: tuple[str, ...] = ()
    unresolved_subagent_count: int = Field(default=0, ge=0, strict=True)


class MediaReference(Contract):
    kind: Literal["image", "audio"]
    media_type: str
    artifact_path: str | None = None
    duration_sec: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class TimelineStep(Contract):
    step_id: int = Field(ge=1, strict=True)
    source: Literal["system", "user", "agent"]
    timestamp: str | None = None
    message: str
    reasoning: str | None = None
    usage: Usage | None = None
    tools: tuple[ToolAction, ...] = ()
    observations: tuple[ToolObservation, ...] = ()
    media: tuple[MediaReference, ...] = ()
    copied_context: bool | None = None


class SubtrajectoryView(Contract):
    trajectory_id: str
    agent_name: str
    source_version: str
    steps: tuple[TimelineStep, ...]
    subagents: tuple[SubtrajectoryView, ...] = ()


class TrajectoryView(Contract):
    projection_version: Literal[3] = 3
    source_version: str
    steps: tuple[TimelineStep, ...]
    subagents: tuple[SubtrajectoryView, ...] = ()
