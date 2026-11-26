"""Failures a caller is told about, and the one body they are reported in."""

from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, TypeAdapter

from opscp_common.hashing import JsonValue

_DETAILS = TypeAdapter(dict[str, JsonValue])


class ErrorCode(StrEnum):
    PERMISSION_DENIED = "PERMISSION_DENIED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    NOT_FOUND = "NOT_FOUND"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    INTERNAL = "INTERNAL"


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ErrorCode
    message: str
    retryable: bool
    details: dict[str, JsonValue]


class OpsError(Exception):
    """The message and details reach the caller, so they never name hosts, SQL or internals."""

    code: ClassVar[ErrorCode]
    retryable: ClassVar[bool] = False

    def __init__(self, message: str, **details: JsonValue) -> None:
        super().__init__(message)
        self.message = message
        self.details = _DETAILS.validate_python(details, strict=True)

    def body(self) -> ErrorBody:
        return ErrorBody(
            code=self.code, message=self.message, retryable=self.retryable, details=self.details
        )


class PermissionDeniedError(OpsError):
    code = ErrorCode.PERMISSION_DENIED


class ValidationFailedError(OpsError):
    code = ErrorCode.VALIDATION_FAILED


class NotFoundError(OpsError):
    code = ErrorCode.NOT_FOUND


class PolicyViolationError(OpsError):
    code = ErrorCode.POLICY_VIOLATION


class ApprovalInvalidError(OpsError):
    code = ErrorCode.APPROVAL_INVALID


class IdempotencyConflictError(OpsError):
    code = ErrorCode.IDEMPOTENCY_CONFLICT


class RateLimitedError(OpsError):
    code = ErrorCode.RATE_LIMITED
    retryable = True

    def __init__(self, message: str, *, retry_after_seconds: int) -> None:
        super().__init__(message, retry_after_seconds=retry_after_seconds)


class BackendUnavailableError(OpsError):
    code = ErrorCode.BACKEND_UNAVAILABLE
    retryable = True


class BackendTimeoutError(OpsError):
    code = ErrorCode.TIMEOUT
    retryable = True


class InternalError(OpsError):
    code = ErrorCode.INTERNAL

    def __init__(self) -> None:
        super().__init__("Something went wrong on the server.")
