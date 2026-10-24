from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, Request

from ..errors import AppError


def container(request: Request):
    return request.app.state.c


Container = Annotated["object", Depends(container)]


async def require_api_key(request: Request, x_api_key: Annotated[str | None, Header()] = None) -> None:
    """Admin / service routes: enforced only when INTERNAL_API_KEY is set (startup warns otherwise)."""
    expected = request.app.state.c.settings.internal_api_key
    if not expected:
        return
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise AppError("UNAUTHORIZED", "Missing or invalid X-Api-Key")
