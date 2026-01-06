from flask import Flask, request, jsonify
from flask_cors import CORS
import logging, time, threading, os, requests, hmac, hashlib, urllib.parse
import json
from dotenv import load_dotenv

from crypto_database import (
    init_db, ensure_user, get_conn,
    start_node, mining_stats, get_mining_history,   # ⬅ ADDED import
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
DEV_MODE = True  # dev/testing (correct)
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

def admin_only(uid):
    return uid == ADMIN_ID

app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": ["https://arjun00777777.github.io"]
    }
})
logging.basicConfig(level=logging.INFO)

init_db()

# --------------------------------------------------
# GLOBAL OPTIONS HANDLER
# --------------------------------------------------
@app.before_request
def handle_options():
    if request.method == "OPTIONS":
        return jsonify({"ok": True})

# --------------------------------------------------
# RATE LIMITER
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
# TELEGRAM INITDATA VERIFICATION
# --------------------------------------------------
def verify_init_data(init_data: str):
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data))
        hash_recv = parsed.pop("hash", None)
        if not hash_recv:
            return None

        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret = hashlib.sha256(BOT_TOKEN.encode()).digest()
        hash_calc = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(hash_calc, hash_recv):
            return None
        return parsed
    except Exception:
        return None

# --------------------------------------------------
# UID RESOLUTION
# --------------------------------------------------
def uid_from_req(req):
    data = req.get_json(silent=True) or {}

    if DEV_MODE:
        init_data = data.get("initData", "")
        if "user=" in init_data:
            parsed = dict(urllib.parse.parse_qsl(init_data))
            uid = int(json.loads(parsed["user"])["id"])
        else:
            uid = req.remote_addr.__hash__() % 10_000_000

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
# TELEGRAM SENDERS
# --------------------------------------------------
def send_telegram_alert(uid, symbol, price, condition, target):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": uid,
                "text": (
                    "🔔 Price Alert Triggered\n\n"
                    f"{symbol} {condition} {target}\n"
                    f"Current: ${price}"
                )
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
                "text": (
                    "⛏️ Mining Completed!\n\n"
                    f"Reward Earned: +{earned} SP\n\n"
                    "Your node is now idle.\n"
                    "Open SignalMania to start mining again 🚀"
                )
            },
            timeout=5
        )
    except Exception as e:
        logging.error(f"Mining notify error: {e}")

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
        with get_conn() as c:
            cur = c.cursor()
            cur.execute(
                "SELECT mining_notified FROM users WHERE uid=?",
                (uid,)
            )
            notified = cur.fetchone()[0]

            if not notified:
                send_mining_complete(uid, data["earned"])
                cur.execute(
                    "UPDATE users SET mining_notified=1 WHERE uid=?",
                    (uid,)
                )
                c.commit()

    return jsonify(data)

# 🆕 MINING HISTORY (NEW, SAFE)
@app.route("/user/mining/history", methods=["POST"])
def mining_history():
    uid = uid_from_req(request)
    if uid is None:
        return jsonify([]), 401
    return jsonify(get_mining_history(uid))

@app.route("/market/signals", methods=["POST"])
def market_signals():
    return jsonify(get_cached_signals())

@app.route("/market/chart", methods=["POST"])
def market_chart():
    d = request.json
    return jsonify(get_chart_data(d["symbol"], d.get("interval", "1m")))

@app.route("/user/profile", methods=["POST"])
def profile():
    uid = uid_from_req(request)
    if uid is None:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(get_profile(uid))

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
    ok, day = daily_claim(uid)
    return jsonify({"ok": ok, "day": day})

@app.route("/alert/create", methods=["POST"])
def alert_create():
    uid = uid_from_req(request)
    if uid is None:
        return jsonify({"error": "unauthorized"}), 401
    d = request.json
    create_alert(uid, d["symbol"].upper().strip(), float(d["target"]), d["condition"])
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

@app.route("/admin/analytics", methods=["POST"])
def admin_analytics():
    uid = uid_from_req(request)
    if uid is None or not admin_only(uid):
        return jsonify({"error": "forbidden"}), 403

    with get_conn() as c:
        cur = c.cursor()

        # Users growth (last 14 days)
        cur.execute("""
            SELECT DATE(ts) as day, COUNT(DISTINCT uid)
            FROM task_history
            GROUP BY day
            ORDER BY day DESC
            LIMIT 14
        """)
        users_growth = cur.fetchall()[::-1]

        # SP distribution
        cur.execute("""
            SELECT mining_sp, task_sp, referral_sp
            FROM users
        """)
        rows = cur.fetchall()

    return jsonify({
        "growth": users_growth,
        "sp_distribution": {
            "mining": sum(r[0] for r in rows),
            "tasks": sum(r[1] for r in rows),
            "referrals": sum(r[2] for r in rows)
        }
    })


# --------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
