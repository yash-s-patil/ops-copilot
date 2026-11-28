"""Configuration of the local issuer. It holds a signing key, so it runs only on a laptop."""

import base64
from typing import Literal, Self

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from opscp_common.logging import LogLevel

SERVICE_NAME = "dev-issuer"
_SECRET_CHARS_MIN = 32


class IssuerSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    environment: Literal["local"]
    log_level: LogLevel = "INFO"
    dev_issuer: str = Field(pattern=r"^https?://[^\s/]+/?$")
    dev_issuer_private_key: SecretStr
    agent_api_audience: str = Field(min_length=1)
    agent_api_client_id: str = Field(min_length=1)
    agent_client_secret: SecretStr = Field(min_length=_SECRET_CHARS_MIN)
    mcp_api_audience: str = Field(min_length=1)
    web_client_id: str = Field(min_length=1)
    web_client_secret: SecretStr = Field(min_length=_SECRET_CHARS_MIN)

    @model_validator(mode="after")
    def _key_is_usable(self) -> Self:
        self.private_key_pem()
        return self

    def private_key_pem(self) -> str:
        try:
            pem = base64.b64decode(self.dev_issuer_private_key.get_secret_value(), validate=True)
            if not isinstance(load_pem_private_key(pem, password=None), RSAPrivateKey):
                raise TypeError
        except (ValueError, TypeError) as error:
            raise ValueError(
                "DEV_ISSUER_PRIVATE_KEY must be a base64-encoded PEM RSA private key"
            ) from error
        return pem.decode("ascii")
