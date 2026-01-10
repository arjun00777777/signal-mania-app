import sqlite3
import time
import datetime

DB = "signal_mania.db"

# --------------------------------------------------
# DB CONNECTION
# --------------------------------------------------
def get_conn():
    return sqlite3.connect(DB, check_same_thread=False)

# --------------------------------------------------
# INIT & MIGRATION
# --------------------------------------------------
def init_db():
    with get_conn() as c:
        cur = c.cursor()

        # USERS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            uid INTEGER PRIMARY KEY,
            sp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            node_start INTEGER DEFAULT 0,
            daily_day INTEGER DEFAULT 0,
            last_daily_claim INTEGER,
            referral_count INTEGER DEFAULT 0
        )
        """)

        # SAFE MIGRATIONS
        def add_column(name, coldef):
            try:
                cur.execute(f"ALTER TABLE users ADD COLUMN {name} {coldef}")
            except sqlite3.OperationalError:
                pass

        add_column("mining_notified", "INTEGER DEFAULT 0")
        add_column("mining_sp", "INTEGER DEFAULT 0")
        add_column("task_sp", "INTEGER DEFAULT 0")
        add_column("referral_sp", "INTEGER DEFAULT 0")
        add_column("last_daily_ts", "INTEGER DEFAULT 0")
        add_column("monthly_day", "INTEGER DEFAULT 0")
        add_column("last_monthly_claim", "INTEGER DEFAULT 0")

        # ALERTS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid INTEGER,
            symbol TEXT,
            target REAL,
            condition TEXT,
            status TEXT DEFAULT 'ACTIVE'
        )
        """)

        # TASK HISTORY
        cur.execute("""
        CREATE TABLE IF NOT EXISTS task_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid INTEGER,
            task TEXT,
            reward INTEGER,
            ts TEXT
        )
        """)

        # REFERRAL REWARDS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS referral_rewards (
            uid INTEGER,
            referrals INTEGER,
            claimed INTEGER DEFAULT 0,
            PRIMARY KEY(uid, referrals)
        )
        """)

        # MINING HISTORY
        cur.execute("""
        CREATE TABLE IF NOT EXISTS mining_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid INTEGER,
            start_ts INTEGER,
            end_ts INTEGER,
            earned INTEGER
        )
        """)

        # MIGRATE OLD DATA
        cur.execute("""
            UPDATE users
            SET task_sp = sp
            WHERE task_sp = 0 AND mining_sp = 0 AND referral_sp = 0
        """)
        cur.execute("""
            UPDATE users
            SET sp = mining_sp + task_sp + referral_sp
        """)

        c.commit()

# --------------------------------------------------
# USER
# --------------------------------------------------
def ensure_user(uid):
    with get_conn() as c:
        cur = c.cursor()
        cur.execute("INSERT OR IGNORE INTO users(uid) VALUES (?)", (uid,))
        c.commit()

# --------------------------------------------------
# LEVEL
# --------------------------------------------------
def calc_level(sp):
    return 1 + (sp // 500)

# --------------------------------------------------
# MINING
# --------------------------------------------------
MINING_SECONDS = 6 * 60 * 60  # 6 hours

def start_node(uid):
    ensure_user(uid)
    now = int(time.time())

    with get_conn() as c:
        cur = c.cursor()
        cur.execute("SELECT node_start FROM users WHERE uid=?", (uid,))
        start = cur.fetchone()[0]

        # Anti-abuse: prevent rapid restart
        if start and now - start < MINING_SECONDS:
            return False

        cur.execute("""
            UPDATE users
            SET node_start=?, mining_notified=0
            WHERE uid=?
        """, (now, uid))

        c.commit()
        return True

def mining_stats(uid):
    ensure_user(uid)
    now = int(time.time())

    with get_conn() as c:
        cur = c.cursor()
        cur.execute("""
            SELECT node_start, mining_sp, task_sp, referral_sp
            FROM users WHERE uid=?
        """, (uid,))
        node_start, mining_sp, task_sp, referral_sp = cur.fetchone()

    base_sp = mining_sp + task_sp + referral_sp

    if not node_start:
        return {
            "running": False,
            "remaining": 0,
            "sp": base_sp,
            "level": calc_level(base_sp)
        }

    elapsed = now - node_start
    earned = min(elapsed // 300, MINING_SECONDS // 300)
    remaining = max(0, MINING_SECONDS - elapsed)

    if elapsed >= MINING_SECONDS:
        with get_conn() as c:
            cur = c.cursor()

            cur.execute("""
                INSERT INTO mining_history (uid, start_ts, end_ts, earned)
                VALUES (?, ?, ?, ?)
            """, (uid, node_start, now, earned))

            cur.execute("""
                UPDATE users
                SET mining_sp = mining_sp + ?, node_start = 0
                WHERE uid=?
            """, (earned, uid))

            cur.execute("""
                UPDATE users
                SET sp = mining_sp + task_sp + referral_sp
                WHERE uid=?
            """, (uid,))

            c.commit()

        total = base_sp + earned
        return {
            "running": False,
            "remaining": 0,
            "sp": total,
            "level": calc_level(total),
            "completed": True,
            "earned": earned
        }

    total = base_sp + earned
    return {
        "running": True,
        "remaining": remaining // 60,
        "sp": total,
        "level": calc_level(total)
    }

# --------------------------------------------------
# MINING HISTORY FETCH
# --------------------------------------------------
def get_mining_history(uid, limit=20):
    with get_conn() as c:
        cur = c.cursor()
        cur.execute("""
            SELECT start_ts, end_ts, earned
            FROM mining_history
            WHERE uid=?
            ORDER BY id DESC
            LIMIT ?
        """, (uid, limit))
        return cur.fetchall()

# --------------------------------------------------
# DAILY TASKS
# --------------------------------------------------
DAILY_REWARDS = [
    10, 12, 14, 16, 18,
    20, 22, 24, 26, 28,
    30, 32, 34, 36, 38,
    40, 42, 44, 46, 48,
    50, 55, 60, 65, 70,
    80, 90, 100, 120, 150
]

DAILY_COOLDOWN = 24 * 60 * 60  # 24 hours

def daily_claim(uid):
    ensure_user(uid)
    now = int(time.time())

    with get_conn() as c:
        cur = c.cursor()
        cur.execute("""
            SELECT daily_day, last_daily_claim
            FROM users WHERE uid=?
        """, (uid,))
        day, last = cur.fetchone()

        # FORCE last_daily_claim TO INTEGER
        try:
            last = int(last)
        except (TypeError, ValueError):
            last = 0

        # 24 hour cooldown
        if last and now - last < 86400:
            remaining = 86400 - (now - last)
            return False, day, remaining

        # reset streak if missed 48h
        if last and now - last > 172800:
            day = 0

        day = min(day + 1, 30)
        reward = DAILY_REWARDS[day - 1]

        cur.execute("""
            UPDATE users
            SET task_sp = task_sp + ?,
                daily_day = ?,
                last_daily_claim = ?
            WHERE uid=?
        """, (reward, day, now, uid))

        cur.execute("""
            UPDATE users
            SET sp = mining_sp + task_sp + referral_sp
            WHERE uid=?
        """, (uid,))

        cur.execute("""
            INSERT INTO task_history(uid, task, reward, ts)
            VALUES (?, ?, ?, ?)
        """, (uid, f"Daily Day {day}", reward, now))

        c.commit()

        return True, day, 0

def get_task_history(uid):
    with get_conn() as c:
        cur = c.cursor()
        cur.execute("""
            SELECT task, reward, ts
            FROM task_history
            WHERE uid=?
            ORDER BY id DESC
        """, (uid,))
        return cur.fetchall()

# --------------------------------------------------
# MONTHLY TASKS
# --------------------------------------------------

MONTHLY_REWARDS = [
    20, 20, 25, 25, 30, 30, 35,
    35, 40, 40, 45, 45, 50,
    50, 60, 60, 70, 70, 80,
    80, 90, 90, 100, 100,
    120, 120, 150, 150, 200, 300
]

def monthly_claim(uid):
    ensure_user(uid)
    now = int(time.time())

    with get_conn() as c:
        cur = c.cursor()
        cur.execute("""
            SELECT monthly_day, last_monthly_claim
            FROM users WHERE uid=?
        """, (uid,))
        day, last = cur.fetchone()

        last = int(last or 0)

        # 24-hour cooldown
        if last and now - last < 86400:
            return False, day, 86400 - (now - last)

        # reset after 30 days
        if day >= 30:
            day = 0

        day += 1
        reward = MONTHLY_REWARDS[day - 1]

        cur.execute("""
            UPDATE users
            SET monthly_day = ?,
                last_monthly_claim = ?,
                task_sp = task_sp + ?
            WHERE uid=?
        """, (day, now, reward, uid))

        cur.execute("""
            UPDATE users
            SET sp = mining_sp + task_sp + referral_sp
            WHERE uid=?
        """, (uid,))

        cur.execute("""
            INSERT INTO task_history(uid, task, reward, ts)
            VALUES (?, ?, ?, ?)
        """, (uid, f"Monthly Day {day}", reward, now))

        c.commit()

        return True, day, 0


# --------------------------------------------------
# REFERRALS
# --------------------------------------------------
REFERRAL_TASKS = {1: 50, 5: 200, 10: 500}

def referral_status(uid):
    with get_conn() as c:
        cur = c.cursor()
        cur.execute("SELECT referral_count FROM users WHERE uid=?", (uid,))
        count = cur.fetchone()[0]

        res = []
        for r, reward in REFERRAL_TASKS.items():
            cur.execute("""
                SELECT claimed FROM referral_rewards
                WHERE uid=? AND referrals=?
            """, (uid, r))
            row = cur.fetchone()
            res.append({
                "referrals": r,
                "reward": reward,
                "claimed": bool(row and row[0]),
                "eligible": count >= r
            })
        return res

def claim_referral_reward(uid, referrals):
    reward = REFERRAL_TASKS.get(referrals)
    if not reward:
        return False

    with get_conn() as c:
        cur = c.cursor()

        cur.execute("""
            SELECT claimed FROM referral_rewards
            WHERE uid=? AND referrals=?
        """, (uid, referrals))
        row = cur.fetchone()
        if row and row[0]:
            return False

        cur.execute("""
            INSERT OR REPLACE INTO referral_rewards(uid, referrals, claimed)
            VALUES (?, ?, 1)
        """, (uid, referrals))

        cur.execute("""
            UPDATE users
            SET referral_sp = referral_sp + ?
            WHERE uid=?
        """, (reward, uid))

        cur.execute("""
            UPDATE users
            SET sp = mining_sp + task_sp + referral_sp
            WHERE uid=?
        """, (uid,))

        cur.execute("""
            INSERT INTO task_history(uid, task, reward, ts)
            VALUES (?, ?, ?, ?)
        """, (uid, f"Referral {referrals}", reward, int(time.time())))

        c.commit()
        return True

# --------------------------------------------------
# PROFILE
# --------------------------------------------------
def get_profile(uid):
    ensure_user(uid)
    with get_conn() as c:
        cur = c.cursor()
        cur.execute("""
            SELECT mining_sp, task_sp, referral_sp, sp,
                   daily_day, last_daily_claim, referral_count
            FROM users WHERE uid=?
        """, (uid,))
        m, t, r, sp, d, last, ref = cur.fetchone()

    return {
        "sp": sp,
        "level": calc_level(sp),
        "daily_day": d,
        "last_daily_claim": last,
        "referral_count": ref,
        "breakdown": {
            "mining": m,
            "tasks": t,
            "referrals": r
        }
    }

# --------------------------------------------------
# ALERTS
# --------------------------------------------------
def create_alert(uid, symbol, target, condition):
    ensure_user(uid)
    with get_conn() as c:
        cur = c.cursor()
        cur.execute("""
            INSERT INTO alerts (uid, symbol, target, condition, status)
            VALUES (?, ?, ?, ?, 'ACTIVE')
        """, (uid, symbol.upper(), target, condition))
        c.commit()

def get_alerts(uid):
    with get_conn() as c:
        cur = c.cursor()
        cur.execute("""
            SELECT id, symbol, target, condition, status
            FROM alerts
            WHERE uid=?
            ORDER BY id DESC
        """, (uid,))
        return cur.fetchall()

def delete_alert(uid, alert_id):
    with get_conn() as c:
        cur = c.cursor()
        cur.execute("""
            DELETE FROM alerts
            WHERE id=? AND uid=?
        """, (alert_id, uid))
        c.commit()
