from uuid import UUID

import pytest
from pydantic import ValidationError

from opscp_common.errors import (
    ApprovalInvalidError,
    BackendTimeoutError,
    BackendUnavailableError,
    ErrorCode,
    IdempotencyConflictError,
    InternalError,
    NotFoundError,
    OpsError,
    PermissionDeniedError,
    PolicyViolationError,
    RateLimitedError,
    ValidationFailedError,
)

RETRYABLE = {ErrorCode.RATE_LIMITED, ErrorCode.BACKEND_UNAVAILABLE, ErrorCode.TIMEOUT}
ERRORS: list[OpsError] = [
    PermissionDeniedError("no"),
    ValidationFailedError("no"),
    NotFoundError("no"),
    PolicyViolationError("no"),
    ApprovalInvalidError("no"),
    IdempotencyConflictError("no"),
    RateLimitedError("no", retry_after_seconds=7),
    BackendUnavailableError("no"),
    BackendTimeoutError("no"),
    InternalError(),
]


def test_every_error_code_has_exactly_one_error_type() -> None:
    assert sorted(error.code for error in ERRORS) == sorted(ErrorCode)


@pytest.mark.parametrize("error", ERRORS, ids=lambda error: error.code.value)
def test_only_rate_limits_outages_and_timeouts_are_retryable(error: OpsError) -> None:
    assert error.body().retryable is (error.code in RETRYABLE)


def test_the_body_carries_the_code_message_and_details() -> None:
    error = PolicyViolationError(
        "Goodwill refund exceeds the 15% cap.", cap_minor=93000, requested_minor=620000
    )

    assert error.body().model_dump(mode="json") == {
        "code": "POLICY_VIOLATION",
        "message": "Goodwill refund exceeds the 15% cap.",
        "retryable": False,
        "details": {"cap_minor": 93000, "requested_minor": 620000},
    }


def test_a_rate_limit_says_when_to_try_again() -> None:
    assert RateLimitedError("Too many calls.", retry_after_seconds=7).body().details == {
        "retry_after_seconds": 7
    }


def test_an_internal_error_says_nothing_about_its_cause() -> None:
    body = InternalError().body()

    assert body.message == "Something went wrong on the server."
    assert body.details == {}


@pytest.mark.parametrize("value", [1.5, UUID(int=1), object()])
def test_a_detail_that_is_not_plain_json_is_refused_where_it_is_raised(value: object) -> None:
    with pytest.raises(ValidationError):
        NotFoundError("No such order.", order=value)  # pyright: ignore[reportArgumentType] -- the wrong type is the point
