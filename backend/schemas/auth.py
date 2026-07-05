"""AdaptIQ backend module for auth behavior."""

from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class AuthUserOut(BaseModel):
    id: str
    email: str
    username: str
    points: int
    level: str
    is_active: bool
    is_admin: bool


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUserOut


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=8)
    new_password: str = Field(min_length=8, max_length=128)


class ProfileEmailChangeRequest(BaseModel):
    new_email: EmailStr


class ProfileEmailChangeConfirmRequest(BaseModel):
    new_email: EmailStr
    code: str = Field(min_length=4, max_length=8)


class MessageOut(BaseModel):
    message: str


class MeOut(BaseModel):
    user: AuthUserOut
    issued_at: datetime


class BootstrapAdminRequest(BaseModel):
    email: EmailStr
    bootstrap_key: str = Field(min_length=8, max_length=256)
