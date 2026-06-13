from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List

class UserCreate(BaseModel):
    user_id: int
    referral_code: Optional[str] = None

class UserResponse(BaseModel):
    user_id: int
    tier: str
    model: str
    is_banned: bool
    created_at: datetime

class MessageRequest(BaseModel):
    user_id: int
    text: str
    model: Optional[str] = None

class MessageResponse(BaseModel):
    response: str
    model_used: str
    processing_time: float

class PaymentRequest(BaseModel):
    user_id: int
    tier: str

class PaymentResponse(BaseModel):
    invoice_url: str
    amount: float

class BroadcastRequest(BaseModel):
    admin_id: int
    message: str
