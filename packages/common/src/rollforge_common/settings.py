from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ROLLFORGE_", env_file=".env", extra="ignore")
    environment: str = "development"
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://rollforge:rollforge@localhost:5432/rollforge"
    )
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    object_storage_endpoint: str = "http://localhost:9000"
    api_credentials: SecretStr = SecretStr("[]")
    control_plane_writes_enabled: bool = False
    object_storage_bucket: str = "rollforge"
    object_storage_access_key: SecretStr = SecretStr("")
    object_storage_secret_key: SecretStr = SecretStr("")
    object_storage_allow_local_http: bool = False
