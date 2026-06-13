import uuid
import secrets
import random
import string

class IDGenerator:
    @staticmethod
    def uuid4() -> str:
        return str(uuid.uuid4())
    
    @staticmethod
    def secure_token(length: int = 32) -> str:
        return secrets.token_urlsafe(length)
    
    @staticmethod
    def numeric_code(length: int = 6) -> str:
        return ''.join(random.choices(string.digits, k=length))
    
    @staticmethod
    def alphanumeric(length: int = 8) -> str:
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))
