import os
import shutil

import pytest
from rollforge_harbor_adapter.manifest import content_manifest


def test_digest_ignores_absolute_location_and_modification_time(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    file = original / "instruction.md"
    file.write_text("task")
    copied = tmp_path / "copy"
    shutil.copytree(original, copied)
    os.utime(copied / "instruction.md", (1, 1))
    assert content_manifest(original).digest == content_manifest(copied).digest
    file.write_text("changed")
    assert content_manifest(original).digest != content_manifest(copied).digest


def test_digest_preserves_executable_semantics(tmp_path):
    file = tmp_path / "test.sh"
    file.write_text("exit 0")
    before = content_manifest(tmp_path)
    file.chmod(0o755)
    assert before.digest != content_manifest(tmp_path).digest


def test_symlink_escape_is_rejected(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    outside = tmp_path / "secret"
    outside.write_text("private")
    (task / "link").symlink_to(outside)
    with pytest.raises(ValueError, match="Symlinks"):
        content_manifest(task)
