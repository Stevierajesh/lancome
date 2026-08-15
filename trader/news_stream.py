"""WebSocket news stream: runs in a daemon thread, pushes events to the queue."""

import logging
import threading

from alpaca.data.live import NewsDataStream

from . import config
from .events import Event, EventType, event_queue

log = logging.getLogger("news_stream")


class NewsStreamWorker:
    def __init__(self, api_key: str, secret_key: str):
        self._stream = NewsDataStream(api_key, secret_key)
        self._thread: threading.Thread | None = None

    def start(self):
        self._stream.subscribe_news(self._handle_news, "*")
        self._thread = threading.Thread(target=self._run, daemon=True, name="news-stream")
        self._thread.start()
        log.info("news stream started")

    def _run(self):
        try:
            self._stream.run()
        except Exception:
            log.exception("news stream crashed")

    async def _handle_news(self, news):
        """Called inside the stream's asyncio loop. Queue.put is thread-safe."""
        symbols = getattr(news, "symbols", []) or []
        if not symbols:
            return
        payload = {
            "headline": news.headline,
            "summary": getattr(news, "summary", "") or "",
            "source": getattr(news, "source", ""),
            "url": getattr(news, "url", ""),
            "created_at": news.created_at.isoformat() if news.created_at else "",
            "all_symbols": symbols,
        }
        for symbol in symbols:
            event_queue.put_nowait(Event(
                type=EventType.NEWS,
                symbol=symbol,
                payload=payload,
            ))

    def stop(self):
        try:
            self._stream.stop()
        except Exception:
            pass
        log.info("news stream stopped")
