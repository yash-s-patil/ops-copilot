"""Configuration of the MCP server, read once at start-up."""

import base64
from typing import Literal, Self
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    load_pem_public_key,
)
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    DirectoryPath,
    Field,
    FilePath,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from opscp_common.logging import LogLevel
from opscp_common.settings import LOCAL, Environment

SERVICE_NAME = "mcp"
ENTRA_AUTHORITY = "login.microsoftonline.com"
_APPROVAL_CURVE = "secp256r1"

type AuthMode = Literal["entra", "eval", "dev"]


class EntraSignIn(BaseModel):
    """Callers hold tokens that Microsoft Entra ID issued for this server."""

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    client_id: UUID
    audience: str
    authority: str = ENTRA_AUTHORITY


class DevSignIn(BaseModel):
    """Callers hold tokens signed with the key that `make env` created on this machine."""

    model_config = ConfigDict(frozen=True)

    issuer: str
    audience: str
    public_key_pem: str


type SignIn = EntraSignIn | DevSignIn


class McpSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    environment: Environment
    log_level: LogLevel = "INFO"
    limits_file: FilePath
    runbooks_dir: DirectoryPath

    auth_mode: AuthMode
    mcp_public_url: AnyHttpUrl
    mcp_api_audience: str = Field(min_length=1)
    tenant_id: UUID | None = None
    mcp_api_client_id: UUID | None = None
    dev_issuer: str | None = Field(default=None, pattern=r"^https?://[^\s/]+/?$")
    dev_issuer_public_key: str | None = None
    approval_signing_key: SecretStr | None = None

    @field_validator(
        "tenant_id",
        "mcp_api_client_id",
        "dev_issuer",
        "dev_issuer_public_key",
        "approval_signing_key",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def _sign_in_is_complete(self) -> Self:
        self.sign_in()
        self.approval_key_pem()
        return self

    @property
    def json_logs(self) -> bool:
        return self.environment != LOCAL

    def approval_key_pem(self) -> str | None:
        """The key approvals are signed with on a developer's machine, or None.

        Azure keeps the key in Key Vault, where it cannot be read, so a key handed to the
        process is accepted only when ENVIRONMENT is local.
        """
        if self.approval_signing_key is None:
            return None
        if self.environment != LOCAL:
            raise ValueError("APPROVAL_SIGNING_KEY is allowed only when ENVIRONMENT is local")
        try:
            pem = base64.b64decode(self.approval_signing_key.get_secret_value(), validate=True)
            key = load_pem_private_key(pem, password=None)
            if not isinstance(key, EllipticCurvePrivateKey) or key.curve.name != _APPROVAL_CURVE:
                raise TypeError
        except (ValueError, TypeError) as error:
            raise ValueError(
                "APPROVAL_SIGNING_KEY must be a base64-encoded PEM P-256 private key"
            ) from error
        return pem.decode("ascii")

    def sign_in(self) -> SignIn:
        """How callers prove who they are. A mode made for testing never runs in Azure."""
        match self.auth_mode:
            case "entra":
                if self.tenant_id is None or self.mcp_api_client_id is None:
                    raise ValueError("AUTH_MODE=entra needs TENANT_ID and MCP_API_CLIENT_ID")
                return EntraSignIn(
                    tenant_id=self.tenant_id,
                    client_id=self.mcp_api_client_id,
                    audience=self.mcp_api_audience,
                )
            case "dev":
                if self.environment != LOCAL:
                    raise ValueError("AUTH_MODE=dev is allowed only when ENVIRONMENT is local")
                if self.dev_issuer is None or self.dev_issuer_public_key is None:
                    raise ValueError("AUTH_MODE=dev needs DEV_ISSUER and DEV_ISSUER_PUBLIC_KEY")
                return DevSignIn(
                    issuer=self.dev_issuer,
                    audience=self.mcp_api_audience,
                    public_key_pem=_pem(self.dev_issuer_public_key),
                )
            case "eval":
                raise ValueError("AUTH_MODE=eval is not available in this version")


def _pem(encoded: str) -> str:
    """The key is kept base64-encoded so that it fits on one line of an environment file."""
    try:
        pem = base64.b64decode(encoded, validate=True)
        if not isinstance(load_pem_public_key(pem), RSAPublicKey):
            raise TypeError
    except (ValueError, TypeError) as error:
        raise ValueError(
            "DEV_ISSUER_PUBLIC_KEY must be a base64-encoded PEM RSA public key"
        ) from error
    return pem.decode("ascii")
