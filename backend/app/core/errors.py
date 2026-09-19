class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, field: str | None = None):
        self.code = code
        self.message = message
        self.status = status
        self.field = field
        super().__init__(message)


def error(code: str, message: str, status: int = 422, field: str | None = None) -> None:
    raise DomainError(code, message, status, field)
