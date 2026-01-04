import sqlite3
import time
from datetime import date, datetime, timedelta

DB_PATH = "signal_mania.db"

# =========================
# DB CONNECTION
# =========================
def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

# =========================
# INIT
# =========================
def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # USERS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        uid INTEGER PRIMARY KEY,
        sp INTEGER DEFAULT 0,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        node_start INTEGER DEFAULT 0,
        referrals INTEGER DEFAULT 0,
        mined_once INTEGER DEFAULT 0,
        daily_streak INTEGER DEFAULT 0,
        last_daily_claim TEXT
    )
    """)

    # TASKS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        uid INTEGER,
        task_id TEXT,
        completed INTEGER DEFAULT 0,
        PRIMARY KEY (uid, task_id)
    )
    """)

    # ALERTS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid INTEGER,
        symbol TEXT,
        target REAL,
        condition TEXT,
        status TEXT DEFAULT 'ACTIVE',
        created_at TEXT
    )
    """)

    conn.commit()
    conn.close()

# =========================
# USERS
# =========================
def get_user(uid):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("INSERT OR IGNORE INTO users(uid) VALUES(?)", (uid,))
    cur.execute("""
        SELECT sp, xp, level, node_start, referrals, daily_streak, last_daily_claim
        FROM users WHERE uid=?
    """, (uid,))
    row = cur.fetchone()
    conn.close()
    return row

def add_sp(uid, amount):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET sp = sp + ?, xp = xp + ?
        WHERE uid=?
    """, (amount, amount, uid))

    # Auto level up: 500 XP per level
    cur.execute("""
        UPDATE users
        SET level = 1 + (xp / 500)
        WHERE uid=?
    """, (uid,))

    conn.commit()
    conn.close()

# =========================
# MINING
# =========================
def start_node(uid):
    conn = get_conn()
    cur = conn.cursor()

    # If already running, do nothing
    cur.execute("SELECT node_start FROM users WHERE uid=?", (uid,))
    row = cur.fetchone()

    if row and row[0] and row[0] > 0:
        conn.close()
        return

    cur.execute("""
        UPDATE users
        SET node_start=?
        WHERE uid=?
    """, (int(time.time()), uid))

    conn.commit()
    conn.close()

def stop_node(uid):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET node_start=0, mined_once=1
        WHERE uid=?
    """, (uid,))

    conn.commit()
    conn.close()

# =========================
# TASKS
# =========================
def task_completed(uid, task_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT completed FROM tasks
        WHERE uid=? AND task_id=?
    """, (uid, task_id))

    row = cur.fetchone()
    conn.close()
    return row is not None and row[0] == 1

def complete_task(uid, task_id, reward, require_ref=False):
    if task_completed(uid, task_id):
        return False

    if require_ref:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT mined_once FROM users WHERE uid=?", (uid,))
        if cur.fetchone()[0] == 0:
            conn.close()
            return False
        conn.close()

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT OR REPLACE INTO tasks(uid, task_id, completed)
        VALUES (?, ?, 1)
    """, (uid, task_id))

    add_sp(uid, reward)

    conn.commit()
    conn.close()
    return True

# =========================
# DAILY CLAIM
# =========================
def daily_claim(uid):
    today = date.today().isoformat()
    conn = get_conn()
    cur = conn.cursor()

    # Ensure user row exists
    cur.execute("INSERT OR IGNORE INTO users(uid) VALUES(?)", (uid,))

    cur.execute("""
        SELECT daily_streak, last_daily_claim
        FROM users WHERE uid=?
    """, (uid,))

    row = cur.fetchone()

    # SAFE defaults
    streak = row[0] if row and row[0] is not None else 0
    last = row[1] if row else None

    if last == today:
        conn.close()
        return False, streak

    if last:
        last_date = datetime.fromisoformat(last).date()
        if last_date + timedelta(days=1) == date.today():
            streak += 1
        else:
            streak = 1
    else:
        streak = 1

    reward = min(500, 10 * (2 ** (streak - 1)))

    cur.execute("""
        UPDATE users
        SET daily_streak=?, last_daily_claim=?, sp=sp+?
        WHERE uid=?
    """, (streak, today, reward, uid))

    conn.commit()
    conn.close()
    return True, streak
# =========================
# ALERTS
# =========================
def create_alert(uid, symbol, target, condition):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO alerts(uid, symbol, target, condition, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (uid, symbol.upper(), target, condition, datetime.utcnow().isoformat()))

    conn.commit()
    conn.close()

def get_alerts(uid):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, symbol, target, condition, status
        FROM alerts
        WHERE uid=?
        ORDER BY status, created_at DESC
    """, (uid,))

    rows = cur.fetchall()
    conn.close()
    return rows

def delete_alert(uid, alert_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM alerts
        WHERE uid=? AND id=?
    """, (uid, alert_id))

    conn.commit()
    conn.close()

def mark_alert_hit(alert_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE alerts
        SET status='HIT'
        WHERE id=?
    """, (alert_id,))

    conn.commit()
    conn.close()
