"""Starts the simulator. Logging stays as the application configures it."""

import uvicorn

ALL_INTERFACES = "0.0.0.0"  # noqa: S104 -- inside a container; what may reach it is set by the platform's ingress
PORT = 8080


def main() -> None:
    uvicorn.run(
        "opscp_payments.sim.app:create_app",
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
