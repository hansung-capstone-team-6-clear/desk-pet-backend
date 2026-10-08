"""디바이스 토큰 확인 (contracts § 0). Pi는 모든 요청에 X-Device-Token 헤더를 붙인다."""

import hmac
from typing import Annotated

from fastapi import Header, HTTPException

from desk_pet_backend.config import settings


def is_valid_token(token: str) -> bool:
    return bool(settings.device_token) and hmac.compare_digest(token, settings.device_token)


def require_device_token(x_device_token: Annotated[str, Header()] = "") -> None:
    """HTTP 라우트용: 토큰이 틀리면 401."""
    if not is_valid_token(x_device_token):
        raise HTTPException(status_code=401, detail="invalid device token")
