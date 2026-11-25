import pytest
from pydantic import ValidationError

from opscp_mcp.settings import DatabaseSettings

_PASSWORD = "local-only"  # noqa: S105


def _settings(**overrides: object) -> DatabaseSettings:
    values: dict[str, object] = {
        "host": "db.internal",
        "name": "meridian",
        "user": "mcp_app",
        "password": _PASSWORD,
    }
    return DatabaseSettings.model_validate(values | overrides)


def test_url_uses_the_async_driver_and_every_field() -> None:
    url = _settings(port=6432).url()

    assert url.drivername == "postgresql+asyncpg"
    assert (url.username, url.host, url.port, url.database) == (
        "mcp_app",
        "db.internal",
        6432,
        "meridian",
    )
    assert url.password == _PASSWORD


def test_port_defaults_to_the_postgres_port() -> None:
    assert _settings().port == 5432


def test_password_is_not_shown_when_settings_are_printed() -> None:
    assert _PASSWORD not in repr(_settings())


def test_values_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "DATABASE_HOST": "h",
        "DATABASE_NAME": "n",
        "DATABASE_USER": "u",
        "DATABASE_PASSWORD": _PASSWORD,
    }.items():
        monkeypatch.setenv(key, value)

    settings = DatabaseSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment

    assert (settings.host, settings.name, settings.user) == ("h", "n", "u")


@pytest.mark.parametrize("missing", ["host", "name", "user", "password"])
def test_missing_value_stops_start_up(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    for key in ("HOST", "NAME", "USER", "PASSWORD"):
        monkeypatch.delenv(f"DATABASE_{key}", raising=False)
    values = {"host": "h", "name": "n", "user": "u", "password": _PASSWORD}
    del values[missing]

    with pytest.raises(ValidationError, match=missing):
        DatabaseSettings.model_validate(values)


@pytest.mark.parametrize("port", [0, 65536])
def test_port_out_of_range_is_rejected(port: int) -> None:
    with pytest.raises(ValidationError):
        _settings(port=port)
