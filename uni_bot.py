import os
import sqlite3
import logging
from datetime import datetime
from typing import Optional, Iterable, Tuple, Any

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================
# Config
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "archive.db").strip() or "archive.db"
TZ_NOTE = "UTC"  # نخليها UTC لأننا نخزن datetime.utcnow()

# Logging (Railway يحب هذا)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("uni-bot")


# =========================
# UI (Keyboards)
# =========================
MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📚 المواد"), KeyboardButton("🗂️ نسخة احتياطية")],
        [KeyboardButton("🧾 آخر الملفات"), KeyboardButton("⭐ المفضلة")],
        [KeyboardButton("🔎 بحث"), KeyboardButton("ℹ️ مساعدة")],
    ],
    resize_keyboard=True,
)

SUBJECTS = [
    "Grammar", "Phonetics", "Poetry", "Drama", "Novel",
    "Listening", "Writing", "Linguistics", "ELT", "Other"
]

def subjects_keyboard() -> ReplyKeyboardMarkup:
    rows = []
    row = []
    for i, s in enumerate(SUBJECTS, 1):
        row.append(KeyboardButton(f"📘 {s}"))
        if i % 2 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([KeyboardButton("⬅️ رجوع")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

BACK_KB = ReplyKeyboardMarkup([[KeyboardButton("⬅️ رجوع")]], resize_keyboard=True)


# =========================
# DB Layer
# =========================
def db_connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")  # أفضل للاستخدام المتكرر
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            file_type TEXT NOT NULL,
            file_id TEXT NOT NULL,
            file_unique_id TEXT NOT NULL,
            caption TEXT,
            created_at TEXT NOT NULL,
            is_fav INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    con.commit()
    return con

def insert_file(
    user_id: int,
    chat_id: int,
    message_id: int,
    subject: str,
    file_type: str,
    file_id: str,
    file_unique_id: str,
    caption: Optional[str],
) -> int:
    created_at = datetime.utcnow().isoformat()
    with db_connect() as con:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO files (user_id, chat_id, message_id, subject, file_type, file_id, file_unique_id, caption, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                chat_id,
                message_id,
                subject,
                file_type,
                file_id,
                file_unique_id,
                (caption or "").strip(),
                created_at,
            ),
        )
        con.commit()
        return int(cur.lastrowid)

def list_recent(user_id: int, limit: int = 10) -> Iterable[sqlite3.Row]:
    with db_connect() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, subject, file_type, caption, created_at, is_fav
            FROM files
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return cur.fetchall()

def list_favs(user_id: int, limit: int = 20) -> Iterable[sqlite3.Row]:
    with db_connect() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, subject, file_type, caption, created_at, is_fav
            FROM files
            WHERE user_id = ? AND is_fav = 1
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return cur.fetchall()

def set_fav(user_id: int, file_row_id: int, fav: int) -> None:
    with db_connect() as con:
        con.execute(
            "UPDATE files SET is_fav = ? WHERE user_id = ? AND id = ?",
            (fav, user_id, file_row_id),
        )
        con.commit()

def search_files(user_id: int, q: str, limit: int = 20) -> Iterable[sqlite3.Row]:
    q = (q or "").strip().lower()
    with db_connect() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, subject, file_type, caption, created_at, is_fav
            FROM files
            WHERE user_id = ?
              AND (LOWER(subject) LIKE ? OR LOWER(caption) LIKE ?)
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, f"%{q}%", f"%{q}%", limit),
        )
        return cur.fetchall()


# =========================
# Helpers
# =========================
def is_archive_mode(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.user_data.get("awaiting_file", False))

def human_date(iso: str) -> str:
    # ISO -> YYYY-MM-DD
    try:
        return iso.split("T")[0]
    except Exception:
        return iso

def pretty_row(r: sqlite3.Row) -> str:
    star = "⭐" if int(r["is_fav"]) == 1 else "☆"
    cap = (r["caption"] or "").strip()
    if len(cap) > 60:
        cap = cap[:60] + "…"
    return f"{star} #{r['id']} | {r['subject']} | {r['file_type']} | {cap or '—'} | {human_date(r['created_at'])}"

def inline_fav_kb(row_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("⭐ مفضلة", callback_data=f"fav:{row_id}:1"),
            InlineKeyboardButton("☆ إزالة", callback_data=f"fav:{row_id}:0"),
        ]]
    )

def detect_subject_from_caption(caption: str) -> Optional[str]:
    """
    ذكيّة بسيطة:
    - إذا المستخدم كتب: Grammar - Unit 1
    - أو: grammar: unit 1
    نلتقط المادة تلقائياً إذا تطابق اسمها.
    """
    if not caption:
        return None
    low = caption.strip().lower()
    for s in SUBJECTS:
        if low.startswith(s.lower() + " -") or low.startswith(s.lower() + ":") or low == s.lower():
            return s
    return None


# =========================
# Commands
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "يا هلا رأفت 👋\n"
        "جاهز نخلي أرشيفك مرتب و(بدون وجع راس) 📚\n\n"
        "• اختَر 📚 المواد ثم المادة\n"
        "• بعدها ابعث ملف/صورة/PDF… مع وصف بالكابشن (اختياري)\n\n"
        "تفضل 👇",
        reply_markup=MAIN_KB,
    )

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("search_mode", None)
    await update.message.reply_text("هذه القائمة الرئيسية ✅", reply_markup=MAIN_KB)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # يلغي أي وضع (بحث/أرشفة)
    context.user_data.pop("awaiting_file", None)
    context.user_data.pop("subject", None)
    context.user_data.pop("search_mode", None)
    await update.message.reply_text("تم الإلغاء ورجعناك للقائمة ✅", reply_markup=MAIN_KB)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subj = context.user_data.get("subject")
    awaiting = bool(context.user_data.get("awaiting_file"))
    searching = bool(context.user_data.get("search_mode"))
    await update.message.reply_text(
        "📌 الحالة الحالية:\n"
        f"• وضع الأرشفة: {'✅' if awaiting else '❌'}\n"
        f"• المادة المختارة: {subj or '—'}\n"
        f"• وضع البحث: {'✅' if searching else '❌'}\n"
        f"• قاعدة البيانات: {DB_PATH}\n"
        f"• الوقت: {TZ_NOTE}",
        reply_markup=MAIN_KB,
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ مساعدة سريعة:\n\n"
        "1) 📚 المواد → اختر مادة.\n"
        "2) ابعث ملف/صورة/PDF…\n"
        "3) اكتب وصف بالكابشن إذا تحب: (Unit 1 / Chapter 2 / امتحان…)\n\n"
        "💡 خدعة ذكية:\n"
        "إذا نسيت تختار مادة، اكتب بالكابشن بدايةً اسم المادة:\n"
        "Grammar - Unit 1\n\n"
        "أوامر مفيدة:\n"
        "/menu — القائمة\n"
        "/cancel — إلغاء الوضع الحالي\n"
        "/status — حالة البوت",
        reply_markup=MAIN_KB,
    )


# =========================
# Menu Text Handler
# =========================
async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text == "⬅️ رجوع":
        await cancel(update, context)
        return

    if text == "📚 المواد":
        context.user_data.pop("search_mode", None)
        await update.message.reply_text("اختر المادة 👇", reply_markup=subjects_keyboard())
        return

    if text.startswith("📘 "):
        subject = text.replace("📘 ", "").strip()
        context.user_data["subject"] = subject
        context.user_data["awaiting_file"] = True
        context.user_data.pop("search_mode", None)

        await update.message.reply_text(
            f"تمام ✅\n"
            f"المادة الحالية: {subject}\n\n"
            "الآن ابعث ملف/صورة/PDF…\n"
            "وإذا تحب، اكتب وصف بالكابشن مثل: Unit 1 / Midterm / Lecture 3",
            reply_markup=BACK_KB,
        )
        return

    if text == "🧾 آخر الملفات":
        context.user_data.pop("search_mode", None)
        rows = list_recent(update.effective_user.id, 10)
        if not rows:
            await update.message.reply_text(
                "لسّا ما عندك ملفات مؤرشفة.\nابدأ من 📚 المواد ✅",
                reply_markup=MAIN_KB,
            )
            return
        msg = "🧾 آخر الملفات:\n\n" + "\n".join(pretty_row(r) for r in rows)
        await update.message.reply_text(msg, reply_markup=MAIN_KB)
        return

    if text == "⭐ المفضلة":
        context.user_data.pop("search_mode", None)
        rows = list_favs(update.effective_user.id, 20)
        if not rows:
            await update.message.reply_text(
                "المفضلة فارغة ⭐\n"
                "بعد ما تؤرشف ملف، اضغط زر ⭐ مفضلة تحت رسالة الأرشفة.",
                reply_markup=MAIN_KB,
            )
            return
        msg = "⭐ المفضلة:\n\n" + "\n".join(pretty_row(r) for r in rows)
        await update.message.reply_text(msg, reply_markup=MAIN_KB)
        return

    if text == "🔎 بحث":
        context.user_data["search_mode"] = True
        context.user_data.pop("awaiting_file", None)  # حتى ما يختلط وضع البحث والأرشفة
        await update.message.reply_text(
            "اكتب كلمة البحث الآن 🔎\n"
            "مثال: unit 1 / midterm / grammar",
            reply_markup=MAIN_KB,
        )
        return

    if text == "🗂️ نسخة احتياطية":
        if os.path.exists(DB_PATH):
            try:
                with open(DB_PATH, "rb") as f:
                    await update.message.reply_document(
                        document=f,
                        filename=os.path.basename(DB_PATH),
                        caption="🗂️ نسخة احتياطية لقاعدة البيانات (SQLite).",
                        reply_markup=MAIN_KB,
                    )
            except Exception as e:
                log.exception("Backup send failed: %s", e)
                await update.message.reply_text("صار خطأ وأنا أرسل النسخة الاحتياطية 😅", reply_markup=MAIN_KB)
        else:
            await update.message.reply_text("ماكو قاعدة بيانات بعد. أرشف أول ملف حتى تنخلق ✅", reply_markup=MAIN_KB)
        return

    if text == "ℹ️ مساعدة":
        await help_cmd(update, context)
        return

    # Search mode
    if context.user_data.get("search_mode"):
        q = text
        rows = search_files(update.effective_user.id, q, 20)
        if not rows:
            await update.message.reply_text(f"ما لقيت شي عن: {q}\nجرّب كلمة ثانية.", reply_markup=MAIN_KB)
            return
        msg = f"🔎 نتائج البحث عن: {q}\n\n" + "\n".join(pretty_row(r) for r in rows)
        await update.message.reply_text(msg, reply_markup=MAIN_KB)
        return

    # Fallback
    await update.message.reply_text(
        "أنا وياك، بس خلّينا نمشي بالنظام 😄\n"
        "اختَر من الأزرار أو اكتب /help",
        reply_markup=MAIN_KB,
    )


# =========================
# File Handler
# =========================
async def handle_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    message_id = update.message.message_id
    caption = (update.message.caption or "").strip()

    # ذكي: إذا ما مختار مادة، نحاول نستنتجها من الكابشن
    subject = context.user_data.get("subject")
    if not subject:
        guessed = detect_subject_from_caption(caption)
        if guessed:
            subject = guessed
            context.user_data["subject"] = subject
            context.user_data["awaiting_file"] = True

    if not is_archive_mode(context) and not subject:
        await update.message.reply_text(
            "حتى أؤرشف صح ✅\n"
            "اختر مادة أولاً من 📚 المواد\n"
            "أو اكتب بالكابشن بدايةً اسم المادة مثل:\n"
            "Grammar - Unit 1",
            reply_markup=MAIN_KB,
        )
        return

    subject = subject or "Other"

    # Extract file
    file_type = None
    file_id = None
    file_unique_id = None

    if update.message.document:
        file_type = "document"
        file_id = update.message.document.file_id
        file_unique_id = update.message.document.file_unique_id
    elif update.message.photo:
        file_type = "photo"
        ph = update.message.photo[-1]
        file_id = ph.file_id
        file_unique_id = ph.file_unique_id
    elif update.message.video:
        file_type = "video"
        file_id = update.message.video.file_id
        file_unique_id = update.message.video.file_unique_id
    elif update.message.audio:
        file_type = "audio"
        file_id = update.message.audio.file_id
        file_unique_id = update.message.audio.file_unique_id
    elif update.message.voice:
        file_type = "voice"
        file_id = update.message.voice.file_id
        file_unique_id = update.message.voice.file_unique_id
    else:
        await update.message.reply_text("هذا النوع حالياً ما أدعمه. أرسل ملف/صورة/فيديو/صوت ✅", reply_markup=MAIN_KB)
        return

    try:
        row_id = insert_file(
            user_id=user_id,
            chat_id=chat_id,
            message_id=message_id,
            subject=subject,
            file_type=file_type,
            file_id=file_id,
            file_unique_id=file_unique_id,
            caption=caption,
        )
    except Exception as e:
        log.exception("DB insert failed: %s", e)
        await update.message.reply_text("صار خطأ وأنا أخزّن البيانات 😅\nجرّب مرة ثانية.", reply_markup=MAIN_KB)
        return

    await update.message.reply_text(
        "✅ تمّت الأرشفة بنجاح!\n\n"
        f"📚 المادة: {subject}\n"
        f"📦 النوع: {file_type}\n"
        f"🆔 رقم الأرشفة: #{row_id}\n"
        f"📝 الوصف: {caption or '—'}\n\n"
        "تحب أخليه ⭐ مفضلة؟",
        reply_markup=inline_fav_kb(row_id),
    )


# =========================
# Callback Handler
# =========================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = (q.data or "")
    if data.startswith("fav:"):
        _, rid, val = data.split(":")
        rid_i = int(rid)
        val_i = int(val)

        try:
            set_fav(update.effective_user.id, rid_i, val_i)
        except Exception as e:
            log.exception("Set fav failed: %s", e)
            await q.edit_message_text("صار خطأ وأنا أحدّث المفضلة 😅")
            return

        if val_i == 1:
            await q.edit_message_text((q.message.text or "") + "\n\n⭐ تمت الإضافة للمفضلة.")
        else:
            await q.edit_message_text((q.message.text or "") + "\n\n☆ تمت الإزالة من المفضلة.")


# =========================
# Error Handler
# =========================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error: %s", context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("صار خطأ غير متوقع 😅\nبس تمام… أنا أسجّل التفاصيل وأرجع.", reply_markup=MAIN_KB)
    except Exception:
        pass


# =========================
# Main
# =========================
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it as an environment variable.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("status", status))

    # Callbacks
    app.add_handler(CallbackQueryHandler(on_callback))

    # Text menus
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_text))

    # Files
    app.add_handler(
        MessageHandler(
            filters.Document.ALL
            | filters.PHOTO
            | filters.VIDEO
            | filters.AUDIO
            | filters.VOICE,
            handle_files,
        )
    )

    # Global error handler
    app.add_error_handler(on_error)

    log.info("Bot started. DB=%s", DB_PATH)
    app.run_polling()


if __name__ == "__main__":
    main()