from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from pydantic import SecretStr
from rollforge_object_store.s3 import ObjectStoreError, S3ObjectStore


@pytest.mark.parametrize(
    "endpoint, local",
    [
        ("http://127.0.0.1:9000", False),
        ("http://192.168.0.2:9000", True),
        ("https://secret@example.invalid", False),
        ("https://example.invalid?token=secret", False),
        ("https://example.invalid/#secret", False),
        ("https://example.invalid:wrong", False),
    ],
)
def test_unsafe_storage_endpoint_never_creates_client(endpoint, local, monkeypatch):
    client = Mock()
    monkeypatch.setattr("rollforge_object_store.s3.boto3.client", client)
    with pytest.raises(ValueError) as error:
        S3ObjectStore(
            endpoint,
            "test",
            SecretStr("synthetic-access"),
            SecretStr("synthetic-secret"),
            allow_local_http=local,
        )
    assert "secret" not in str(error.value)
    client.assert_not_called()


def test_raw_sdk_response_never_appears_in_storage_error(monkeypatch):
    client = Mock()
    monkeypatch.setattr("rollforge_object_store.s3.boto3.client", Mock(return_value=client))
    store = S3ObjectStore(
        "https://example.invalid", "test", SecretStr("access"), SecretStr("secret")
    )
    client.get_object.side_effect = ClientError(
        {
            "Error": {"Code": "Denied", "Message": "synthetic-private-token"},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        },
        "GetObject",
    )
    with pytest.raises(ObjectStoreError) as error:
        store.read("key")
    assert "synthetic-private-token" not in str(error.value)
    assert error.value.__suppress_context__
