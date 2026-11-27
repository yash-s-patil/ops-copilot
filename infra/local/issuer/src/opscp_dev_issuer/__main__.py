"""Starts the local issuer. Logging stays as the application configures it."""

import uvicorn

ALL_INTERFACES = "0.0.0.0"  # noqa: S104 -- inside a container on a developer's machine
PORT = 8090


def main() -> None:
    uvicorn.run(
        "opscp_dev_issuer.app:create_app",
        factory=True,
        host=ALL_INTERFACES,
        port=PORT,
        log_config=None,
        access_log=False,
        ws="none",
        server_header=False,
    )


if __name__ == "__main__":
    main()
