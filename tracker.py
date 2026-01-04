import requests
import time
import logging
import os
import sqlite3

DB_PATH = "signal_mania.db"
COINGECKO_API = "https://api.coingecko.com/api/v3"

logging.basicConfig(level=logging.INFO)

class MarketTracker:
    def __init__(self):
        self.cache = []
        self.last_fetch = 0

    # =========================
    # FETCH MARKET DATA
    # =========================
    def fetch(self):
        # Cache for 60 seconds
        if time.time() - self.last_fetch < 60:
            return self.cache

        try:
            r = requests.get(
                f"{COINGECKO_API}/coins/markets",
                params={
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": 20,
                    "page": 1,
                    "price_change_percentage": "24h"
                },
                timeout=10
            )
            r.raise_for_status()

            data = r.json()
            self.cache = [
                {
                    "symbol": c["symbol"].upper(),
                    "price": round(c["current_price"], 2),
                    "change": round(c.get("price_change_percentage_24h", 0), 2)
                }
                for c in data
            ]

            self.last_fetch = time.time()
            logging.info(f"Whale data loaded: {len(self.cache)} coins")

        except Exception as e:
            logging.error(f"Market fetch failed: {e}")

        return self.cache

    # =========================
    # ALERT MATCHING
    # =========================
    def match_alerts(self):
        """
        Returns list of alerts that should trigger
        """
        self.fetch()
        triggered = []

        prices = {c["symbol"]: c["price"] for c in self.cache}

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        cur.execute("""
            SELECT id, uid, symbol, target, condition
            FROM alerts
            WHERE status='ACTIVE'
        """)

        for alert_id, uid, symbol, target, condition in cur.fetchall():
            price = prices.get(symbol.upper())
            if price is None:
                continue

            if condition == "ABOVE" and price >= target:
                triggered.append((alert_id, uid, symbol, target, condition))

            elif condition == "BELOW" and price <= target:
                triggered.append((alert_id, uid, symbol, target, condition))

        conn.close()
        return triggered

