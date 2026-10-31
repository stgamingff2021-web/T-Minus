"""Tiny in-process pub/sub used by the SSE endpoints (agent trace + system notifications).

Events are also persisted (agent_events) so a reconnecting client can resume from
its Last-Event-ID (§8.4); this module only fans live events out to open streams.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

_subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
_loop: asyncio.AbstractEventLoop | None = None
_recent_system: list[dict] = []


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def subscribe(channel: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _subs[channel].add(q)
    return q


def unsubscribe(channel: str, q: asyncio.Queue) -> None:
    _subs[channel].discard(q)


def _deliver(channel: str, event: dict) -> None:
    for q in list(_subs.get(channel, ())):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


def publish(channel: str, event: dict[str, Any]) -> None:
    """Thread-safe publish (may be called from worker threads)."""
    if channel == "system":
        _recent_system.append(event)
        del _recent_system[:-50]
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None and (_loop is None or running is _loop):
        _deliver(channel, event)
    elif _loop is not None:
        _loop.call_soon_threadsafe(_deliver, channel, event)


def recent_system() -> list[dict]:
    return list(_recent_system)
