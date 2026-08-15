"""Event bus: thread-safe queue connecting stream threads to the main loop."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from queue import Queue


class EventType(Enum):
    NEWS = "news"
    SCANNER_HIT = "scanner_hit"
    TICK = "tick"
    SHUTDOWN = "shutdown"


@dataclass
class Event:
    type: EventType
    symbol: str
    payload: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


event_queue: Queue[Event] = Queue(maxsize=500)
