"""Starts the web server. Logging stays as the application configures it."""

import uvicorn

ALL_INTERFACES = "0.0.0.0"  # noqa: S104 -- inside a container; what may reach it is set by the platform's ingress
PORT = 8000


def main() -> None:
    """Nothing here writes a request target to the log, since a target can carry a query string.

    That is why the access log is off, and why WebSocket upgrades, which this server has no
    use for and which are logged with their target, are not handled at all.
    """
    uvicorn.run(
        "opscp_mcp.main:create_app",
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
