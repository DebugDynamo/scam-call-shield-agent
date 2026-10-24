"""Room (multi-party meeting) adapter: INTERFACE ONLY.

STATUS: NOT IMPLEMENTED and UNTESTED. There is no Google Meet integration and none is claimed.
Pipecat can join Daily rooms (pipecat.transports.daily) and LiveKit rooms; wiring one in needs, at minimum:
  * a Daily account + DAILY_API_KEY (room/token creation via the Daily REST API) - or LiveKit URL/key/secret,
  * a `RoomAdapter` implementation that creates a room + bot token and returns the join URL for humans,
  * a pipeline using DailyTransport (see pipecat.runner.daily) built with `build_voice_worker`,
  * participant/ownership/consent rules (who may invite the bot, recording disclosure).
`POST /api/v1/sessions` with mode=room returns 501 NOT_IMPLEMENTED until an adapter exists.
"""

from __future__ import annotations

from typing import Protocol

from ..errors import AppError


class RoomAdapter(Protocol):
    name: str

    async def create_room(self, session_id: str) -> dict[str, str]:
        """Return {'joinUrl': ..., 'botToken': ...}."""
        ...

    async def start_bot(self, session_id: str, room: dict[str, str]) -> None: ...
    async def close_room(self, session_id: str) -> None: ...


class NotImplementedRoomAdapter:
    name = "none"

    async def create_room(self, session_id: str) -> dict[str, str]:
        raise AppError("NOT_IMPLEMENTED", "Room adapter is not implemented")

    async def start_bot(self, session_id: str, room: dict[str, str]) -> None:
        raise AppError("NOT_IMPLEMENTED", "Room adapter is not implemented")

    async def close_room(self, session_id: str) -> None:
        return None
