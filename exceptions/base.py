class AppException(Exception):
    """Base exception for application"""
    def __init__(self, message: str, code: int = 500):
        self.message = message
        self.code = code
        super().__init__(message)

class UserNotFoundError(AppException):
    def __init__(self, user_id: int):
        super().__init__(f"User {user_id} not found", 404)

class PaymentError(AppException):
    def __init__(self, message: str):
        super().__init__(message, 400)

class RateLimitError(AppException):
    def __init__(self):
        super().__init__("Too many requests", 429)
