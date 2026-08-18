"""Enrichment: build a complete case file for the judge from a candidate symbol."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from alpaca.data.historical import NewsClient
from alpaca.data.requests import NewsRequest

from . import config, data, signals
from .scanner import Scanner
from .signals import Signal

log = logging.getLogger("enrichment")


@dataclass
class CaseFile:
    symbol: str
    bars: list[dict] = field(default_factory=list)
    signal: Signal | None = None
    news: list[dict] = field(default_factory=list)
    scanner_context: dict = field(default_factory=dict)
    portfolio: dict = field(default_factory=dict)
    run_context: dict = field(default_factory=dict)


def build_run_context(account: dict, positions: dict, stocks_open: bool | None = None) -> dict:
    """Context for the judge that should not affect deterministic risk checks."""
    now = datetime.now(timezone.utc)
    equity = account.get("equity")
    target = config.TARGET_EQUITY
    context = {
        "timestamp": now.isoformat(),
        "market_date": now.date().isoformat(),
        "stocks_open": stocks_open,
        "open_positions": len(positions),
        "max_open_positions": config.MAX_OPEN_POSITIONS,
        "max_position_pct": config.MAX_POSITION_PCT,
        "daily_loss_limit_pct": config.DAILY_LOSS_LIMIT_PCT,
        "stop_loss_pct": config.STOP_LOSS_PCT,
        "take_profit_pct": config.TAKE_PROFIT_PCT,
        "target_equity": target or None,
    }
    if target and equity is not None:
        remaining = target - equity
        context["current_equity"] = equity
        context["target_remaining"] = remaining
        context["target_progress_pct"] = round(equity / target, 4) if target > 0 else None
        context["target_reached"] = remaining <= 0
    return context


def enrich(
    symbol: str,
    scanner: Scanner,
    news_client: NewsClient,
    account: dict,
    positions: dict,
) -> CaseFile:
    """Fetch bars, recent news, and evaluate signals for a symbol."""
    bars = data.get_bars([symbol]).get(symbol, [])

    news_items: list[dict] = []
    try:
        response = news_client.get_news(NewsRequest(
            symbols=symbol,
            limit=config.NEWS_LOOKBACK_ARTICLES,
        ))
        raw = response.data.get(symbol, []) if hasattr(response, "data") else []
        news_items = [
            {
                "headline": getattr(n, "headline", ""),
                "summary": getattr(n, "summary", "") or "",
                "created_at": n.created_at.isoformat() if n.created_at else "",
            }
            for n in raw
        ]
    except Exception as e:
        log.warning("news fetch for %s failed: %s", symbol, e)

    pos_symbol = symbol.replace("/", "")
    sig = signals.evaluate(symbol, bars, holding=pos_symbol in positions)

    return CaseFile(
        symbol=symbol,
        bars=bars,
        signal=sig,
        news=news_items,
        scanner_context=scanner.get_entry(symbol) or {},
        portfolio={"account": account, "positions": positions},
        run_context=build_run_context(account, positions),
    )
