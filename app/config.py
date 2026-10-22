"""集中读取配置；真实密码只从环境变量或本机 .env 获取。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = Field(min_length=1)
    postgres_user: str = Field(min_length=1)
    postgres_password: SecretStr = Field(min_length=1)

    redis_host: str = "127.0.0.1"
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_db: int = Field(default=0, ge=0)
    redis_password: SecretStr | None = None
    redis_socket_timeout: float = Field(default=0.3, gt=0, le=5)
    guide_cache_prefix: str = Field(default="campus-assistant:guides:v1", min_length=1)

    jwt_secret_key: SecretStr = Field(min_length=32)
    access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)

    model_provider: Literal["mock", "deepseek"] = "mock"
    model_name: str = Field(default="deepseek-flash", min_length=1, max_length=100)
    model_api_key: SecretStr | None = None
    model_base_url: str = "https://api.deepseek.com"
    model_timeout_seconds: float = Field(default=30, ge=0.1, le=120)
    model_max_tokens: int = Field(default=1024, ge=1, le=8192)
    mock_chunk_chars: int = Field(default=32, ge=1, le=256)
    mock_delay_seconds: float = Field(default=0.04, ge=0, le=2)
    mock_failure_after_chunks: int = Field(default=2, ge=0, le=100)
    chat_context_prefix: str = "campus-assistant:context:v1"
    chat_context_ttl: int = Field(default=600, ge=10, le=86400)
    chat_context_rounds: int = Field(default=6, ge=1, le=20)
    chat_context_max_chars: int = Field(default=12000, ge=100, le=64000)

    @property
    def database_url(self) -> URL:
        # URL.create 能正确处理密码里的 @、%、/ 等特殊字符。
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
