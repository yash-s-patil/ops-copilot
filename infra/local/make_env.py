"""Prints a .env for local development: a new database password, storage key and signing key.

The signing key stands in for the sign-in service. Tokens signed with it are accepted only
by a server started with AUTH_MODE=dev, which a server refuses outside ENVIRONMENT=local.
"""

import base64
import secrets

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

STORAGE_ACCOUNT = "opscplocal"
ON_THIS_MACHINE = "127.0.0.1"
INSIDE_COMPOSE = "azurite"
DEV_ISSUER = "http://dev-issuer.localhost/"
MCP_AUDIENCE = "api://opscp-mcp"
MCP_PUBLIC_URL = "http://127.0.0.1:8000"
KEY_BITS = 2048
PUBLIC_EXPONENT = 65537


def _connection(host: str, storage_key: str) -> str:
    return (
        f"DefaultEndpointsProtocol=http;AccountName={STORAGE_ACCOUNT};"
        f"AccountKey={storage_key};BlobEndpoint=http://{host}:10000/{STORAGE_ACCOUNT};"
    )


def _one_line(pem: bytes) -> str:
    return base64.b64encode(pem).decode()


def _signing_key() -> tuple[str, str]:
    """A new key pair as (private, public), each a base64-encoded PEM."""
    key = rsa.generate_private_key(public_exponent=PUBLIC_EXPONENT, key_size=KEY_BITS)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return _one_line(private), _one_line(public)


def _approval_key() -> str:
    """The key approvals are signed with locally, where there is no Key Vault to hold it."""
    key = ec.generate_private_key(ec.SECP256R1())
    return _one_line(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


def main() -> None:
    password = secrets.token_hex(24)
    storage_key = base64.b64encode(secrets.token_bytes(48)).decode()
    private_key, public_key = _signing_key()
    lines = (
        "ENVIRONMENT=local",
        f"POSTGRES_PASSWORD={password}",
        "DATABASE_HOST=127.0.0.1",
        "DATABASE_NAME=meridian",
        "DATABASE_USER=migrator",
        f"DATABASE_PASSWORD={password}",
        f'AZURITE_ACCOUNTS="{STORAGE_ACCOUNT}:{storage_key}"',
        "POLICIES_CONTAINER=policies",
        f'BLOB_CONNECTION_STRING="{_connection(ON_THIS_MACHINE, storage_key)}"',
        f'BLOB_CONNECTION_STRING_IN_COMPOSE="{_connection(INSIDE_COMPOSE, storage_key)}"',
        "AUTH_MODE=dev",
        f"MCP_PUBLIC_URL={MCP_PUBLIC_URL}",
        f"MCP_API_AUDIENCE={MCP_AUDIENCE}",
        f"DEV_ISSUER={DEV_ISSUER}",
        f"DEV_ISSUER_PUBLIC_KEY={public_key}",
        f"DEV_ISSUER_PRIVATE_KEY={private_key}",
        f"APPROVAL_SIGNING_KEY={_approval_key()}",
    )
    print("\n".join(lines))  # noqa: T201 -- the output is the file


if __name__ == "__main__":
    main()
