import requests
import time
import logging

logger = logging.getLogger(__name__)

BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr"
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

# --------------------------------------------------
# LIVE MARKET SIGNALS
# --------------------------------------------------
def get_market_signals(limit=20):
    """
    Fetch ALL Binance tickers once,
    sort by quoteVolume, return top coins
    """
    try:
        r = requests.get(BINANCE_TICKER_URL, timeout=10)
        r.raise_for_status()
        data = r.json()

        usdt_pairs = [
            d for d in data
            if d["symbol"].endswith("USDT")
            and not d["symbol"].endswith("BUSDUSDT")
        ]

        # ✅ FIXED SORTING BUG
        usdt_pairs.sort(
            key=lambda x: float(x["quoteVolume"]),
            reverse=True
        )

        signals = []
        for d in usdt_pairs[:limit]:
            signals.append({
                "symbol": d["symbol"].replace("USDT", ""),
                "price": round(float(d["lastPrice"]), 4),
                "change_24h": round(float(d["priceChangePercent"]), 2)
            })

        return signals

    except Exception as e:
        logger.error(f"Binance ticker error: {e}")
        return []

# --------------------------------------------------
# REAL CHART DATA
# --------------------------------------------------
def get_chart_data(symbol, interval="1m", limit=100):
    try:
        symbol = symbol.upper() + "USDT"

        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }

        r = requests.get(BINANCE_KLINES_URL, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        candles = []
        for k in data:
            candles.append({
                "time": int(k[0] / 1000),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4])
            })

        return candles

    except Exception as e:
        logger.error(f"Binance chart error ({symbol}): {e}")
        return []
