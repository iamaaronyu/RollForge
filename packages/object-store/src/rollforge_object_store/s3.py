"""S3 原子条件写入与完整内容校验；SDK 错误不回显。"""

import hashlib
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import SecretStr

MAX_OBJECT_BYTES = 64 * 1024 * 1024


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class ObjectStoreError(Exception):
    pass


class ObjectConflict(ObjectStoreError):
    pass


class S3ObjectStore:
    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key: SecretStr,
        secret_key: SecretStr,
        *,
        allow_local_http: bool = False,
    ):
        try:
            url = urlsplit(endpoint)
            _port = url.port
        except ValueError:
            raise ValueError("对象存储地址无效") from None
        local_http = (
            allow_local_http
            and url.scheme == "http"
            and url.hostname
            in {
                "localhost",
                "127.0.0.1",
                "::1",
            }
        )
        if (
            (url.scheme != "https" and not local_http)
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError("对象存储需要无凭证 HTTPS 地址；HTTP 仅允许显式本机测试")
        if not access_key.get_secret_value() or not secret_key.get_secret_value():
            raise ValueError("对象存储凭证为空")
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key.get_secret_value(),
            aws_secret_access_key=secret_key.get_secret_value(),
            region_name="us-east-1",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                proxies={},
                connect_timeout=5,
                read_timeout=30,
                retries={"mode": "standard", "max_attempts": 3},
            ),
        )

    def read(self, key: str, maximum: int = MAX_OBJECT_BYTES) -> bytes:
        if not 0 <= maximum <= MAX_OBJECT_BYTES:
            raise ValueError("对象读取大小超限")
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            body = response["Body"]
            try:
                data = body.read(maximum + 1)
            finally:
                body.close()
        except (ClientError, BotoCoreError, OSError):
            raise ObjectStoreError("对象读取失败") from None
        if len(data) > maximum:
            raise ObjectStoreError("对象读取大小超限")
        return data

    def read_verified(self, key: str, expected: str, size: int) -> bytes:
        data = self.read(key, size)
        if len(data) != size or digest(data) != expected:
            raise ObjectConflict("对象内容与 Manifest 不一致")
        return data

    def put_immutable(self, key: str, data: bytes) -> None:
        if len(data) > MAX_OBJECT_BYTES:
            raise ValueError("对象上传大小超限")
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                IfNoneMatch="*",
                Metadata={"sha256": digest(data).removeprefix("sha256:")},
            )
        except ClientError as error:
            if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise ObjectStoreError("对象条件写入失败，请重试") from None
        except (BotoCoreError, OSError):
            raise ObjectStoreError("对象上传失败") from None
        self.read_verified(key, digest(data), len(data))

    def close(self):
        self.client.close()
