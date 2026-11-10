class ApiError(Exception):
    """Expected business error returned without exposing internal details."""
    def __init__(self, status, code, message, extra=None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.extra = extra or {}
