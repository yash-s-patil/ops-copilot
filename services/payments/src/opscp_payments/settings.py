"""Configuration of the two payments programs, read once at start-up."""

import base64
from pathlib import Path
from typing import Literal, Self
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    load_pem_public_key,
)
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    FilePath,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from opscp_common.logging import LogLevel
from opscp_common.settings import LOCAL, Environment

WORKER_NAME = "payments-worker"
SIM_NAME = "payments-sim"
SUBMIT_ROLE = "Payments.Submit"
ENTRA_AUTHORITY = "login.microsoftonline.com"
PROD: Environment = "prod"
_ISSUER = r"^https?://[^\s/]+/?$"

type AuthMode = Literal["entra", "dev"]


class ManagedIdentity(BaseModel):
    """The worker asks Microsoft Entra ID for a token as its own identity. No secret."""

    model_config = ConfigDict(frozen=True)

    scope: str
    client_id: UUID


class DevIssuer(BaseModel):
    """The worker signs its own token with the key that `make env` created on this machine."""

    model_config = ConfigDict(frozen=True)

    issuer: str
    audience: str
    private_key_pem: SecretStr


class EntraCallers(BaseModel):
    """The payment service accepts tokens Microsoft Entra ID issued for it."""

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    audience: str
    authority: str = ENTRA_AUTHORITY


class DevCallers(BaseModel):
    """The payment service accepts tokens signed with this machine's development key."""

    model_config = ConfigDict(frozen=True)

    issuer: str
    audience: str
    public_key_pem: str


class _Program(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    environment: Environment
    log_level: LogLevel = "INFO"
    limits_file: FilePath
    auth_mode: AuthMode
    dev_issuer: str | None = Field(default=None, pattern=_ISSUER)

    @property
    def json_logs(self) -> bool:
        return self.environment != LOCAL

    def _dev_issuer(self) -> str:
        """A mode made for testing never runs in Azure."""
        if self.environment != LOCAL:
            raise ValueError("AUTH_MODE=dev is allowed only when ENVIRONMENT is local")
        if self.dev_issuer is None:
            raise ValueError("AUTH_MODE=dev needs DEV_ISSUER")
        return self.dev_issuer


class WorkerSettings(_Program):
    payments_url: AnyHttpUrl
    payments_scope: str | None = None
    azure_client_id: UUID | None = None
    payments_api_audience: str | None = None
    dev_issuer_private_key: SecretStr | None = None
    chaos_enabled: bool = False
    heartbeat_file: Path | None = None
    """Touched after every poll, so that whatever runs the worker can tell it is alive."""

    @field_validator(
        "dev_issuer",
        "payments_scope",
        "heartbeat_file",
        "azure_client_id",
        "payments_api_audience",
        "dev_issuer_private_key",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def _is_complete(self) -> Self:
        if self.chaos_enabled and self.environment == PROD:
            raise ValueError("CHAOS_ENABLED is not allowed when ENVIRONMENT is prod")
        if self.environment != LOCAL and self.payments_url.scheme != "https":
            raise ValueError("PAYMENTS_URL must be https outside local development")
        self.identity()
        return self

    def identity(self) -> ManagedIdentity | DevIssuer:
        """How the worker proves to the payment service that it is the worker."""
        if self.auth_mode == "entra":
            if self.payments_scope is None or self.azure_client_id is None:
                raise ValueError("AUTH_MODE=entra needs PAYMENTS_SCOPE and AZURE_CLIENT_ID")
            return ManagedIdentity(scope=self.payments_scope, client_id=self.azure_client_id)
        issuer = self._dev_issuer()
        if self.payments_api_audience is None or self.dev_issuer_private_key is None:
            raise ValueError("AUTH_MODE=dev needs PAYMENTS_API_AUDIENCE and DEV_ISSUER_PRIVATE_KEY")
        pem = _rsa_pem(self.dev_issuer_private_key.get_secret_value(), "DEV_ISSUER_PRIVATE_KEY")
        return DevIssuer(
            issuer=issuer, audience=self.payments_api_audience, private_key_pem=SecretStr(pem)
        )


class SimSettings(_Program):
    payments_api_audience: str = Field(min_length=1)
    tenant_id: UUID | None = None
    dev_issuer_public_key: str | None = None
    sim_allow_forced_outcome: bool = False

    @field_validator("dev_issuer", "tenant_id", "dev_issuer_public_key", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def _is_complete(self) -> Self:
        if self.environment == PROD:
            raise ValueError("The payment simulator does not run when ENVIRONMENT is prod")
        self.callers()
        return self

    def callers(self) -> EntraCallers | DevCallers:
        """Whose tokens the simulator accepts."""
        if self.auth_mode == "entra":
            if self.tenant_id is None:
                raise ValueError("AUTH_MODE=entra needs TENANT_ID")
            return EntraCallers(tenant_id=self.tenant_id, audience=self.payments_api_audience)
        issuer = self._dev_issuer()
        if self.dev_issuer_public_key is None:
            raise ValueError("AUTH_MODE=dev needs DEV_ISSUER_PUBLIC_KEY")
        return DevCallers(
            issuer=issuer,
            audience=self.payments_api_audience,
            public_key_pem=_rsa_pem(self.dev_issuer_public_key, "DEV_ISSUER_PUBLIC_KEY"),
        )


def _rsa_pem(encoded: str, name: str) -> str:
    """The key is kept base64-encoded so that it fits on one line of an environment file."""
    private = name.endswith("PRIVATE_KEY")
    try:
        pem = base64.b64decode(encoded, validate=True)
        key = load_pem_private_key(pem, password=None) if private else load_pem_public_key(pem)
        if not isinstance(key, RSAPrivateKey if private else RSAPublicKey):
            raise TypeError
    except (ValueError, TypeError) as error:
        raise ValueError(f"{name} must be a base64-encoded PEM RSA key") from error
    return pem.decode("ascii")
