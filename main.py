import os
import logging
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv

from crypto_database import ensure_user, get_conn

# --------------------------------------------------
# ENV
# --------------------------------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
APP_URL = "https://arjun00777777.github.io/signal-mania-app/"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------------------------------------
# /start COMMAND (WITH SAFE REFERRALS)
# --------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args  # referral payload

    # Ensure current user exists
    ensure_user(chat_id)

    # --------------------------------------------------
    # REFERRAL HANDLING (ANTI-ABUSE)
    # --------------------------------------------------
    if args:
        try:
            referrer_id = int(args[0])

            # Prevent self-referral
            if referrer_id != chat_id:
                with get_conn() as c:
                    cur = c.cursor()

                    # Check referrer exists
                    cur.execute(
                        "SELECT uid FROM users WHERE uid=?",
                        (referrer_id,)
                    )
                    if not cur.fetchone():
                        return

                    # 🔒 Check if THIS user already gave referral credit
                    cur.execute("""
                        SELECT 1 FROM task_history
                        WHERE uid=? AND task=?
                    """, (chat_id, f"referred_by_{referrer_id}"))

                    if cur.fetchone():
                        logger.info(
                            f"Duplicate referral ignored: {referrer_id} -> {chat_id}"
                        )
                        return

                    # ✅ Grant referral
                    cur.execute("""
                        UPDATE users
                        SET referral_count = referral_count + 1
                        WHERE uid=?
                    """, (referrer_id,))

                    # 🔒 Mark referral as used (0 reward, audit only)
                    cur.execute("""
                        INSERT INTO task_history (uid, task, reward, ts)
                        VALUES (?, ?, 0, ?)
                    """, (
                        chat_id,
                        f"referred_by_{referrer_id}",
                        datetime.date.today().isoformat()
                    ))

                    c.commit()
                    logger.info(f"Referral added: {referrer_id} -> {chat_id}")

        except Exception as e:
            logger.warning(f"Referral parse error: {e}")

    # --------------------------------------------------
    # MINI APP BUTTON
    # --------------------------------------------------
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🚀 Open SignalMania",
            web_app=WebAppInfo(url=APP_URL)
        )
    ]])

    await update.message.reply_text(
        "🚀 *Welcome to SignalMania*\n\n"
        "• Mining Node\n"
        "• Live Market Signals\n"
        "• Price Alerts 🔔\n"
        "• Daily & Referral Rewards\n\n"
        "Open the app below 👇",
        reply_markup=kb,
        parse_mode="Markdown"
    )

# --------------------------------------------------
# BOT BOOTSTRAP
# --------------------------------------------------
def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(10)
        .read_timeout(10)
        .write_timeout(10)
        .build()
    )

    app.add_handler(CommandHandler("start", start))

    logger.info("SignalMania bot started")
    app.run_polling(drop_pending_updates=True)

# --------------------------------------------------
if __name__ == "__main__":
    main()
