import base64
import logging
from collections.abc import Iterator
from uuid import UUID

import pytest
import structlog
from azure.identity.aio import ManagedIdentityCredential
from pydantic import ValidationError

from opscp_common import blob
from opscp_common.blob import policies_container
from opscp_common.logging import configure_logging
from opscp_common.settings import StorageSettings

ACCOUNT_URL = "https://stopscpdemo.blob.core.windows.net"
CLIENT_ID = UUID("7f1c2d3e-0000-4000-8000-0000000000aa")
_KEY = base64.b64encode(b"not a real key, only a test value").decode()
EMULATOR = f"DefaultEndpointsProtocol=http;AccountName=opscplocal;AccountKey={_KEY};"
CONNECTION = f"{EMULATOR}BlobEndpoint=http://127.0.0.1:10000/opscplocal;"
STORAGE_VARIABLES = (
    "ENVIRONMENT",
    "POLICIES_CONTAINER",
    "BLOB_ACCOUNT_URL",
    "AZURE_CLIENT_ID",
    "BLOB_CONNECTION_STRING",
)


@pytest.fixture(autouse=True)
def no_storage_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in STORAGE_VARIABLES:
        monkeypatch.delenv(variable, raising=False)


def _settings(**values: object) -> StorageSettings:
    return StorageSettings.model_validate({"policies_container": "policies"} | values)


def _in_azure(**values: object) -> StorageSettings:
    defaults = {
        "environment": "demo",
        "blob_account_url": ACCOUNT_URL,
        "azure_client_id": CLIENT_ID,
    }
    return _settings(**(defaults | values))


def test_azure_is_reached_by_account_url_with_a_named_identity() -> None:
    settings = _in_azure()

    assert (settings.blob_account_url, settings.azure_client_id) == (ACCOUNT_URL, CLIENT_ID)
    assert settings.blob_connection_string is None


def test_the_account_url_needs_the_identity_that_reads_it() -> None:
    with pytest.raises(ValidationError, match="AZURE_CLIENT_ID"):
        _in_azure(azure_client_id=None)


@pytest.mark.parametrize(
    "url",
    [
        "http://stopscpdemo.blob.core.windows.net",
        "https://example.com",
        "https://stopscpdemo.blob.core.windows.net.example.com",
        f"{ACCOUNT_URL}/policies",
        f"{ACCOUNT_URL}?sv=2026&sig=abc",
        f"{ACCOUNT_URL}#fragment",
        "https://user@stopscpdemo.blob.core.windows.net",
        "https://:secret@stopscpdemo.blob.core.windows.net",
        " https://stopscpdemo.blob.core.windows.net",
        "https://stopscpdemo.blob.core.windows.net\n",
        "https://.blob.core.windows.net",
        "https://a.b.blob.core.windows.net",
        "https://evil.example\\.blob.core.windows.net",
        "https://stopscpdemo.blob.core.windows.net:8443",
        "HTTPS://STOPSCPDEMO.BLOB.CORE.WINDOWS.NET",
        "not a url",
    ],
)
def test_only_a_plain_https_blob_account_url_is_accepted(url: str) -> None:
    with pytest.raises(ValidationError, match="BLOB_ACCOUNT_URL must be"):
        _in_azure(blob_account_url=url)


def test_the_local_emulator_is_reached_by_connection_string() -> None:
    settings = _settings(environment="local", blob_connection_string=CONNECTION)

    assert settings.blob_connection_string is not None
    assert settings.blob_connection_string.get_secret_value() == CONNECTION


@pytest.mark.parametrize("environment", ["demo", "prod"])
def test_a_connection_string_is_refused_outside_local(environment: str) -> None:
    with pytest.raises(ValidationError, match="only when ENVIRONMENT is local"):
        _settings(environment=environment, blob_connection_string=CONNECTION)


@pytest.mark.parametrize(
    "connection",
    [
        pytest.param(f"{EMULATOR}EndpointSuffix=core.windows.net", id="a real account"),
        pytest.param(
            f"{EMULATOR}BlobEndpoint=https://stopscpdemo.blob.core.windows.net;", id="remote"
        ),
        pytest.param(f"{CONNECTION}BlobEndpoint=https://evil.example/a;", id="given twice"),
        pytest.param(f"{CONNECTION}blobendpoint=https://evil.example;", id="twice in other case"),
        pytest.param(
            f"{CONNECTION}BlobSecondaryEndpoint=https://evil.example;", id="remote secondary"
        ),
        pytest.param(f"{EMULATOR}BlobEndpoint=http://127.0.0.1@evil.example/a;", id="userinfo"),
        pytest.param(f"{EMULATOR}BlobEndpoint=evil.example;", id="no scheme"),
    ],
)
def test_a_connection_string_must_point_at_this_machine(connection: str) -> None:
    with pytest.raises(ValidationError, match="local emulator"):
        _settings(environment="local", blob_connection_string=connection)


@pytest.mark.parametrize(
    "values",
    [
        pytest.param({}, id="neither"),
        pytest.param({"blob_account_url": "", "blob_connection_string": ""}, id="both blank"),
        pytest.param(
            {
                "blob_account_url": ACCOUNT_URL,
                "azure_client_id": CLIENT_ID,
                "blob_connection_string": CONNECTION,
            },
            id="both",
        ),
    ],
)
def test_exactly_one_way_to_reach_storage_is_required(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        _settings(environment="local", **values)


def test_a_blank_variable_counts_as_unset() -> None:
    settings = _in_azure(blob_connection_string="")

    assert settings.blob_connection_string is None


def test_an_unknown_environment_is_refused() -> None:
    with pytest.raises(ValidationError, match="environment"):
        _in_azure(environment="staging")


def test_the_account_key_is_not_shown_when_settings_are_printed() -> None:
    settings = _settings(environment="local", blob_connection_string=CONNECTION)

    assert _KEY not in repr(settings)
    assert _KEY not in str(settings.model_dump())


@pytest.mark.parametrize("environment", ["local", "prod"])
def test_a_refused_connection_string_is_not_quoted_in_the_error(environment: str) -> None:
    key_last = f"DefaultEndpointsProtocol=https;AccountName=real;AccountKey={_KEY}"

    with pytest.raises(ValidationError) as refusal:
        _settings(environment=environment, blob_connection_string=key_last)

    assert _KEY[-12:] not in str(refusal.value)
    assert _KEY[:12] not in str(refusal.value)


def test_values_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("POLICIES_CONTAINER", "policies")
    monkeypatch.setenv("BLOB_ACCOUNT_URL", ACCOUNT_URL)
    monkeypatch.setenv("AZURE_CLIENT_ID", str(CLIENT_ID))
    monkeypatch.setenv("BLOB_CONNECTION_STRING", "")

    settings = StorageSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment

    assert (settings.environment, settings.policies_container) == ("prod", "policies")
    assert settings.azure_client_id == CLIENT_ID


async def test_the_container_is_opened_from_the_connection_string_locally() -> None:
    settings = _settings(environment="local", blob_connection_string=CONNECTION)

    async with policies_container(settings) as container:
        assert container.container_name == "policies"
        assert container.url == "http://127.0.0.1:10000/opscplocal/policies"


async def test_the_container_is_opened_by_url_in_azure() -> None:
    async with policies_container(_in_azure(environment="prod")) as container:
        assert container.container_name == "policies"
        assert container.url == f"{ACCOUNT_URL}/policies"


async def test_the_named_managed_identity_is_the_one_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    used: list[str | None] = []

    class _Credential(ManagedIdentityCredential):
        def __init__(self, *, client_id: str | None = None) -> None:
            used.append(client_id)
            super().__init__(client_id=client_id)

    monkeypatch.setattr(blob, "ManagedIdentityCredential", _Credential)

    async with policies_container(_in_azure(environment="prod")) as container:
        assert isinstance(container.credential, _Credential)

    assert used == [str(CLIENT_ID)]


def test_a_trailing_slash_on_the_account_url_is_accepted() -> None:
    assert _in_azure(blob_account_url=f"{ACCOUNT_URL}/").blob_account_url == f"{ACCOUNT_URL}/"


@pytest.fixture
def reset_logging() -> Iterator[None]:
    yield
    structlog.reset_defaults()
    logging.basicConfig(handlers=[logging.NullHandler()], level=logging.WARNING, force=True)
    logging.getLogger("azure").setLevel(logging.NOTSET)


@pytest.mark.usefixtures("reset_logging")
def test_the_azure_sdk_does_not_log_every_request() -> None:
    configure_logging(service="mcp", level="INFO", json=True)

    assert logging.getLogger("azure.core.pipeline").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("another.library").getEffectiveLevel() == logging.INFO


@pytest.mark.usefixtures("reset_logging")
def test_a_stricter_level_also_applies_to_the_azure_sdk() -> None:
    configure_logging(service="mcp", level="ERROR", json=True)

    assert logging.getLogger("azure.core.pipeline").getEffectiveLevel() == logging.ERROR
