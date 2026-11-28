"""The token endpoint over HTTP."""

from urllib.parse import parse_qs

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from opscp_common.logging import configure_logging
from opscp_dev_issuer.exchange import Client, Exchange, RefusedError, SignInClient
from opscp_dev_issuer.settings import SERVICE_NAME, IssuerSettings

TOKEN_PATH = "/token"  # noqa: S105 -- a path
_BODY_BYTES_MAX = 16_384
_TOO_LARGE = 413
_FORM = "application/x-www-form-urlencoded"
_NO_STORE = {"Cache-Control": "no-store"}
_LOG = structlog.get_logger(__name__)


def build_app(exchange: Exchange) -> FastAPI:
    app = FastAPI(title="Local issuer", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(TOKEN_PATH)
    async def token(request: Request) -> JSONResponse:
        declared = request.headers.get("content-length", "")
        if not declared.isdecimal() or int(declared) > _BODY_BYTES_MAX:
            return _refused(RefusedError("invalid_request", "The body is too large.", _TOO_LARGE))
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != _FORM:
            return _refused(RefusedError("invalid_request", "The body must be a form."))
        fields = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        form = {name: values[0] for name, values in fields.items() if len(values) == 1}
        try:
            issued = exchange.issue(form)
        except RefusedError as refused:
            return _refused(refused)
        _LOG.info("token_issued", scope=issued.scope)
        return JSONResponse(
            {
                "token_type": "Bearer",
                "scope": issued.scope,
                "expires_in": issued.expires_in,
                "access_token": issued.access_token,
            },
            headers=_NO_STORE,
        )

    return app


def _refused(refused: RefusedError) -> JSONResponse:
    _LOG.info("token_refused", error=refused.error)
    return JSONResponse(
        {"error": refused.error, "error_description": refused.description},
        status_code=refused.status,
        headers=_NO_STORE,
    )


def create_app() -> FastAPI:
    settings = IssuerSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment
    configure_logging(service=SERVICE_NAME, level=settings.log_level, json=False)
    client = Client(
        client_id=settings.agent_api_client_id,
        secret=settings.agent_client_secret.get_secret_value(),
        audience=settings.agent_api_audience,
        downstream_audience=settings.mcp_api_audience,
    )
    sign_in = SignInClient(
        client_id=settings.web_client_id,
        secret=settings.web_client_secret.get_secret_value(),
        audience=settings.agent_api_audience,
    )
    return build_app(
        Exchange(settings.dev_issuer, settings.private_key_pem(), client, sign_in=sign_in)
    )
