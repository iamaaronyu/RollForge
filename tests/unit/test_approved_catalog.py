import json

import pytest
from rollforge_api.catalog import load_catalog


def test_empty_catalog_is_disabled():
    assert load_catalog(None) == {}


@pytest.mark.parametrize("kind", ["public", "symlink", "oversized", "malformed"])
def test_catalog_rejects_unsafe_or_invalid_file(tmp_path, kind):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps([{"id": "sample", "secret": "synthetic-private-value"}]))
    path.chmod(0o600)
    if kind == "public":
        path.chmod(0o644)
    elif kind == "symlink":
        link = tmp_path / "link.json"
        link.symlink_to(path)
        path = link
    elif kind == "oversized":
        path.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(ValueError) as error:
        load_catalog(path)
    assert "synthetic-private-value" not in str(error.value)
