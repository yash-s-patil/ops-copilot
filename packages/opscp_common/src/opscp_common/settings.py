"""Process configuration, read once from the environment."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

POSTGRES_ASYNC_DRIVER = "postgresql+asyncpg"


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATABASE_", extra="ignore", frozen=True)

    host: str
    port: int = Field(default=5432, gt=0, le=65535)
    name: str
    user: str
    password: SecretStr

    def url(self) -> URL:
        return URL.create(
            drivername=POSTGRES_ASYNC_DRIVER,
            username=self.user,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.name,
        )
