import os

from dotenv import load_dotenv

load_dotenv()

ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")

# Liquid, high-volume names so 5-min bars are meaningful
WATCHLIST = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD", "MSFT", "AMZN"]

# Crypto trades 24/7 on Alpaca. Data/order symbols use a slash ("BTC/USD");
# position symbols drop it ("BTCUSD").
CRYPTO_WATCHLIST = ["BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD"]

POLL_INTERVAL_SECONDS = 300          # 5 minutes
BAR_TIMEFRAME_MINUTES = 5
LOOKBACK_BARS = 100                  # ~1.5 trading days of 5-min bars

# Signal parameters — trend (slow)
SMA_FAST = 9
SMA_SLOW = 21
RSI_PERIOD = 14
RSI_OVERSOLD = 25                    # tighter than 30 — only fire on real capitulation
RSI_OVERBOUGHT = 75                  # tighter than 70 — let winners run longer

# Signal parameters — reactive (fast)
VOLUME_SPIKE_THRESHOLD = 2.5         # current bar volume >= 2.5x the 20-bar average
GAP_UP_THRESHOLD = 1.0               # open >= 1% above previous close
MOMENTUM_PERIOD = 5                  # rate of change over last 5 bars
MOMENTUM_BUY_THRESHOLD = 1.5         # +1.5% over MOMENTUM_PERIOD bars
MOMENTUM_SELL_THRESHOLD = 2.0        # -2% triggers sell if holding

# Signal parameters — VWAP
VWAP_DEVIATION_BUY_PCT = -1.5        # buy when price is 1.5%+ below VWAP (mean reversion)
VWAP_DEVIATION_SELL_PCT = 2.0        # sell when price is 2%+ above VWAP (overextended)

# Signal parameters — relative volume (time-of-day)
RELATIVE_VOLUME_TOD_THRESHOLD = 3.0  # 3x normal volume for this time slot

# Signal parameters — bid/ask pressure
BID_ASK_IMBALANCE_THRESHOLD = 0.70   # 70%+ on one side = directional pressure
SPREAD_MAX_PCT = 0.3                 # ignore quotes with spread > 0.3%

# Signal parameters — sector correlation break
CORRELATION_BREAK_PCT = 1.5          # symbol diverges from benchmark by 1.5%+
CORRELATION_LOOKBACK = 10            # compare returns over last 10 bars

# Signal parameters — multi-timeframe
HOURLY_SMA_FAST = 5
HOURLY_SMA_SLOW = 13

# Risk parameters
# Keep MAX_POSITION_PCT * MAX_OPEN_POSITIONS under 1.0 — _entry_budget clamps to
# available cash, so anything over 100% just starves the last few slots into
# dust orders (13 x 10% = 130% meant an effective cap of ~10).
MAX_POSITION_PCT = 0.07              # max 7% of equity per position (13 x 7% = 91%)
MAX_OPEN_POSITIONS = 13
DAILY_LOSS_LIMIT_PCT = 0.02          # stop trading if down 2% on the day
STOP_LOSS_PCT = 0.02                 # exit position at -2%
TAKE_PROFIT_PCT = 0.03               # exit position at +3%

# Judge
JUDGE_ENABLED = True
JUDGE_TIMEOUT_SECONDS = 120
JUDGE_MIN_CONFIDENCE = 0.6
JUDGE_BACKEND = os.environ.get("JUDGE_BACKEND", "codex")   # "codex", "ollama", "none"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# Scanner
SCANNER_INTERVAL_SECONDS = 120       # poll screener every 2 minutes
SCANNER_TOP_ACTIVES = 20
SCANNER_TOP_MOVERS = 10
SCANNER_WATCHLIST_MAX = 25
SCANNER_ENTRY_TTL_SECONDS = 1800     # drop symbols after 30 min of no activity

# News
NEWS_STREAM_ENABLED = True
NEWS_LOOKBACK_ARTICLES = 5

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
