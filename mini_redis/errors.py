"""에러 코드. 응답 형식: {"error": "CODE", "message": "..."}"""


class MiniRedisError(Exception):
    status = 500
    code = "INTERNAL_ERROR"

    def __init__(self, message: str = "", **extra):
        super().__init__(message)
        self.message = message
        self.extra = extra  # 응답 객체에 덧붙일 필드 (lease, retry_after_ms)


class InvalidKey(MiniRedisError):
    status = 400
    code = "INVALID_KEY"


class ValueTooLarge(MiniRedisError):
    status = 413
    code = "VALUE_TOO_LARGE"


class CapacityExceeded(MiniRedisError):
    status = 507
    code = "CAPACITY_EXCEEDED"


class InvalidValue(MiniRedisError):
    status = 400
    code = "INVALID_VALUE"


class KeyNotFound(MiniRedisError):
    status = 404
    code = "KEY_NOT_FOUND"


class NotInteger(MiniRedisError):
    status = 409
    code = "NOT_INTEGER"


class EmptyPrefix(MiniRedisError):
    status = 400
    code = "EMPTY_PREFIX"


class ConfirmRequired(MiniRedisError):
    status = 400
    code = "CONFIRM_REQUIRED"


class InvalidTtl(MiniRedisError):
    status = 400
    code = "INVALID_TTL"


class InvalidBody(MiniRedisError):
    status = 400
    code = "INVALID_BODY"


class LeaseInvalid(MiniRedisError):
    status = 409
    code = "LEASE_INVALID"


class NotImplementedYet(MiniRedisError):
    """아직 구현되지 않은 명령 (네임스페이스는 #11)."""

    status = 501
    code = "NOT_IMPLEMENTED"
