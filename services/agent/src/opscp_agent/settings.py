"""Configuration of the agent service, read once at start-up."""

import base64
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
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

SERVICE_NAME = "agent"
ENTRA_AUTHORITY = "login.microsoftonline.com"
PROD = "prod"
_CANARY_CHARS_MIN = 16
_SECRET_CHARS_MIN = 32

type AuthMode = Literal["entra", "dev"]
type LlmProvider = Literal["azure", "scripted"]
type OboCredential = Literal["federated", "certificate", "secret"]


class EntraSignIn(BaseModel):
    """Callers hold tokens that Microsoft Entra ID issued for this service."""

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    client_id: UUID
    audience: str
    authority: str = ENTRA_AUTHORITY


class DevSignIn(BaseModel):
    """Callers hold tokens signed by the issuer that runs beside the services on a laptop."""

    model_config = ConfigDict(frozen=True)

    issuer: str
    audience: str
    public_key_pem: str


type SignIn = EntraSignIn | DevSignIn


class FederatedExchange(BaseModel):
    """The service proves itself to Entra with its managed identity. No secret exists."""

    model_config = ConfigDict(frozen=True)

    token_url: str
    client_id: str
    managed_identity_client_id: UUID
    scope: str


class SecretExchange(BaseModel):
    """The service proves itself to the local issuer with a secret `make env` generated."""

    model_config = ConfigDict(frozen=True)

    token_url: str
    client_id: str
    secret: SecretStr
    scope: str


type TokenExchangeSettings = FederatedExchange | SecretExchange


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    environment: Environment
    log_level: LogLevel = "INFO"
    limits_file: FilePath
    pricing_file: FilePath
    usd_inr_rate: Decimal = Field(gt=0)

    auth_mode: AuthMode
    agent_api_audience: str = Field(min_length=1)
    agent_api_client_id: str | None = None
    tenant_id: UUID | None = None
    azure_client_id: UUID | None = None
    dev_issuer: str | None = Field(default=None, pattern=r"^https?://[^\s/]+/?$")
    dev_issuer_public_key: str | None = None

    mcp_url: AnyHttpUrl
    mcp_scope: str = Field(min_length=1)
    obo_credential: OboCredential
    token_url: AnyHttpUrl | None = None
    agent_client_secret: SecretStr | None = Field(default=None, min_length=_SECRET_CHARS_MIN)

    llm_provider: LlmProvider
    canary: SecretStr = Field(min_length=_CANARY_CHARS_MIN)
    chaos_enabled: bool = False

    @field_validator(
        "agent_api_client_id",
        "tenant_id",
        "azure_client_id",
        "dev_issuer",
        "dev_issuer_public_key",
        "token_url",
        "agent_client_secret",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def _is_complete(self) -> Self:
        if self.chaos_enabled and self.environment == PROD:
            raise ValueError("CHAOS_ENABLED is never allowed when ENVIRONMENT is prod")
        if self.environment != LOCAL and self.mcp_url.scheme != "https":
            raise ValueError("MCP_URL must be https outside local development")
        if self.llm_provider == "scripted" and self.environment == PROD:
            raise ValueError("LLM_PROVIDER=scripted is never allowed when ENVIRONMENT is prod")
        if self.llm_provider == "azure":
            raise ValueError("LLM_PROVIDER=azure is not available in this version")
        self.sign_in()
        self.token_exchange()
        return self

    @property
    def json_logs(self) -> bool:
        return self.environment != LOCAL

    @property
    def demo_tools(self) -> bool:
        """What shows the Technical details switch. It follows chaos and nothing else."""
        return self.chaos_enabled

    def sign_in(self) -> SignIn:
        """How callers prove who they are. The local mode never runs in Azure."""
        match self.auth_mode:
            case "entra":
                client_id = _uuid(self.agent_api_client_id)
                if self.tenant_id is None or client_id is None:
                    raise ValueError("AUTH_MODE=entra needs TENANT_ID and AGENT_API_CLIENT_ID")
                return EntraSignIn(
                    tenant_id=self.tenant_id, client_id=client_id, audience=self.agent_api_audience
                )
            case "dev":
                if self.environment != LOCAL:
                    raise ValueError("AUTH_MODE=dev is allowed only when ENVIRONMENT is local")
                if self.dev_issuer is None or self.dev_issuer_public_key is None:
                    raise ValueError("AUTH_MODE=dev needs DEV_ISSUER and DEV_ISSUER_PUBLIC_KEY")
                return DevSignIn(
                    issuer=self.dev_issuer,
                    audience=self.agent_api_audience,
                    public_key_pem=_pem(self.dev_issuer_public_key),
                )

    def token_exchange(self) -> TokenExchangeSettings:
        """How a person's token is exchanged for one meant for the MCP server."""
        match self.obo_credential:
            case "federated":
                tenant, client, identity = (
                    self.tenant_id,
                    self.agent_api_client_id,
                    self.azure_client_id,
                )
                if tenant is None or client is None or identity is None:
                    raise ValueError(
                        "OBO_CREDENTIAL=federated needs TENANT_ID, AGENT_API_CLIENT_ID "
                        "and AZURE_CLIENT_ID"
                    )
                return FederatedExchange(
                    token_url=f"https://{ENTRA_AUTHORITY}/{tenant}/oauth2/v2.0/token",
                    client_id=client,
                    managed_identity_client_id=identity,
                    scope=self.mcp_scope,
                )
            case "secret":
                if self.environment != LOCAL:
                    raise ValueError(
                        "OBO_CREDENTIAL=secret is allowed only when ENVIRONMENT is local"
                    )
                url, client, secret = (
                    self.token_url,
                    self.agent_api_client_id,
                    self.agent_client_secret,
                )
                if url is None or client is None or secret is None:
                    raise ValueError(
                        "OBO_CREDENTIAL=secret needs TOKEN_URL, AGENT_API_CLIENT_ID "
                        "and AGENT_CLIENT_SECRET"
                    )
                return SecretExchange(
                    token_url=str(url), client_id=client, secret=secret, scope=self.mcp_scope
                )
            case "certificate":
                raise ValueError("OBO_CREDENTIAL=certificate is not available in this version")


def _uuid(value: str | None) -> UUID | None:
    try:
        return None if value is None else UUID(value)
    except ValueError:
        return None


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
