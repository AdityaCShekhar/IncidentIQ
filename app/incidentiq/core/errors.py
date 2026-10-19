from uuid import UUID


class APIError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, *, incident_id: UUID | None = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.incident_id = incident_id
