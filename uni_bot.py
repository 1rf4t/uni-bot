#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import shutil
from pathlib import Path
from datetime import datetime

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
# CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# ✅ قاعدة البيانات: لازم تكون بمسار دائم (يفضل /data/archive.db مع Railway Volume)
DB_PATH = os.getenv("DB_PATH", "archive.db").strip()

# ✅ مجلد النسخ الاحتياطية (يفضل /data/backups)
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups").strip()

# ✅ حتى يرسل النسخ تلقائياً لك
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# ✅ كل كم دقيقة يسوي نسخة تلقائية (60 = كل ساعة)
AUTO_BACKUP_MINUTES = int(os.getenv("AUTO_BACKUP_MINUTES", "60"))

# ✅ عدد النسخ التي نحتفظ بها فقط (حتى ما يمتلئ التخزين)
KEEP_BACKUPS = int(os.getenv("KEEP_BACKUPS", "10"))

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN غير مضبوط. استخدم متغير بيئة BOT_TOKEN")

# موادك الرسمية
SUBJECTS = [
    "Poetry",
    "Writing",
    "Psychological Health",
    "Drama",
    "Linguistics",
    "Novel",
    "Pedagogy and Curriculum Innovation",
    "Grammar",
    "Listening and speaking",
]

# رموز أنيقة لكل مادة
SUBJECT_EMOJI = {
    "Poetry": "🪶",
    "Writing": "✍️",
    "Psychological Health": "🧠",
    "Drama": "🎭",
    "Linguistics": "🧩",
    "Novel": "📖",
    "Pedagogy and Curriculum Innovation": "🏫",
    "Grammar": "📚",
    "Listening and speaking": "🎧",
}

# لوحة رئيسية (Reply keyboard)
MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📚 المواد"), KeyboardButton("🧾 آخر الملفات")],
        [KeyboardButton("⭐ المفضلة"), KeyboardButton("🔎 بحث")],
        [KeyboardButton("📦 نسخة احتياطية"), KeyboardButton("ℹ️ مساعدة")],
    ],
    resize_keyboard=True,
)

# =========================
# PATHS helpers
# =========================
def ensure_dirs():
    # إنشاء مجلد قاعدة البيانات إذا كان DB_PATH داخل مجلد
    db_file = Path(DB_PATH)
    if db_file.parent and str(db_file.parent) != ".":
        db_file.parent.mkdir(parents=True, exist_ok=True)

    # إنشاء مجلد النسخ الاحتياطية
    Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)

def make_backup_name() -> str:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return f"archive_backup_{ts}.db"

def list_backup_files():
    p = Path(BACKUP_DIR)
    files = sorted(p.glob("archive_backup_*.db"), key=lambda x: x.stat().st_mtime, reverse=True)
    return files

def cleanup_old_backups():
    if KEEP_BACKUPS <= 0:
        return
    files = list_backup_files()
    for f in files[KEEP_BACKUPS:]:
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass

# =========================
# DB
# =========================
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            file_type TEXT NOT NULL,
            tg_file_id TEXT NOT NULL,
            filename TEXT,
            caption TEXT,
            added_at TEXT NOT NULL,
            is_fav INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_files_user_subject ON files(user_id, subject);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_files_user_added ON files(user_id, added_at);")
    con.commit()
    con.close()

def add_file(user_id: int, subject: str, file_type: str, tg_file_id: str, filename: str | None, caption: str | None):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO files (user_id, subject, file_type, tg_file_id, filename, caption, added_at, is_fav)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (user_id, subject, file_type, tg_file_id, filename, caption, datetime.utcnow().isoformat(timespec="seconds")),
    )
    con.commit()
    new_id = cur.lastrowid
    con.close()
    return new_id

def count_by_subject(user_id: int):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT subject, COUNT(*) cnt FROM files WHERE user_id=? GROUP BY subject", (user_id,))
    rows = cur.fetchall()
    con.close()
    return [(r[0], r[1]) for r in rows]

def list_files_by_subject(user_id: int, subject: str, limit: int = 50):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, file_type, filename, caption, added_at, is_fav
        FROM files
        WHERE user_id=? AND subject=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, subject, limit),
    )
    rows = cur.fetchall()
    con.close()
    return rows

def get_file_by_id(user_id: int, file_id: int):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, file_type, tg_file_id, filename, caption, added_at, is_fav
        FROM files
        WHERE user_id=? AND id=?
        """,
        (user_id, file_id),
    )
    row = cur.fetchone()
    con.close()
    return row

def set_fav(user_id: int, file_id: int, fav: int):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE files SET is_fav=? WHERE user_id=? AND id=?", (fav, user_id, file_id))
    con.commit()
    con.close()

def delete_file(user_id: int, file_id: int):
    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM files WHERE user_id=? AND id=?", (user_id, file_id))
    con.commit()
    con.close()

def list_recent(user_id: int, limit: int = 10):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, file_type, filename, caption, added_at, is_fav
        FROM files
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    rows = cur.fetchall()
    con.close()
    return rows

def list_favorites(user_id: int, limit: int = 50):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, file_type, filename, caption, added_at, is_fav
        FROM files
        WHERE user_id=? AND is_fav=1
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    rows = cur.fetchall()
    con.close()
    return rows

def search_files(user_id: int, q: str, limit: int = 30):
    like = f"%{q}%"
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, file_type, filename, caption, added_at, is_fav
        FROM files
        WHERE user_id=?
          AND (filename LIKE ? OR caption LIKE ?)
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, like, like, limit),
    )
    rows = cur.fetchall()
    con.close()
    return rows

# =========================
# BACKUP (SAFE)
# =========================
def create_backup_file() -> Path:
    """
    ✅ نسخ آمن للـ SQLite: نعمل copy للملف الحالي إلى BACKUP_DIR
    """
    backup_name = make_backup_name()
    backup_path = Path(BACKUP_DIR) / backup_name

    # إذا DB غير موجود، نمنع الخطأ
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(f"DB not found: {DB_PATH}")

    shutil.copy2(DB_PATH, backup_path)
    cleanup_old_backups()
    return backup_path

async def auto_backup_job(context: ContextTypes.DEFAULT_TYPE):
    if OWNER_ID == 0:
        return
    try:
        backup_path = create_backup_file()
        with open(backup_path, "rb") as f:
            await context.bot.send_document(
                chat_id=OWNER_ID,
                document=f,
                filename=backup_path.name,
                caption="✅ نسخة احتياطية تلقائية (Auto Backup)",
            )
    except Exception as e:
        try:
            await context.bot.send_message(chat_id=OWNER_ID, text=f"❌ فشل النسخ الاحتياطي التلقائي: {e}")
        except Exception:
            pass

# =========================
# UI Helpers
# =========================
def subjects_keyboard(user_id: int):
    counts = dict(count_by_subject(user_id))
    items = []
    for s in SUBJECTS:
        emoji = SUBJECT_EMOJI.get(s, "📘")
        cnt = counts.get(s, 0)
        items.append(InlineKeyboardButton(f"{emoji} {s} ({cnt})", callback_data=f"subj:{s}"))

    buttons = []
    for i in range(0, len(items), 2):
        buttons.append(items[i:i + 2])

    buttons.append([InlineKeyboardButton("↩️ رجوع", callback_data="back:home")])
    return InlineKeyboardMarkup(buttons)

def files_keyboard(subject: str, rows):
    items = []
    for r in rows:
        fid = int(r["id"])
        name = (r["filename"] or "").strip()
        if not name:
            name = r["caption"] or f"file_{fid}"

        clean = name.replace("\n", " ").strip()
        if len(clean) > 26:
            clean = clean[:23] + "…"

        items.append(InlineKeyboardButton(f"📄 {clean}", callback_data=f"open:{fid}"))

    buttons = []
    for i in range(0, len(items), 2):
        buttons.append(items[i:i + 2])

    buttons.append([InlineKeyboardButton("↩️ رجوع للمواد", callback_data="back:subjects")])
    return InlineKeyboardMarkup(buttons)

def manage_keyboard(file_id: int, is_fav: int):
    fav_btn = InlineKeyboardButton("⭐ إزالة من المفضلة" if is_fav else "⭐ إضافة للمفضلة", callback_data=f"fav:{file_id}")
    del_btn = InlineKeyboardButton("🗑️ حذف", callback_data=f"del:{file_id}")
    back_btn = InlineKeyboardButton("↩️ رجوع للمواد", callback_data="back:subjects")
    return InlineKeyboardMarkup([[fav_btn, del_btn], [back_btn]])

def pretty_file_line(r):
    subj = r["subject"]
    emoji = SUBJECT_EMOJI.get(subj, "📘")
    name = (r["filename"] or "").strip() or (r["caption"] or f"file_{r['id']}")
    fav = "⭐" if r["is_fav"] else ""
    return f"{fav}{emoji} <b>{subj}</b> | #{r['id']} | {name} | {r['added_at']}"

# =========================
# Bot Handlers
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("fixed_subject", None)
    context.user_data.pop("search_mode", None)

    text = (
        "يا هلا رأفت 👋\n"
        "أنا بوت الأرشفة الذكي 📚\n\n"
        "✅ تقدر تأرشف بطريقتين:\n"
        "1) من الأزرار: 📚 المواد → اختر مادة → ارسل الملف.\n"
        "2) الأسرع: اكتب اسم المادة لوحده (مثلاً: Linguistics) ثم ارسل/حوّل ملفات بعدها.\n"
        "   (يبقى ثابت 10 دقائق)\n\n"
        "اضغط من القائمة 👇"
    )
    await update.message.reply_text(text, reply_markup=MAIN_KB)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ مساعدة:\n"
        "• 📚 المواد: عرض موادك وعدد الملفات داخل كل مادة.\n"
        "• لتحفظ بسرعة: اكتب اسم المادة فقط ثم ابعث ملفات.\n"
        "• لفتح ملف: ادخل المادة واضغط اسم الملف من القائمة.\n"
        "• ⭐ المفضلة: ملفاتك المميزة.\n"
        "• 🧾 آخر الملفات: آخر ما حفظته.\n"
        "• 📦 نسخة احتياطية: Backup يدوي يوصلك.\n",
        reply_markup=MAIN_KB,
    )

# (اختياري) حتى تجيب رقم ايديك بسهولة
async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🆔 Your Telegram ID: {update.effective_user.id}", reply_markup=MAIN_KB)

def normalize_subject(text: str):
    t = text.strip()
    for s in SUBJECTS:
        if t.lower() == s.lower():
            return s
    return None

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # بحث
    if context.user_data.get("search_mode"):
        q = text
        context.user_data["search_mode"] = False
        rows = search_files(update.effective_user.id, q)
        if not rows:
            await update.message.reply_text("🔎 ما لقيت نتائج.", reply_markup=MAIN_KB)
            return
        msg = "🔎 نتائج البحث:\n\n" + "\n".join(pretty_file_line(r) for r in rows)
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)
        return

    if text == "📚 المواد":
        kb = subjects_keyboard(update.effective_user.id)
        await update.message.reply_text("📚 موادك (مع عدد الملفات):\n👇 اضغط مادة", reply_markup=kb)
        return

    if text == "🧾 آخر الملفات":
        rows = list_recent(update.effective_user.id, 12)
        if not rows:
            await update.message.reply_text("✅ ما عندك أرشيف بعد. أرشف أول ملف.", reply_markup=MAIN_KB)
            return
        msg = "🧾 آخر الملفات:\n\n" + "\n".join(pretty_file_line(r) for r in rows)
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)
        return

    if text == "⭐ المفضلة":
        rows = list_favorites(update.effective_user.id, 50)
        if not rows:
            await update.message.reply_text("⭐ ما عندك ملفات مفضلة بعد.", reply_markup=MAIN_KB)
            return
        msg = "⭐ المفضلة:\n\n" + "\n".join(pretty_file_line(r) for r in rows)
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)
        return

    if text == "🔎 بحث":
        context.user_data["search_mode"] = True
        await update.message.reply_text("🔎 اكتب كلمة من اسم الملف أو الوصف:", reply_markup=MAIN_KB)
        return

    # ✅ نسخة احتياطية يدوية صحيحة (تعمل نسخة فعلية وترسلها)
    if text == "📦 نسخة احتياطية":
        try:
            backup_path = create_backup_file()
            with open(backup_path, "rb") as f:
                await update.message.reply_document(
                    document=f,
                    filename=backup_path.name,
                    caption="📦 نسخة احتياطية من قاعدة البيانات",
                )
        except Exception as e:
            await update.message.reply_text(f"❌ فشل النسخ الاحتياطي: {e}", reply_markup=MAIN_KB)
        return

    if text == "ℹ️ مساعدة":
        await help_cmd(update, context)
        return

    subj = normalize_subject(text)
    if subj:
        context.user_data["fixed_subject"] = subj
        context.user_data["fixed_until"] = datetime.utcnow().timestamp() + (10 * 60)
        emoji = SUBJECT_EMOJI.get(subj, "📘")
        await update.message.reply_text(
            f"✅ ثبتت المادة مؤقتاً: {emoji} <b>{subj}</b>\n"
            "الآن ارسل/حوّل ملفات... (صالح 10 دقائق)\n"
            "إذا تريد وصف: اكتب Caption مع الملف.",
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KB,
        )
        return

    await update.message.reply_text("ما فهمت 😅\nاضغط من القائمة: 📚 المواد أو اكتب اسم المادة لتثبيتها.", reply_markup=MAIN_KB)

def get_fixed_subject(context: ContextTypes.DEFAULT_TYPE):
    subj = context.user_data.get("fixed_subject")
    until = context.user_data.get("fixed_until", 0)
    if subj and datetime.utcnow().timestamp() <= until:
        return subj
    context.user_data.pop("fixed_subject", None)
    context.user_data.pop("fixed_until", None)
    return None

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    subj = get_fixed_subject(context)
    if not subj:
        kb = subjects_keyboard(user_id)
        await update.message.reply_text("👇 اختر مادة أولاً لحفظ الملف:", reply_markup=kb)
        return

    msg = update.message
    caption = (msg.caption or "").strip() or None

    file_type = None
    tg_file_id = None
    filename = None

    if msg.document:
        file_type = "document"
        tg_file_id = msg.document.file_id
        filename = msg.document.file_name
    elif msg.photo:
        file_type = "photo"
        tg_file_id = msg.photo[-1].file_id
        filename = "photo.jpg"
    elif msg.video:
        file_type = "video"
        tg_file_id = msg.video.file_id
        filename = "video.mp4"
    elif msg.audio:
        file_type = "audio"
        tg_file_id = msg.audio.file_id
        filename = msg.audio.file_name or "audio.mp3"
    elif msg.voice:
        file_type = "voice"
        tg_file_id = msg.voice.file_id
        filename = "voice.ogg"
    else:
        await update.message.reply_text("⚠️ هذا النوع غير مدعوم حالياً.", reply_markup=MAIN_KB)
        return

    new_id = add_file(user_id, subj, file_type, tg_file_id, filename, caption)

    emoji = SUBJECT_EMOJI.get(subj, "📘")
    await update.message.reply_text(
        f"✅ تمت الأرشفة بنجاح!\n"
        f"المادة: {emoji} {subj}\n"
        f"رقم: #{new_id}\n"
        f"الوصف: {caption or '—'}\n"
        f"⏳ تثبيت المادة ما زال فعالاً.",
        reply_markup=MAIN_KB,
    )

# =========================
# Callbacks
# =========================
async def cb_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    subject = query.data.split(":", 1)[1]
    user_id = query.from_user.id

    rows = list_files_by_subject(user_id, subject, 50)
    emoji = SUBJECT_EMOJI.get(subject, "📘")

    if not rows:
        await query.message.reply_text(f"✅ {emoji} {subject}\nماكو ملفات بعد. أرشف أول ملف.", reply_markup=MAIN_KB)
        return

    kb = files_keyboard(subject, rows)
    await query.message.reply_text(f"{emoji} <b>{subject}</b> — اختر ملف لفتحه:", parse_mode=ParseMode.HTML, reply_markup=kb)

async def cb_open_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    file_id = int(query.data.split(":", 1)[1])

    row = get_file_by_id(user_id, file_id)
    if not row:
        await query.message.reply_text("❌ الملف غير موجود أو تم حذفه.")
        return

    filename = (row["filename"] or "").strip() or f"file_{file_id}"
    caption = row["caption"] or None
    is_fav = int(row["is_fav"])

    if row["file_type"] == "document":
        await query.message.reply_document(document=row["tg_file_id"], caption=caption or filename)
    elif row["file_type"] == "photo":
        await query.message.reply_photo(photo=row["tg_file_id"], caption=caption or filename)
    elif row["file_type"] == "video":
        await query.message.reply_video(video=row["tg_file_id"], caption=caption or filename)
    elif row["file_type"] == "audio":
        await query.message.reply_audio(audio=row["tg_file_id"], caption=caption or filename)
    elif row["file_type"] == "voice":
        await query.message.reply_voice(voice=row["tg_file_id"], caption=caption or filename)
    else:
        await query.message.reply_text("⚠️ نوع الملف غير مدعوم.")
        return

    subj = row["subject"]
    emoji = SUBJECT_EMOJI.get(subj, "📘")
    await query.message.reply_text(
        f"⚙️ <b>إدارة الملف</b>:\n{emoji} {subj} | #{file_id}\n📄 {filename}",
        parse_mode=ParseMode.HTML,
        reply_markup=manage_keyboard(file_id, is_fav),
    )

async def cb_fav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    file_id = int(query.data.split(":", 1)[1])
    row = get_file_by_id(user_id, file_id)
    if not row:
        await query.message.reply_text("❌ الملف غير موجود.")
        return

    new_fav = 0 if int(row["is_fav"]) else 1
    set_fav(user_id, file_id, new_fav)

    await query.message.reply_text("⭐ تم تحديث المفضلة.")
    await query.message.reply_text("⚙️ إدارة الملف:", reply_markup=manage_keyboard(file_id, new_fav))

async def cb_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    file_id = int(query.data.split(":", 1)[1])

    row = get_file_by_id(user_id, file_id)
    if not row:
        await query.message.reply_text("❌ الملف غير موجود.")
        return

    delete_file(user_id, file_id)
    await query.message.reply_text("🗑️ تم حذف الملف من أرشيفك.")

async def cb_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    where = query.data.split(":", 1)[1]
    if where == "subjects":
        kb = subjects_keyboard(query.from_user.id)
        await query.message.reply_text("📚 موادك (مع عدد الملفات):\n👇 اضغط مادة", reply_markup=kb)
    else:
        await query.message.reply_text("رجعناك للقائمة الرئيسية ✅", reply_markup=MAIN_KB)

# =========================
# MAIN
# =========================
def main():
    ensure_dirs()
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # ✅ جدولة النسخ الاحتياطي التلقائي
    if AUTO_BACKUP_MINUTES > 0:
        app.job_queue.run_repeating(
            auto_backup_job,
            interval=AUTO_BACKUP_MINUTES * 60,
            first=60,
        )

    # commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myid", myid))

    # callbacks
    app.add_handler(CallbackQueryHandler(cb_subject, pattern=r"^subj:"))
    app.add_handler(CallbackQueryHandler(cb_open_file, pattern=r"^open:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_fav, pattern=r"^fav:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_del, pattern=r"^del:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_back, pattern=r"^back:"))

    # messages
    app.add_handler(
        MessageHandler(
            filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE,
            handle_file,
        )
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Bot is running...")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()