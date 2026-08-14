import os

from dotenv import load_dotenv

load_dotenv()

ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")

# Liquid, high-volume names so 5-min bars are meaningful
WATCHLIST = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD", "MSFT", "AMZN"]

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

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
