"""Prints a .env for local development, with a new database password and storage key."""

import base64
import secrets

STORAGE_ACCOUNT = "opscplocal"
BLOB_ENDPOINT = f"http://127.0.0.1:10000/{STORAGE_ACCOUNT}"


def main() -> None:
    password = secrets.token_hex(24)
    storage_key = base64.b64encode(secrets.token_bytes(48)).decode()
    connection = (
        f"DefaultEndpointsProtocol=http;AccountName={STORAGE_ACCOUNT};"
        f"AccountKey={storage_key};BlobEndpoint={BLOB_ENDPOINT};"
    )
    lines = (
        "ENVIRONMENT=local",
        f"POSTGRES_PASSWORD={password}",
        "DATABASE_HOST=127.0.0.1",
        "DATABASE_NAME=meridian",
        "DATABASE_USER=migrator",
        f"DATABASE_PASSWORD={password}",
        f'AZURITE_ACCOUNTS="{STORAGE_ACCOUNT}:{storage_key}"',
        "POLICIES_CONTAINER=policies",
        f'BLOB_CONNECTION_STRING="{connection}"',
    )
    print("\n".join(lines))  # noqa: T201 -- the output is the file


if __name__ == "__main__":
    main()
