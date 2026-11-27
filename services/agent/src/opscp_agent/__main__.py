"""Starts the web server. Logging stays as the application configures it."""

import uvicorn

ALL_INTERFACES = "0.0.0.0"  # noqa: S104 -- inside a container; what may reach it is set by the platform's ingress
PORT = 8001


def main() -> None:
    """The access log is off because a request target can carry a query string."""
    uvicorn.run(
        "opscp_agent.main:create_app",
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
