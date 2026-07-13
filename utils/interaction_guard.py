import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger("bot.guard")


class InteractionGuard:
    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._user_cooldowns: dict[tuple[int, str], float] = {}

    def _get_lock(self, key: int) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    @asynccontextmanager
    async def lock(self, key: int) -> AsyncIterator[bool]:
        lock = self._get_lock(key)
        if lock.locked():
            yield False
            return
        async with lock:
            yield True

    def check_cooldown(self, user_id: int, action: str, cooldown: float) -> float | None:
        now = time.monotonic()
        key = (user_id, action)
        last = self._user_cooldowns.get(key, 0.0)
        remaining = cooldown - (now - last)
        if remaining > 0:
            return remaining
        self._user_cooldowns[key] = now
        return None


interaction_guard = InteractionGuard()
