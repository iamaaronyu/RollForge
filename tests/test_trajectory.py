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


def test_saved_real_harbor_trajectory():
    directory = os.environ.get("ROLLFORGE_TEST_NATIVE_OUTPUT")
    if not directory:
        pytest.skip("需要已保存的真实 Harbor 轨迹")
    value = (Path(directory) / "agent/trajectory.json").read_text()
    result = project_trajectory(value)
    assert len(result.steps) == len(json.loads(value)["steps"])
    assert any(step.tools for step in result.steps)
