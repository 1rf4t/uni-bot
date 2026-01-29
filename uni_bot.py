import os
import sqlite3
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================
# CONFIG (SAFE)
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "archive.db").strip()

if not BOT_TOKEN:
    raise SystemExit(
        "❌ BOT_TOKEN غير موجود.\n"
        "في Termux نفّذ:\n"
        "export BOT_TOKEN=\"YOUR_TOKEN\"\n"
        "ثم شغّل البوت."
    )

# =========================
# SUBJECTS (Your exact list)
# =========================
SUBJECTS = {
    "Poetry": "📜 Poetry",
    "Writing": "✍️ Writing",
    "Psychological Health": "🧠 Psychological Health",
    "Drama": "🎭 Drama",
    "Linguistics": "🧩 Linguistics",
    "Novel": "📖 Novel",
    "Pedagogy and Curriculum Innovation": "🏫 Pedagogy & Curriculum Innovation",
    "Grammar": "📐 Grammar",
    "Listening and speaking": "🎧 Listening & Speaking",
}

# =========================
# DATABASE
# =========================
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    subject TEXT NOT NULL,
    file_id TEXT NOT NULL,
    file_name TEXT,
    file_type TEXT NOT NULL,
    caption TEXT,
    created_at TEXT DEFAULT (datetime('now'))
)
""")
conn.commit()

# =========================
# UI
# =========================
MAIN_KB = ReplyKeyboardMarkup(
    [
        ["📚 المواد", "🗂 آخر الملفات"],
        ["⭐ المفضلة", "🔍 بحث"],
        ["📦 نسخة احتياطية", "ℹ️ مساعدة"],
    ],
    resize_keyboard=True,
)

def get_counts(user_id: int) -> dict:
    cur.execute("""
        SELECT subject, COUNT(*)
        FROM files
        WHERE user_id=?
        GROUP BY subject
    """, (user_id,))
    return {s: c for (s, c) in cur.fetchall()}

def subjects_menu(user_id: int) -> InlineKeyboardMarkup:
    counts = get_counts(user_id)
    rows = []
    for key, label in SUBJECTS.items():
        c = counts.get(key, 0)
        rows.append([InlineKeyboardButton(f"{label} ({c})", callback_data=f"subj:{key}")])
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)

def file_open_button(file_db_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📎 فتح", callback_data=f"open:{file_db_id}")],
        [InlineKeyboardButton("⬅️ رجوع للمواد", callback_data="back_subjects")]
    ])

# =========================
# START
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 أهلاً رأفت\n"
        "أنا بوت مكتبتك الذكية 📚\n\n"
        "✅ طريقتين للأرشفة:\n"
        "1) من الأزرار: 📚 المواد → اختر مادة → أرسل الملف\n"
        "2) الأسرع: اكتب اسم المادة وحده (مثل Linguistics) ثم أرسل الملفات\n\n"
        "⬇️ اختر من القائمة:",
        reply_markup=MAIN_KB
    )

# =========================
# HELPERS: detect files
# =========================
def extract_file(message):
    if message.document:
        return message.document.file_id, message.document.file_name or "document", "document"
    if message.photo:
        ph = message.photo[-1]
        return ph.file_id, "photo.jpg", "photo"
    if message.video:
        return message.video.file_id, "video.mp4", "video"
    if message.audio:
        return message.audio.file_id, "audio.mp3", "audio"
    if message.voice:
        return message.voice.file_id, "voice.ogg", "voice"
    return None, None, None

# =========================
# SAVE FILE
# =========================
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    subject = context.user_data.get("current_subject")
    if not subject:
        await update.message.reply_text(
            "❗ قبل إرسال الملف: اختَر مادة.\n"
            "مثال: اكتب Linguistics ثم أرسل الملفات.\n"
            "أو اضغط 📚 المواد.",
            reply_markup=MAIN_KB
        )
        return

    file_id, file_name, file_type = extract_file(update.message)
    if not file_id:
        await update.message.reply_text("❌ لم أفهم نوع الملف.")
        return

    caption = (update.message.caption or "").strip()

    cur.execute("""
        INSERT INTO files (user_id, subject, file_id, file_name, file_type, caption)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, subject, file_id, file_name, file_type, caption))
    conn.commit()

    await update.message.reply_text(f"✅ تم حفظ الملف داخل {SUBJECTS.get(subject, subject)}")

# =========================
# TEXT HANDLER
# =========================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id

    if text == "📚 المواد":
        await update.message.reply_text("📚 موادك (مع عدد الملفات):", reply_markup=subjects_menu(user_id))
        return

    # Quick-set subject by exact name
    if text in SUBJECTS:
        context.user_data["current_subject"] = text
        await update.message.reply_text(
            f"📌 تم تثبيت المادة: {SUBJECTS[text]}\n"
            "الآن أرسل ملفاتك مباشرة ✅",
            reply_markup=MAIN_KB
        )
        return

    await update.message.reply_text(
        "🧠 اكتب اسم مادة لتثبيتها (مثل: Linguistics)\n"
        "أو اضغط 📚 المواد.",
        reply_markup=MAIN_KB
    )

# =========================
# CALLBACKS
# =========================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id

    data = q.data

    if data == "back_main":
        await q.edit_message_text("✅ رجعناك للقائمة الرئيسية.")
        return

    if data == "back_subjects":
        await q.edit_message_text("📚 موادك (مع عدد الملفات):", reply_markup=subjects_menu(user_id))
        return

    if data.startswith("subj:"):
        subject = data.split("subj:", 1)[1]
        context.user_data["current_subject"] = subject

        cur.execute("""
            SELECT id, file_name, file_type, caption, created_at
            FROM files
            WHERE user_id=? AND subject=?
            ORDER BY id DESC
            LIMIT 30
        """, (user_id, subject))
        rows = cur.fetchall()

        if not rows:
            await q.edit_message_text(
                f"📭 لا توجد ملفات داخل {SUBJECTS.get(subject, subject)}.\n"
                "أرسل ملف الآن وسيُحفظ مباشرة ✅"
            )
            return

        # Show list as text + ask user to choose file number or press open
        text = f"{SUBJECTS.get(subject, subject)}\n\n"
        for r in rows:
            fid, name, ftype, cap, dt = r
            cap_short = (cap[:20] + "…") if cap and len(cap) > 20 else (cap or "—")
            text += f"⭐ #{fid} | {name} | {cap_short}\n"

        text += "\n⬇️ لفتح ملف: اضغط (📎 فتح) بعد ما ترسل رقم الملف.\n" \
                "مثال: اكتب 12"

        context.user_data["open_subject"] = subject
        await q.edit_message_text(text)
        return

    if data.startswith("open:"):
        file_db_id = int(data.split("open:", 1)[1])
        await send_file_by_db_id(q, user_id, file_db_id)
        return

async def send_file_by_db_id(q, user_id: int, file_db_id: int):
    cur.execute("""
        SELECT file_id, file_type, file_name, caption
        FROM files
        WHERE id=? AND user_id=?
    """, (file_db_id, user_id))
    row = cur.fetchone()

    if not row:
        await q.message.reply_text("❌ الملف غير موجود.")
        return

    file_id, ftype, fname, cap = row
    cap = cap or ""

    # send by type
    if ftype == "photo":
        await q.message.reply_photo(file_id, caption=cap)
    elif ftype == "video":
        await q.message.reply_video(file_id, caption=cap)
    elif ftype in ("audio", "voice"):
        await q.message.reply_audio(file_id, caption=cap)
    else:
        await q.message.reply_document(file_id, caption=cap)

# =========================
# OPEN BY NUMBER (BEST UX)
# =========================
async def open_by_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id

    if not text.isdigit():
        return

    file_db_id = int(text)
    # Create a one-click open button
    await update.message.reply_text(
        f"📌 ملف رقم #{file_db_id}\nاضغط لفتحه:",
        reply_markup=file_open_button(file_db_id)
    )

# =========================
# MAIN
# =========================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))

    # Number-to-open helper
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, open_by_number), group=0)
    # Normal text handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text), group=1)

    # Files
    app.add_handler(MessageHandler(
        filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE,
        handle_file
    ))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()