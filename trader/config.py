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

# Signal parameters
SMA_FAST = 9
SMA_SLOW = 21
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

# Risk parameters
MAX_POSITION_PCT = 0.10              # max 10% of equity per position
MAX_OPEN_POSITIONS = 4
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
