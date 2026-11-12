"""에러 코드. 응답 형식: {"error": "CODE", "message": "..."}"""


class MiniRedisError(Exception):
    status = 500
    code = "INTERNAL_ERROR"

    def __init__(self, message: str = ""):
        super().__init__(message)
        self.message = message


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
