"""Process configuration, read once from the environment."""

import re
from typing import Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

POSTGRES_ASYNC_DRIVER = "postgresql+asyncpg"
LOCAL = "local"
LOCAL_BLOB_HOSTS = frozenset({"127.0.0.1", "localhost", "azurite"})

_ACCOUNT_URL = re.compile(r"https://[a-z0-9]{3,24}\.blob\.core\.windows\.net/?")
_BLOB_ENDPOINT = "blobendpoint"
_ENDPOINT_SUFFIX = "endpoint"

type Environment = Literal["local", "demo", "prod"]


def _is_emulator(connection_string: str) -> bool:
    """Read as the storage SDK reads it: names without regard to case, each given once."""
    endpoints: dict[str, str | None] = {}
    seen: set[str] = set()
    for segment in connection_string.split(";"):
        name, _, value = segment.partition("=")
        key = name.strip().lower()
        if key in seen:
            return False
        seen.add(key)
        if key.endswith(_ENDPOINT_SUFFIX):
            endpoints[key] = urlsplit(value).hostname
    return _BLOB_ENDPOINT in endpoints and all(
        host in LOCAL_BLOB_HOSTS for host in endpoints.values()
    )


class StorageSettings(BaseSettings):
    """Where policy documents are kept.

    In Azure the account is reached by URL with the program's managed identity. A connection
    string carries an account key, so it is accepted only for the emulator on this machine.
    """

    model_config = SettingsConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    environment: Environment
    policies_container: str = Field(min_length=3)
    blob_account_url: str | None = None
    azure_client_id: UUID | None = None
    blob_connection_string: SecretStr | None = None

    @field_validator("blob_account_url", "azure_client_id", "blob_connection_string", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def _one_way_in(self) -> Self:
        url, secret = self.blob_account_url, self.blob_connection_string
        if (url is None) == (secret is None):
            raise ValueError("Set exactly one of BLOB_ACCOUNT_URL and BLOB_CONNECTION_STRING")
        if url is not None:
            self._check_account(url)
        if secret is not None:
            self._check_emulator(secret.get_secret_value())
        return self

    def _check_account(self, url: str) -> None:
        if _ACCOUNT_URL.fullmatch(url) is None:
            raise ValueError("BLOB_ACCOUNT_URL must be https://<account>.blob.core.windows.net")
        if self.azure_client_id is None:
            raise ValueError("AZURE_CLIENT_ID must name the managed identity that reads storage")

    def _check_emulator(self, connection_string: str) -> None:
        if self.environment != LOCAL:
            raise ValueError("BLOB_CONNECTION_STRING is allowed only when ENVIRONMENT is local")
        if not _is_emulator(connection_string):
            raise ValueError("BLOB_CONNECTION_STRING must point at the local emulator")


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DATABASE_", extra="ignore", frozen=True, hide_input_in_errors=True
    )

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
