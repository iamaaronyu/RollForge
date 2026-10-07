import pytest
from pydantic import ValidationError
from rollforge_schemas.registry import ModelSpec, TaskSpec, revision_digest


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@gateway.test",
        "https://gateway.test?key=x",
        "https://gateway.test/v1",
        "file:///tmp/config",
    ],
)
def test_model_registry_rejects_credential_or_ambiguous_endpoint(url):
    with pytest.raises(ValidationError):
        ModelSpec(model_base_url=url)


def test_registry_normalized_payload_digest_and_no_arbitrary_fields():
    first = ModelSpec(model_base_url="https://gateway.test/")
    second = ModelSpec(model_base_url="https://gateway.test")
    assert revision_digest(first) == revision_digest(second)
    with pytest.raises(ValidationError):
        TaskSpec(archive_key="../private", archive_digest="sha256:" + "a" * 64)
    with pytest.raises(ValidationError):
        ModelSpec(model_base_url="https://gateway.test", api_key="synthetic")
