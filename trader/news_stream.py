"""News ingestion: WebSocket stream with REST polling fallback.

The free Alpaca tier allows only 1 WebSocket connection. If the stream fails
(connection limit, auth error, crash), we fall back to polling the REST news
endpoint on the scanner interval. Either way, events land in the same queue.
"""

import logging
import threading
import time

from alpaca.data.historical import NewsClient
from alpaca.data.live import NewsDataStream
from alpaca.data.requests import NewsRequest

from . import config
from .events import Event, EventType, event_queue

log = logging.getLogger("news_stream")


class NewsStreamWorker:
    """Tries WebSocket first; exposes poll_rest() as fallback for the main loop."""

    def __init__(self, api_key: str, secret_key: str):
        self._api_key = api_key
        self._secret_key = secret_key
        self._stream: NewsDataStream | None = None
        self._thread: threading.Thread | None = None
        self._ws_alive = False
        self._seen_ids: set[str] = set()  # deduplicate across polls

    # ------------------------------------------------------------------
    # WebSocket path
    # ------------------------------------------------------------------

    def start(self):
        """Attempt to start the WebSocket stream in a daemon thread."""
        self._stream = NewsDataStream(self._api_key, self._secret_key)
        self._stream.subscribe_news(self._handle_news, "*")
        self._thread = threading.Thread(target=self._run_ws, daemon=True, name="news-stream")
        self._thread.start()
        log.info("news WebSocket starting…")

    def _run_ws(self):
        try:
            self._ws_alive = True
            self._stream.run()
        except Exception as e:
            self._ws_alive = False
            log.warning("news WebSocket failed (falling back to REST polling): %s", e)

    @property
    def is_streaming(self) -> bool:
        return self._ws_alive and self._thread is not None and self._thread.is_alive()

    async def _handle_news(self, news):
        """Called inside the stream's asyncio loop. Queue.put is thread-safe."""
        news_id = getattr(news, "id", None)
        if news_id and news_id in self._seen_ids:
            return
        if news_id:
            self._seen_ids.add(news_id)
            self._trim_seen()

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

    # ------------------------------------------------------------------
    # REST polling fallback
    # ------------------------------------------------------------------

    def poll_rest(self, news_client: NewsClient):
        """Poll the REST news endpoint. Called from the main loop when WS is down."""
        try:
            response = news_client.get_news(NewsRequest(limit=50))
            items = list(response) if response else []
            new_count = 0
            for article in items:
                news_id = str(getattr(article, "id", ""))
                if news_id in self._seen_ids:
                    continue
                self._seen_ids.add(news_id)
                symbols = getattr(article, "symbols", []) or []
                if not symbols:
                    continue
                payload = {
                    "headline": getattr(article, "headline", ""),
                    "summary": getattr(article, "summary", "") or "",
                    "source": getattr(article, "source", ""),
                    "url": getattr(article, "url", ""),
                    "created_at": article.created_at.isoformat() if article.created_at else "",
                    "all_symbols": symbols,
                }
                for symbol in symbols:
                    event_queue.put_nowait(Event(
                        type=EventType.NEWS,
                        symbol=symbol,
                        payload=payload,
                    ))
                new_count += 1
            self._trim_seen()
            if new_count:
                log.info("REST news poll: %d new articles", new_count)
        except Exception as e:
            log.warning("REST news poll failed: %s", e)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _trim_seen(self):
        """Keep the dedup set from growing forever."""
        if len(self._seen_ids) > 5000:
            # drop oldest half (set is unordered, but this is fine for dedup)
            to_keep = list(self._seen_ids)[-2500:]
            self._seen_ids = set(to_keep)

    def stop(self):
        if self._stream:
            try:
                self._stream.stop()
            except Exception:
                pass
        self._ws_alive = False
        log.info("news stream stopped")
