"""Prints a .env for local development, with a new database password and storage key."""

import base64
import secrets

STORAGE_ACCOUNT = "opscplocal"
ON_THIS_MACHINE = "127.0.0.1"
INSIDE_COMPOSE = "azurite"


def _connection(host: str, storage_key: str) -> str:
    return (
        f"DefaultEndpointsProtocol=http;AccountName={STORAGE_ACCOUNT};"
        f"AccountKey={storage_key};BlobEndpoint=http://{host}:10000/{STORAGE_ACCOUNT};"
    )


def main() -> None:
    password = secrets.token_hex(24)
    storage_key = base64.b64encode(secrets.token_bytes(48)).decode()
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
    )
    print("\n".join(lines))  # noqa: T201 -- the output is the file


if __name__ == "__main__":
    main()
