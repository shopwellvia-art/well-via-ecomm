"""Request/response shapes for the COD OTP endpoints."""
from __future__ import annotations

from pydantic import Field
from app.schemas.base import AppSchema


class CodOtpSendRequest(AppSchema):
    phone: str = Field(min_length=8, max_length=20)


class CodOtpSendResponse(AppSchema):
    phone_masked: str
    expires_in_seconds: int


class CodOtpVerifyRequest(AppSchema):
    phone: str = Field(min_length=8, max_length=20)
    code: str = Field(min_length=4, max_length=8)


class CodOtpVerifyResponse(AppSchema):
    verified: bool = True
