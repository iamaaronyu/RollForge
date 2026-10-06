import hashlib
import hmac

from pydantic import TypeAdapter
from rollforge_schemas.api import Credential, Principal


class Authenticator:
    def __init__(self, config: str):
        try:
            self.credentials = TypeAdapter(list[Credential]).validate_json(config)
            hashes = [credential.token_sha256 for credential in self.credentials]
            if len(set(hashes)) != len(hashes):
                raise ValueError
        except ValueError:
            # 错误消息不包含原始配置或验证上下文。
            raise ValueError("API 鉴权配置无效") from None

    def authenticate(self, token: str) -> Principal | None:
        if not 32 <= len(token) <= 256:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        principal = None
        for credential in self.credentials:
            if hmac.compare_digest(digest, credential.token_sha256):
                principal = Principal(subject_id=credential.subject_id, role=credential.role)
        return principal
