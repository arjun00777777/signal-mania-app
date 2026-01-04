import os
import time
import json
import hmac
import hashlib
import threading
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from urllib.parse import parse_qsl
from dotenv import load_dotenv

from crypto_database import (
    init_db,
    get_user,
    start_node,
    stop_node,
    add_sp,
    complete_task,
    daily_claim,
    create_alert,
    get_alerts,
    delete_alert,
    mark_alert_hit
)
from tracker import MarketTracker

# =========================
# ENV & CONFIG
# =========================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "YOUR_BOT_USERNAME")

NODE_SECONDS = 6 * 60 * 60  # 6 hours

# =========================
# APP SETUP
# =========================
app = Flask(__name__)
CORS(app, supports_credentials=True)

tracker = MarketTracker()

# =========================
# TELEGRAM AUTH
# =========================
def verify(init_data):
    if not init_data:
        return None
    try:
        data = dict(parse_qsl(init_data))
        received_hash = data.pop("hash", None)
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()
        calculated_hash = hmac.new(
            secret,
            check_string.encode(),
            hashlib.sha256
        ).hexdigest()
        if calculated_hash == received_hash:
            return json.loads(data["user"])["id"]
    except Exception as e:
        print("Auth error:", e)
    return None

# =========================
# GLOBAL OPTIONS HANDLER
# =========================
@app.route("/<path:path>", methods=["OPTIONS"])
def options_handler(path):
    response = jsonify({"ok": True})
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

# =========================
# WHALE SIGNALS
# =========================
@app.route("/market/signals", methods=["GET"])
def market_signals():
    tracker.fetch()
    return jsonify(tracker.cache)

# =========================
# MINING
# =========================
@app.route("/user/mining/stats", methods=["POST", "OPTIONS"])
def mining_stats():
    if request.method == "OPTIONS":
        return jsonify({"ok": True})

    uid = verify(request.json.get("initData"))
    if not uid:
        return jsonify({"error": "unauthorized"}), 403

    sp, xp, level, node_start, refs, streak, last = get_user(uid)

    if node_start == 0:
        return jsonify({
            "sp": sp,
            "level": level,
            "running": False
        })

    elapsed = int(time.time()) - node_start
    if elapsed >= NODE_SECONDS:
        reward = (NODE_SECONDS // 60) * level
        add_sp(uid, reward)
        stop_node(uid)
        sp, xp, level, _, _, _, _ = get_user(uid)
        return jsonify({
            "sp": sp,
            "level": level,
            "running": False
        })

    return jsonify({
        "sp": sp,
        "level": level,
        "running": True,
        "remaining": NODE_SECONDS - elapsed
    })

@app.route("/user/mining/start", methods=["POST", "OPTIONS"])
def mining_start():
    if request.method == "OPTIONS":
        return jsonify({"ok": True})

    uid = verify(request.json.get("initData"))
    if not uid:
        return jsonify({"error": "unauthorized"}), 403

    start_node(uid)
    return jsonify({"ok": True})

# =========================
# TASKS
# =========================
@app.route("/task/complete", methods=["POST", "OPTIONS"])
def task_complete():
    if request.method == "OPTIONS":
        return jsonify({"ok": True})

    uid = verify(request.json.get("initData"))
    if not uid:
        return jsonify({"ok": False})

    data = request.json
    ok = complete_task(
        uid,
        data.get("task_id"),
        int(data.get("reward", 0)),
        data.get("require_ref", False)
    )
    return jsonify({"ok": ok})

# =========================
# DAILY CLAIM
# =========================
@app.route("/claim/daily", methods=["POST", "OPTIONS"])
def claim_daily():
    if request.method == "OPTIONS":
        return jsonify({"ok": True})

    uid = verify(request.json.get("initData"))
    if not uid:
        return jsonify({"ok": False})

    ok, streak = daily_claim(uid)
    return jsonify({"ok": ok, "streak": streak})

# =========================
# ALERTS
# =========================
@app.route("/alert/create", methods=["POST", "OPTIONS"])
def alert_create_route():
    if request.method == "OPTIONS":
        return jsonify({"ok": True})

    uid = verify(request.json.get("initData"))
    if not uid:
        return jsonify({"ok": False})

    data = request.json
    create_alert(
        uid,
        data["symbol"],
        float(data["target"]),
        data["condition"]
    )
    return jsonify({"ok": True})

@app.route("/alert/list", methods=["POST", "OPTIONS"])
def alert_list_route():
    if request.method == "OPTIONS":
        return jsonify({"ok": True})

    uid = verify(request.json.get("initData"))
    if not uid:
        return jsonify([])

    return jsonify(get_alerts(uid))

@app.route("/alert/delete", methods=["POST", "OPTIONS"])
def alert_delete_route():
    if request.method == "OPTIONS":
        return jsonify({"ok": True})

    uid = verify(request.json.get("initData"))
    if not uid:
        return jsonify({"ok": False})

    delete_alert(uid, request.json.get("id"))
    return jsonify({"ok": True})

# =========================
# PROFILE
# =========================
@app.route("/user/profile", methods=["POST", "OPTIONS"])
def user_profile():
    if request.method == "OPTIONS":
        return jsonify({"ok": True})

    uid = verify(request.json.get("initData"))
    if not uid:
        return jsonify({"error": "unauthorized"}), 403

    sp, xp, level, node_start, refs, streak, last = get_user(uid)

    return jsonify({
        "sp": sp,
        "level": level,
        "referrals": refs,
        "daily_streak": streak,
        "referral_link": f"https://t.me/{BOT_USERNAME}?start={uid}"
    })

# =========================
# ALERT ENGINE (BACKGROUND)
# =========================
def alert_engine():
    while True:
        tracker.fetch()
        for alert_id, uid, sym, target, cond in tracker.match_alerts():
            try:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": uid,
                        "text": f"🔔 ALERT HIT\n{sym} {cond} {target}"
                    },
                    timeout=5
                )
                mark_alert_hit(alert_id)
            except Exception as e:
                print("Alert notify failed:", e)
        time.sleep(60)

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    init_db()
    tracker.fetch()
    threading.Thread(target=alert_engine, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
