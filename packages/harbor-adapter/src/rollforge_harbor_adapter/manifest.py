import hashlib
import json
from pathlib import Path

from rollforge_schemas.runtime import ContentManifest, FileRecord


def content_manifest(directory: Path) -> ContentManifest:
    """Canonical identity independent of archive metadata and absolute location.

    S0 supports regular files/directories only. Symlinks and special files are
    rejected instead of silently following references outside the task/result.
    Permission identity preserves only executable semantics, not host-specific modes.
    """
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("Manifest root must be a real directory")
    records = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlinks are not supported in S0 manifests")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Special files are not supported in S0 manifests")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        records.append(
            FileRecord(
                path=path.relative_to(directory).as_posix(),
                size=path.stat().st_size,
                digest="sha256:" + digest.hexdigest(),
                executable=bool(path.stat().st_mode & 0o111),
            )
        )
    canonical = json.dumps(
        [record.model_dump() for record in records],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return ContentManifest(digest="sha256:" + hashlib.sha256(canonical).hexdigest(), files=records)
