"""WebSocket event bus: the server pushes {type, data} messages to every connected browser.

Types: alert | situation_changed | run_started | run_finished | action_updated.

Request handlers and background runs execute in worker threads, but WebSocket sends must happen on the
server's event loop. `publish()` is therefore thread-safe: it schedules the broadcast on the loop
captured at startup. With no loop or no clients (e.g. in unit tests), publishing is a no-op.
"""

import asyncio
import logging
from typing import Any

from fastapi import WebSocket
from fastapi.encoders import jsonable_encoder

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.sent: list[dict[str, Any]] = []  # recent messages, kept for tests and debugging

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.loop = self.loop or asyncio.get_running_loop()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def _broadcast(self, message: dict[str, Any]) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001 - a dead socket must not block the others
                self.disconnect(ws)

    def publish(self, type_: str, data: Any = None) -> None:
        message = {"type": type_, "data": jsonable_encoder(data)}
        self.sent = [*self.sent[-199:], message]
        if self.loop is None or not self.clients or self.loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self.loop.create_task(self._broadcast(message))
        else:
            asyncio.run_coroutine_threadsafe(self._broadcast(message), self.loop)
