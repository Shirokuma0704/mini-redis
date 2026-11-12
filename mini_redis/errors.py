"""에러 코드. 응답 형식: {"error": "CODE", "message": "..."}"""


class MiniRedisError(Exception):
    status = 500
    code = "INTERNAL_ERROR"

    def __init__(self, message: str = ""):
        super().__init__(message)
        self.message = message
