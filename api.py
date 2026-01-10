from flask import Flask, request, jsonify
from flask_cors import CORS
import logging, time, threading, os, requests, hmac, hashlib, urllib.parse, json
from dotenv import load_dotenv

from crypto_database import (
    init_db, ensure_user, get_conn,
    start_node, mining_stats, get_mining_history,
    daily_claim, get_profile, get_task_history,
    create_alert, get_alerts, delete_alert,
    referral_status, claim_referral_reward
)

from tracker import get_market_signals, get_chart_data

# --------------------------------------------------
# ENV
# --------------------------------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DEV_MODE = False

# --------------------------------------------------
# APP
# --------------------------------------------------
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

logging.basicConfig(level=logging.INFO)
init_db()

# --------------------------------------------------
# HELPERS
# --------------------------------------------------
def admin_only(uid):
    return uid == ADMIN_ID

# --------------------------------------------------
# OPTIONS HANDLER
# --------------------------------------------------
@app.before_request
def handle_options():
    if request.method == "OPTIONS":
        return jsonify({"ok": True})

# --------------------------------------------------
# RATE LIMIT
# --------------------------------------------------
RATE_LIMIT = {}
RATE_WINDOW = 10
RATE_MAX = 10

def rate_limit(uid, ip):
    key = f"{uid}:{ip}"
    now = time.time()
    bucket = RATE_LIMIT.get(key, [])
    bucket = [t for t in bucket if now - t < RATE_WINDOW]
    if len(bucket) >= RATE_MAX:
        return False
    bucket.append(now)
    RATE_LIMIT[key] = bucket
    return True

# --------------------------------------------------
# TELEGRAM INIT DATA VERIFY
# --------------------------------------------------
def verify_init_data(init_data):
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data))
        hash_recv = parsed.pop("hash", None)
        if not hash_recv:
            return None

        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        hash_calc = hmac.new(
            secret_key,
            data_check.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(hash_calc, hash_recv):
            return None

        return parsed
    except Exception as e:
        logging.error(e)
        return None

# --------------------------------------------------
# UID
# --------------------------------------------------
def uid_from_req(req):
    data = req.get_json(silent=True) or {}

    if DEV_MODE:
        uid = int(time.time()) % 1_000_000
        ensure_user(uid)
        return uid

    init_data = data.get("initData")
    parsed = verify_init_data(init_data)

    if not parsed or "user" not in parsed:
        return None

    uid = int(json.loads(parsed["user"])["id"])
    ensure_user(uid)

    ip = req.remote_addr or "0.0.0.0"
    if not rate_limit(uid, ip):
        return None

    return uid

# --------------------------------------------------
# MARKET CACHE
# --------------------------------------------------
MARKET_CACHE = {"data": [], "ts": 0}
CACHE_TTL = 60

def get_cached_signals(limit=50):
    now = time.time()
    if now - MARKET_CACHE["ts"] > CACHE_TTL:
        MARKET_CACHE["data"] = get_market_signals(limit)
        MARKET_CACHE["ts"] = now
    return MARKET_CACHE["data"]

# --------------------------------------------------
# MARKET INTEL (RESTORED)
# --------------------------------------------------
INTEL_CACHE = {"data": [], "ts": 0}
INTEL_TTL = 90

def generate_market_intel():
    signals = get_cached_signals(50)
    now = int(time.time())

    bullish, bearish = [], []

    for s in signals:
        ch = s.get("change_24h", 0)
        if ch >= 2:
            bullish.append({"symbol": s["symbol"], "change": round(ch, 2)})
        elif ch <= -2:
            bearish.append({"symbol": s["symbol"], "change": round(ch, 2)})

    feed = []

    total = len(bullish) + len(bearish)
    if total >= 4:
        feed.append({
            "type": "VOLATILITY",
            "msg": f"High volatility — {total} assets moving fast",
            "detail": "Market-wide movement detected",
            "window": "24h",
            "ts": now,
            "assets": {"bullish": bullish[:10], "bearish": bearish[:10]}
        })

    if len(bearish) >= 2:
        feed.append({
            "type": "BEARISH_GROUP",
            "msg": "Broad market weakness",
            "detail": "Multiple assets under selling pressure",
            "window": "24h",
            "ts": now,
            "assets": bearish[:10]
        })

    if bullish:
        top = max(bullish, key=lambda x: x["change"])
        feed.append({
            "type": "BULLISH",
            "msg": f"{top['symbol']} showing strong momentum",
            "detail": f"+{top['change']}% in last 24h",
            "window": "24h",
            "ts": now,
            "assets": [top]
        })

    if not feed:
        feed.append({
            "type": "STABLE",
            "msg": "Market stable",
            "detail": "No abnormal activity detected",
            "window": "24h",
            "ts": now,
            "assets": []
        })

    return feed[:6]

def get_market_intel():
    now = time.time()
    if now - INTEL_CACHE["ts"] > INTEL_TTL:
        INTEL_CACHE["data"] = generate_market_intel()
        INTEL_CACHE["ts"] = now
    return INTEL_CACHE["data"]

# --------------------------------------------------
# TELEGRAM SENDERS
# --------------------------------------------------
def send_telegram_alert(uid, symbol, price, condition, target):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": uid,
                "text": f"🔔 {symbol} {condition} {target}\nCurrent: ${price}"
            },
            timeout=5
        )
    except Exception as e:
        logging.error(e)

def send_mining_complete(uid, earned):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": uid,
                "text": f"⛏️ Mining completed\n+{earned} SP earned"
            },
            timeout=5
        )
    except Exception as e:
        logging.error(e)

# --------------------------------------------------
# ALERT ENGINE
# --------------------------------------------------
def alert_engine():
    while True:
        try:
            signals = get_cached_signals(50)
            price_map = {s["symbol"]: s["price"] for s in signals}

            with get_conn() as c:
                cur = c.cursor()
                cur.execute("""
                    SELECT id, uid, symbol, target, condition
                    FROM alerts WHERE status='ACTIVE'
                """)
                alerts = cur.fetchall()

            for aid, uid, sym, target, cond in alerts:
                price = price_map.get(sym)
                if price is None:
                    continue

                EPS = target * 0.001
                hit = (
                    cond == "ABOVE" and price >= target - EPS or
                    cond == "BELOW" and price <= target + EPS
                )

                if hit:
                    send_telegram_alert(uid, sym, price, cond, target)
                    with get_conn() as c:
                        cur = c.cursor()
                        cur.execute(
                            "UPDATE alerts SET status='TRIGGERED' WHERE id=?",
                            (aid,)
                        )
                        c.commit()
        except Exception as e:
            logging.error(e)

        time.sleep(5)

threading.Thread(target=alert_engine, daemon=True).start()

# --------------------------------------------------
# ROUTES
# --------------------------------------------------
@app.route("/user/mining/start", methods=["POST"])
def mining_start():
    uid = uid_from_req(request)
    if uid is None:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"ok": start_node(uid)})

@app.route("/user/mining/stats", methods=["POST"])
def mining_stat():
    uid = uid_from_req(request)
    if uid is None:
        return jsonify({"error": "unauthorized"}), 401

    data = mining_stats(uid)

    if data.get("completed"):
        send_mining_complete(uid, data["earned"])

    return jsonify(data)

@app.route("/user/mining/history", methods=["POST"])
def mining_history():
    uid = uid_from_req(request)
    if uid is None:
        return jsonify([]), 401
    return jsonify(get_mining_history(uid))

@app.route("/market/signals", methods=["POST"])
def market_signals():
    return jsonify(get_cached_signals())

@app.route("/market/intel", methods=["POST"])
def market_intel():
    return jsonify(get_market_intel())

@app.route("/market/chart", methods=["POST"])
def market_chart():
    d = request.json
    return jsonify(get_chart_data(d["symbol"], d.get("interval", "1m")))

@app.route("/user/profile", methods=["POST"])
def profile():
    uid = uid_from_req(request)
    if uid is None:
        return jsonify({"error": "unauthorized"}), 401

    p = get_profile(uid)

    now = int(time.time())
    last = int(p.get("last_daily_claim") or 0)
    remaining = max(0, 86400 - (now - last))

    p["daily"] = {
        "remaining": remaining
    }

    # ✅ MONTHLY DATA (SAFE)
    p["monthly"] = {
        "day": int(p.get("monthly_day") or 0)
    }

    return jsonify(p)

@app.route("/tasks/history", methods=["POST"])
def task_history():
    uid = uid_from_req(request)
    if uid is None:
        return jsonify([]), 401
    return jsonify(get_task_history(uid))

@app.route("/claim/daily", methods=["POST"])
def claim_daily():
    uid = uid_from_req(request)
    if uid is None:
        return jsonify({"error": "unauthorized"}), 401

    ok, day, remaining = daily_claim(uid)
    return jsonify({
        "ok": ok,
        "day": day,
        "remaining": remaining
    })

@app.route("/alert/create", methods=["POST"])
def alert_create():
    uid = uid_from_req(request)
    if uid is None:
        return jsonify({"error": "unauthorized"}), 401

    d = request.json or {}
    symbol = str(d.get("symbol", "")).upper().strip()

    if not symbol.isalpha() or len(symbol) < 2:
        return jsonify({"ok": False, "error": "Invalid coin symbol"}), 400

    try:
        target = float(d.get("target"))
        if target <= 0:
            raise ValueError
    except:
        return jsonify({"ok": False, "error": "Invalid target price"}), 400

    condition = d.get("condition")
    if condition not in ("ABOVE", "BELOW"):
        return jsonify({"ok": False, "error": "Invalid condition"}), 400

    valid = {s["symbol"] for s in get_cached_signals(200)}
    if symbol not in valid:
        return jsonify({"ok": False, "error": "Unknown coin"}), 400

    create_alert(uid, symbol, target, condition)
    return jsonify({"ok": True})

@app.route("/alert/list", methods=["POST"])
def alert_list():
    uid = uid_from_req(request)
    if uid is None:
        return jsonify([]), 401
    return jsonify(get_alerts(uid))

@app.route("/alert/delete", methods=["POST"])
def alert_delete():
    uid = uid_from_req(request)
    if uid is None:
        return jsonify({"error": "unauthorized"}), 401
    delete_alert(uid, request.json["id"])
    return jsonify({"ok": True})

@app.route("/referral/status", methods=["POST"])
def referral_status_route():
    uid = uid_from_req(request)
    if uid is None:
        return jsonify([]), 401
    return jsonify(referral_status(uid))

@app.route("/referral/claim", methods=["POST"])
def referral_claim():
    uid = uid_from_req(request)
    if uid is None:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"ok": claim_referral_reward(uid, int(request.json["referrals"]))})

@app.route("/admin/stats", methods=["POST"])
def admin_stats():
    uid = uid_from_req(request)
    if uid is None or not admin_only(uid):
        return jsonify({"error": "forbidden"}), 403

    with get_conn() as c:
        cur = c.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users WHERE node_start != 0")
        active_miners = cur.fetchone()[0]
        cur.execute("SELECT SUM(sp) FROM users")
        total_sp = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM alerts WHERE status='ACTIVE'")
        active_alerts = cur.fetchone()[0]

    return jsonify({
        "users": users,
        "active_miners": active_miners,
        "total_sp": total_sp,
        "active_alerts": active_alerts
    })


def admin_analytics():
    uid = uid_from_req(request)
    if uid is None or not admin_only(uid):
        return jsonify({"error": "forbidden"}), 403

    with get_conn() as c:
        cur = c.cursor()
        cur.execute("""
            SELECT DATE(ts), COUNT(DISTINCT uid)
            FROM task_history
            GROUP BY DATE(ts)
            ORDER BY DATE(ts) DESC
            LIMIT 14
        """)
        growth = cur.fetchall()[::-1]

        cur.execute("SELECT mining_sp, task_sp, referral_sp FROM users")
        rows = cur.fetchall()

    return jsonify({
        "growth": growth,
        "sp_distribution": {
            "mining": sum(r[0] for r in rows),
            "tasks": sum(r[1] for r in rows),
            "referrals": sum(r[2] for r in rows)
        }
    })

@app.route("/admin/advanced-stats", methods=["POST"])
def admin_advanced_stats():
    uid = uid_from_req(request)
    if uid is None or not admin_only(uid):
        return jsonify({"error": "forbidden"}), 403

    now = int(time.time())
    today_start = now - 86400

    with get_conn() as c:
        cur = c.cursor()

        # New users today
        cur.execute("""
            SELECT COUNT(*) FROM users
            WHERE uid IN (
                SELECT uid FROM task_history
                WHERE ts >= ?
            )
        """, (today_start,))
        new_today = cur.fetchone()[0]

        # Active users last 24h
        cur.execute("""
            SELECT COUNT(DISTINCT uid)
            FROM task_history
            WHERE ts >= ?
        """, (today_start,))
        active_24h = cur.fetchone()[0]

        # Total referrals
        cur.execute("SELECT SUM(referral_count) FROM users")
        total_referrals = cur.fetchone()[0] or 0

        # Top referrer
        cur.execute("""
            SELECT uid, referral_count
            FROM users
            ORDER BY referral_count DESC
            LIMIT 1
        """)
        top = cur.fetchone()

    return jsonify({
        "new_today": new_today,
        "active_24h": active_24h,
        "total_referrals": total_referrals,
        "top_referrer": {
            "uid": top[0],
            "count": top[1]
        } if top else None
    })

@app.route("/admin/suspicious-users", methods=["POST"])
def admin_suspicious_users():
    uid = uid_from_req(request)
    if uid is None or not admin_only(uid):
        return jsonify({"error": "forbidden"}), 403

    now = int(time.time())
    last_24h = now - 86400
    last_10m = now - 600

    suspicious = []

    with get_conn() as c:
        cur = c.cursor()

        # Rule 1: High referrals, low activity
        cur.execute("""
            SELECT u.uid, u.referral_count,
                   COUNT(t.id) as actions
            FROM users u
            LEFT JOIN task_history t ON u.uid = t.uid
            GROUP BY u.uid
            HAVING u.referral_count >= 3 AND actions <= 1
        """)
        for uid_, refs, acts in cur.fetchall():
            suspicious.append({
                "uid": uid_,
                "reason": "High referrals, low activity",
                "meta": f"refs={refs}, actions={acts}"
            })

        # Rule 2: Fast SP growth (24h)
        cur.execute("""
            SELECT uid, reward
            FROM task_history
            WHERE ts >= ?
        """, (last_24h,))
        sp_24h = {}
        for u, r in cur.fetchall():
            sp_24h[u] = sp_24h.get(u, 0) + (r or 0)

        for u, total in sp_24h.items():
            if total >= 300:
                suspicious.append({
                    "uid": u,
                    "reason": "Fast SP growth (24h)",
                    "meta": f"sp_24h={total}"
                })

        # Rule 3: Burst actions (10 minutes)
        cur.execute("""
            SELECT uid, COUNT(*) as cnt
            FROM task_history
            WHERE ts >= ?
            GROUP BY uid
            HAVING cnt >= 5
        """, (last_10m,))
        for u, cnt in cur.fetchall():
            suspicious.append({
                "uid": u,
                "reason": "Burst activity",
                "meta": f"events_10m={cnt}"
            })

    return jsonify(suspicious)


# --------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
