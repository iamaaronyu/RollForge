"""ATIF 白名单投影与不补造缺失信息。"""

import json
import os
from pathlib import Path

import pytest
from rollforge_harbor_adapter.trajectory import UnsupportedTrajectory, project_trajectory


def document():
    return {
        "schema_version": "ATIF-v1.8",
        "steps": [
            {"step_id": 1, "source": "user", "message": "synthetic"},
            {
                "step_id": 2,
                "source": "agent",
                "message": "<script>text</script>",
                "metrics": {"completion_tokens": 0},
                "extra": {"private_metadata": "omitted"},
                "tool_calls": [
                    {
                        "tool_call_id": "call-1",
                        "function_name": "read",
                        "arguments": {"path": "synthetic.txt"},
                    }
                ],
                "observation": {
                    "results": [
                        {
                            "source_call_id": "call-1",
                            "content": [
                                {"type": "text", "text": "synthetic result"},
                                {
                                    "type": "image",
                                    "source": {
                                        "media_type": "image/png",
                                        "path": "https://invalid.test/private",
                                    },
                                },
                            ],
                        }
                    ]
                },
            },
        ],
    }


def test_projection_missing_zero_and_media():
    media_paths = {"input.png"}
    data = document()
    data["steps"][0]["message"] = [
        {"type": "text", "text": "inspect"},
        {"type": "image", "source": {"media_type": "image/png", "path": "input.png"}},
        {
            "type": "audio",
            "source": {
                "media_type": "audio/wav",
                "path": "https://media.invalid/audio.wav",
                "duration_sec": 1.5,
            },
        },
    ]
    result = project_trajectory(json.dumps(data), available_media_paths=media_paths)
    assert result.projection_version == 2
    assert result.steps[0].media[0].artifact_path == "input.png"
    assert result.steps[0].media[1].artifact_path is None
    assert result.steps[0].media[1].duration_sec == 1.5
    assert "media.invalid" not in result.model_dump_json()
    step = result.steps[1]
    assert step.usage.completion_tokens == 0
    assert step.usage.prompt_tokens is None and step.reasoning is None
    assert step.observations[0].call_id == step.tools[0].call_id
    assert step.observations[0].text == "synthetic result\n[图片附件]"
    assert "invalid.test" not in result.model_dump_json()
    assert "private_metadata" not in result.model_dump_json()
    assert step.message == "<script>text</script>"


@pytest.mark.parametrize("change", ["version", "order", "reference", "metrics", "many"])
def test_projection_rejects_inconsistent_or_unsupported(change):
    data = document()
    if change == "version":
        data["schema_version"] = "ATIF-v2.0"
    if change == "order":
        data["steps"][1]["step_id"] = 3
    if change == "reference":
        data["steps"][1]["observation"]["results"][0]["source_call_id"] = "absent"
    if change == "metrics":
        data["steps"][1]["metrics"]["completion_tokens"] = -1
    if change == "many":
        data["steps"] = data["steps"] * 501
    with pytest.raises(UnsupportedTrajectory, match="轨迹格式不受支持"):
        project_trajectory(json.dumps(data))


def test_projection_rejects_invalid_media_source_without_echoing_path():
    data = document()
    data["steps"][0]["message"] = [
        {"type": "image", "source": {"media_type": "text/html", "path": "secret-key"}}
    ]
    with pytest.raises(UnsupportedTrajectory, match="轨迹格式不受支持") as error:
        project_trajectory(json.dumps(data), available_media_paths={"secret-key"})
    assert "secret-key" not in str(error.value)


def test_projection_expands_embedded_subagents_and_redacts_external_references():
    data = document()
    data["steps"][1]["observation"]["results"][0]["subagent_trajectory_ref"] = [
        {"trajectory_id": "sub-1"},
        {"trajectory_path": "https://private.invalid/trace.json?token=secret"},
    ]
    data["subagent_trajectories"] = [
        {
            "schema_version": "ATIF-v1.8",
            "trajectory_id": "sub-1",
            "agent": {"name": "helper"},
            "steps": [{"step_id": 1, "source": "agent", "message": "child output"}],
        }
    ]

    result = project_trajectory(json.dumps(data))

    observation = result.steps[1].observations[0]
    assert observation.subagent_trajectory_ids == ("sub-1",)
    assert observation.unresolved_subagent_count == 1
    assert result.subagents[0].agent_name == "helper"
    assert result.subagents[0].steps[0].message == "child output"
    assert "private.invalid" not in result.model_dump_json()
    assert "token=secret" not in result.model_dump_json()


@pytest.mark.parametrize(
    "change",
    [
        "duplicate",
        "missing-id",
        "missing-agent",
        "too-deep",
        "too-many-subagents",
        "too-many-steps",
    ],
)
def test_projection_rejects_invalid_or_overdeep_embedded_subagents(change):
    data = document()
    child = {
        "schema_version": "ATIF-v1.8",
        "trajectory_id": "child",
        "agent": {"name": "helper"},
        "steps": [{"step_id": 1, "source": "agent", "message": "synthetic"}],
    }
    if change == "duplicate":
        data["subagent_trajectories"] = [child, child]
    elif change == "missing-id":
        child.pop("trajectory_id")
        data["subagent_trajectories"] = [child]
    elif change == "missing-agent":
        child.pop("agent")
        data["subagent_trajectories"] = [child]
    elif change == "too-many-subagents":
        data["subagent_trajectories"] = []
        for ordinal in range(16):
            branch = {
                "schema_version": "ATIF-v1.8",
                "trajectory_id": f"child-{ordinal}",
                "agent": {"name": "helper"},
                "steps": [{"step_id": 1, "source": "agent", "message": "synthetic"}],
                "subagent_trajectories": [],
            }
            for nested_ordinal in range(4):
                branch["subagent_trajectories"].append(
                    {
                        "schema_version": "ATIF-v1.8",
                        "trajectory_id": f"nested-{ordinal}-{nested_ordinal}",
                        "agent": {"name": "nested"},
                        "steps": [{"step_id": 1, "source": "agent", "message": "synthetic"}],
                    }
                )
            data["subagent_trajectories"].append(branch)
    elif change == "too-many-steps":
        data["steps"] = [
            {"step_id": ordinal, "source": "agent", "message": "synthetic"}
            for ordinal in range(1, 1001)
        ]
        data["subagent_trajectories"] = []
        for ordinal in range(5):
            data["subagent_trajectories"].append(
                {
                    "schema_version": "ATIF-v1.8",
                    "trajectory_id": f"child-{ordinal}",
                    "agent": {"name": "helper"},
                    "steps": [
                        {"step_id": step, "source": "agent", "message": "synthetic"}
                        for step in range(1, 1001)
                    ],
                }
            )
    else:
        current = child
        for ordinal in range(4):
            nested = {
                "schema_version": "ATIF-v1.8",
                "trajectory_id": f"child-{ordinal}",
                "agent": {"name": "helper"},
                "steps": [{"step_id": 1, "source": "agent", "message": "synthetic"}],
            }
            current["subagent_trajectories"] = [nested]
            current = nested
        data["subagent_trajectories"] = [child]
    with pytest.raises(UnsupportedTrajectory, match="轨迹格式不受支持"):
        project_trajectory(json.dumps(data))


def test_saved_real_harbor_trajectory():
    directory = os.environ.get("ROLLFORGE_TEST_NATIVE_OUTPUT")
    if not directory:
        pytest.skip("需要已保存的真实 Harbor 轨迹")
    value = (Path(directory) / "agent/trajectory.json").read_text()
    result = project_trajectory(value)
    assert len(result.steps) == len(json.loads(value)["steps"])
    assert any(step.tools for step in result.steps)
