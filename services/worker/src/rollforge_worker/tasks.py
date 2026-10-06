"""Task 包只含普通文件；验证归档摘要与规范内容摘要后才交给 Harbor。"""

import io
import os
import stat
import tarfile
import tempfile
from pathlib import Path

from rollforge_harbor_adapter.manifest import content_manifest
from rollforge_schemas.runnable import digest_bytes, safe_key


def unpack_task(data: bytes, destination: Path, archive_digest: str, task_digest: str):
    if len(data) > 64 * 1024 * 1024 or digest_bytes(data) != archive_digest:
        raise ValueError("Task archive digest or size is invalid")
    if destination.exists():
        raise ValueError("Task extraction requires a fresh directory")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        if len(members) > 1024 or sum(member.size for member in members) > 64 * 1024 * 1024:
            raise ValueError("Task archive exceeds current budget")
        names = set()
        for member in members:
            safe_key(member.name.rstrip("/") if member.isdir() else member.name)
            if member.name in names or not (member.isfile() or member.isdir()):
                raise ValueError("Task archive contains unsafe entries")
            names.add(member.name)
        destination.mkdir(mode=0o700, parents=True)
        for member in members:
            path = destination / member.name
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("Task file is unreadable")
            with stream, path.open("xb") as output:
                path.chmod(0o700 if member.mode & 0o111 else 0o600)
                output.write(stream.read())
    if content_manifest(destination).digest != task_digest:
        raise ValueError("Task content does not match frozen revision")


def pack_task(directory: Path) -> tuple[bytes, str]:
    manifest = content_manifest(directory)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for record in manifest.files:
            archive.add(directory / record.path, arcname=record.path, recursive=False)
    data = output.getvalue()
    if len(data) > 64 * 1024 * 1024:
        raise ValueError("Task archive exceeds current budget")
    return data, manifest.digest


def protected_file(path: Path):
    info = path.lstat()
    parent = path.parent.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_mode & 0o077
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_mode & 0o077
    ):
        raise ValueError("Private runtime files require mode 0600 and parent 0700")


def atomic_write(path: Path, data: str):
    descriptor, temporary = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
