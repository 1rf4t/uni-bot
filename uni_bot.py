import os
import sqlite3
from datetime import datetime
from typing import Optional, List, Tuple, Dict

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

# =======================
# Config
# =======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "archive.db").strip()

DEFAULT_PAGE_SIZE = 10
MAX_LIST_LIMIT = 100

SUBJECTS = [
    "Grammar", "Phonetics", "Poetry", "Drama", "Novel",
    "Listening", "Writing", "Linguistics", "ELT", "Other"
]

# =======================
# Keyboards (UI)
# =======================
MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📚 المواد"), KeyboardButton("🧾 آخر الملفات")],
        [KeyboardButton("⭐ المفضلة"), KeyboardButton("🔎 بحث")],
        [KeyboardButton("🗂️ نسخة احتياطية"), KeyboardButton("ℹ️ مساعدة")],
    ],
    resize_keyboard=True,
)

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

def subject_actions_keyboard(subject: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📤 أرشفة ملف جديد"), KeyboardButton("📂 عرض ملفات المادة")],
            [KeyboardButton("⬅️ رجوع")],
        ],
        resize_keyboard=True,
    )

def inline_fav_keyboard(row_id: int, is_fav: int) -> InlineKeyboardMarkup:
    # زر واحد يتبدّل حسب الحالة
    if is_fav:
        btn = InlineKeyboardButton("⭐ موجودة بالمفضلة (إزالة)", callback_data=f"fav:{row_id}:0")
    else:
        btn = InlineKeyboardButton("☆ إضافة للمفضلة", callback_data=f"fav:{row_id}:1")
    return InlineKeyboardMarkup([[btn]])

def inline_subject_files_nav(subject: str, offset: int, page_size: int, has_more: bool) -> InlineKeyboardMarkup:
    buttons = []
    if offset > 0:
        prev_offset = max(0, offset - page_size)
        buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"subnav:{subject}:{prev_offset}:{page_size}"))
    if has_more:
        next_offset = offset + page_size
        buttons.append(InlineKeyboardButton("التالي ➡️", callback_data=f"subnav:{subject}:{next_offset}:{page_size}"))
    if not buttons:
        # إذا صفحة واحدة، لا نعرض شي
        return InlineKeyboardMarkup([[]])
    return InlineKeyboardMarkup([buttons])

# =======================
# Database
# =======================
def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
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
    con = db()
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
            datetime.utcnow().isoformat(),
        ),
    )
    con.commit()
    new_id = cur.lastrowid
    con.close()
    return new_id

def list_recent(user_id: int, limit: int = 10):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, file_type, caption, created_at, is_fav
        FROM files
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, min(limit, MAX_LIST_LIMIT)),
    )
    rows = cur.fetchall()
    con.close()
    return rows

def list_favs(user_id: int, limit: int = 20):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, file_type, caption, created_at, is_fav
        FROM files
        WHERE user_id = ? AND is_fav = 1
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, min(limit, MAX_LIST_LIMIT)),
    )
    rows = cur.fetchall()
    con.close()
    return rows

def set_fav(user_id: int, file_row_id: int, fav: int):
    con = db()
    cur = con.cursor()
    cur.execute(
        "UPDATE files SET is_fav = ? WHERE user_id = ? AND id = ?",
        (fav, user_id, file_row_id),
    )
    con.commit()
    con.close()

def search_files(user_id: int, q: str, limit: int = 20):
    q = (q or "").strip()
    con = db()
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
        (user_id, f"%{q.lower()}%", f"%{q.lower()}%", min(limit, MAX_LIST_LIMIT)),
    )
    rows = cur.fetchall()
    con.close()
    return rows

def list_by_subject(user_id: int, subject: str, limit: int, offset: int):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, file_type, caption, created_at, is_fav
        FROM files
        WHERE user_id = ? AND subject = ?
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, subject, min(limit, MAX_LIST_LIMIT), max(0, offset)),
    )
    rows = cur.fetchall()
    con.close()
    return rows

def get_file_record(user_id: int, row_id: int):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, user_id, chat_id, message_id, subject, file_type, caption, created_at, is_fav
        FROM files
        WHERE user_id = ? AND id = ?
        """,
        (user_id, row_id),
    )
    row = cur.fetchone()
    con.close()
    return row

# =======================
# Helpers
# =======================
def safe_cap(s: str, n: int = 50) -> str:
    s = (s or "").strip()
    if not s:
        return "بدون وصف"
    return (s[:n] + "…") if len(s) > n else s

def pretty_row_short(row) -> str:
    # (id, subject, file_type, caption, created_at, is_fav)
    rid, subj, ftype, cap, created, is_fav = row
    star = "⭐" if is_fav else "☆"
    date = (created or "").split("T")[0] if created else ""
    return f"{star} #{rid} • {safe_cap(cap, 38)} ({ftype}, {date})"

def set_mode(context: ContextTypes.DEFAULT_TYPE, mode: str):
    context.user_data["mode"] = mode

def get_mode(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("mode", "idle")

def current_subject(context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    return context.user_data.get("subject")

# =======================
# Commands
# =======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    set_mode(context, "idle")
    await update.message.reply_text(
        "يا هلا رأفت 👋\n"
        "أنا *Raafat Archive Bot* — خلّي ملفاتك منظمة مثل رفوف مكتبة 📚\n\n"
        "ابدأ من زر: *📚 المواد* ثم اختر المادة.\n"
        "بعدها إمّا تؤرشف ملف جديد أو تعرض ملفات المادة.",
        reply_markup=MAIN_KB,
        parse_mode=ParseMode.MARKDOWN,
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *طريقة الاستخدام السريعة:*\n"
        "1) اضغط *📚 المواد* واختر مادة.\n"
        "2) اختر:\n"
        "   • *📤 أرشفة ملف جديد* ثم أرسل ملف/صورة/PDF…\n"
        "   • *📂 عرض ملفات المادة* لعرض قائمة ملفاتها.\n\n"
        "🧾 *آخر الملفات* يعرض آخر 10 ملفات.\n"
        "⭐ *المفضلة* تعرض الملفات المعلّمة بنجمة.\n"
        "🔎 *بحث* يفتّش في (اسم المادة + الوصف/الكابشن).\n\n"
        "ملاحظة: أنا أخزن بيانات الملف في SQLite، والملف نفسه يبقى على تيليغرام.",
        reply_markup=MAIN_KB,
        parse_mode=ParseMode.MARKDOWN,
    )

# =======================
# Subject files list (inline)
# =======================
async def send_subject_files_list(
    update_or_query,
    context: ContextTypes.DEFAULT_TYPE,
    subject: str,
    offset: int = 0,
    page_size: int = DEFAULT_PAGE_SIZE
):
    user_id = update_or_query.effective_user.id
    rows = list_by_subject(user_id, subject, page_size, offset)

    # هل يوجد المزيد؟
    # نتحقق بجلب عنصر إضافي صغير (حل بسيط)
    more_check = list_by_subject(user_id, subject, 1, offset + page_size)
    has_more = len(more_check) > 0

    if not rows and offset == 0:
        text = (
            f"📘 *{subject}*\n"
            "ماكو ملفات بعد بهاي المادة.\n\n"
            "تريد تبدأ؟ اختر *📤 أرشفة ملف جديد* وارسِل ملفك."
        )
        # نرسل على حسب نوع المصدر (رسالة أو كولباك)
        if hasattr(update_or_query, "message") and update_or_query.message:
            await update_or_query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        else:
            await update_or_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        return

    lines = [f"📘 *{subject}* — ملفاتك (صفحة {offset//page_size + 1})\n"]
    for r in rows:
        lines.append(pretty_row_short(r))

    lines.append("\n✳️ *لإرسال ملف:* اكتب رقم الأرشفة مثل: `#12` أو فقط `12`.")
    text = "\n".join(lines)

    nav_kb = inline_subject_files_nav(subject, offset, page_size, has_more)

    if hasattr(update_or_query, "message") and update_or_query.message:
        await update_or_query.message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=nav_kb if nav_kb.inline_keyboard and nav_kb.inline_keyboard[0] else None,
        )
    else:
        await update_or_query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=nav_kb if nav_kb.inline_keyboard and nav_kb.inline_keyboard[0] else None,
        )

# =======================
# Menu Text Handler
# =======================
async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    mode = get_mode(context)

    # ===== رجوع =====
    if text == "⬅️ رجوع":
        context.user_data.pop("subject", None)
        set_mode(context, "idle")
        await update.message.reply_text("رجعناك للقائمة الرئيسية ✅", reply_markup=MAIN_KB)
        return

    # ===== Main menu =====
    if text == "📚 المواد":
        set_mode(context, "choosing_subject")
        await update.message.reply_text("اختر المادة 👇", reply_markup=subjects_keyboard())
        return

    if text == "ℹ️ مساعدة":
        await help_cmd(update, context)
        return

    if text == "🧾 آخر الملفات":
        set_mode(context, "idle")
        rows = list_recent(update.effective_user.id, 10)
        if not rows:
            await update.message.reply_text("لسّا ما عندك ملفات مؤرشفة. ابدأ من 📚 المواد ✅", reply_markup=MAIN_KB)
            return
        msg = "🧾 *آخر الملفات:*\n\n" + "\n".join(pretty_row_short(r) for r in rows)
        msg += "\n\n✳️ لإرسال ملف: اكتب رقم الأرشفة مثل `12`."
        await update.message.reply_text(msg, reply_markup=MAIN_KB, parse_mode=ParseMode.MARKDOWN)
        return

    if text == "⭐ المفضلة":
        set_mode(context, "idle")
        rows = list_favs(update.effective_user.id, 20)
        if not rows:
            await update.message.reply_text("المفضلة فارغة ⭐\nبعد ما تؤرشف ملف، اضغط ☆ إضافة للمفضلة.", reply_markup=MAIN_KB)
            return
        msg = "⭐ *المفضلة:*\n\n" + "\n".join(pretty_row_short(r) for r in rows)
        msg += "\n\n✳️ لإرسال ملف: اكتب رقم الأرشفة مثل `12`."
        await update.message.reply_text(msg, reply_markup=MAIN_KB, parse_mode=ParseMode.MARKDOWN)
        return

    if text == "🔎 بحث":
        set_mode(context, "search_waiting")
        await update.message.reply_text("اكتب كلمة البحث الآن 🔎\nمثال: `unit 1` أو `grammar`", reply_markup=MAIN_KB, parse_mode=ParseMode.MARKDOWN)
        return

    if text == "🗂️ نسخة احتياطية":
        set_mode(context, "idle")
        if os.path.exists(DB_PATH):
            await update.message.reply_document(
                document=open(DB_PATH, "rb"),
                filename=DB_PATH,
                caption="🗂️ نسخة احتياطية لقاعدة البيانات (SQLite).",
                reply_markup=MAIN_KB,
            )
        else:
            await update.message.reply_text("ماكو قاعدة بيانات بعد. أرشف أول ملف حتى تنخلق ✅", reply_markup=MAIN_KB)
        return

    # ===== Choosing subject =====
    if text.startswith("📘 "):
        subject = text.replace("📘 ", "").strip()
        if subject not in SUBJECTS:
            subject = "Other"
        context.user_data["subject"] = subject
        set_mode(context, "subject_home")
        await update.message.reply_text(
            f"📘 *{subject}*\nشنو تريد تسوي؟",
            reply_markup=subject_actions_keyboard(subject),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # ===== Subject actions =====
    if mode == "subject_home":
        subject = current_subject(context) or "Other"

        if text == "📤 أرشفة ملف جديد":
            set_mode(context, "awaiting_file")
            await update.message.reply_text(
                f"تمام ✅\n"
                f"المادة الحالية: *{subject}*\n\n"
                "ارسل الآن ملف/صورة/PDF…\n"
                "✍️ الأفضل تكتب وصف بالكابشن مثل:\n"
                "`Unit 1 - Handout` أو `محاضرة 2`",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("⬅️ رجوع")]], resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if text == "📂 عرض ملفات المادة":
            # نخلي وضع "عرض مادة" حتى الرقم يرسل الملف مباشرة
            set_mode(context, "subject_browsing")
            context.user_data["browse_subject"] = subject
            context.user_data["browse_offset"] = 0
            context.user_data["browse_page_size"] = DEFAULT_PAGE_SIZE
            await send_subject_files_list(update, context, subject, 0, DEFAULT_PAGE_SIZE)
            return

        # أي شي غير معروف داخل subject_home
        await update.message.reply_text("اختَر أحد الخيارين من الأزرار ✅", reply_markup=subject_actions_keyboard(subject))
        return

    # ===== Search mode =====
    if mode == "search_waiting":
        q = text
        rows = search_files(update.effective_user.id, q, 20)
        set_mode(context, "idle")
        if not rows:
            await update.message.reply_text(f"ما لقيت شي عن: *{q}*", reply_markup=MAIN_KB, parse_mode=ParseMode.MARKDOWN)
            return
        msg = f"🔎 *نتائج البحث عن:* `{q}`\n\n" + "\n".join(pretty_row_short(r) for r in rows)
        msg += "\n\n✳️ لإرسال ملف: اكتب رقم الأرشفة مثل `12`."
        await update.message.reply_text(msg, reply_markup=MAIN_KB, parse_mode=ParseMode.MARKDOWN)
        return

    # ===== If user typed an ID number to fetch file =====
    # نسمح بها بأي وقت تقريباً
    cleaned = text.replace("#", "").strip()
    if cleaned.isdigit():
        rid = int(cleaned)
        rec = get_file_record(update.effective_user.id, rid)
        if not rec:
            await update.message.reply_text("ما لقيت هذا الرقم عندك 🤷‍♂️\nتأكد من رقم الأرشفة.", reply_markup=MAIN_KB)
            return

        _, _, chat_id, message_id, subject, ftype, cap, created, is_fav = rec
        # نعيد إرسال الملف الأصلي
        await context.bot.copy_message(
            chat_id=update.effective_chat.id,
            from_chat_id=chat_id,
            message_id=message_id,
        )

        await update.message.reply_text(
            f"✅ هذا ملفك\n"
            f"📘 المادة: {subject}\n"
            f"🧾 الوصف: {cap or 'بدون وصف'}\n"
            f"🆔 رقم الأرشفة: #{rid}",
            reply_markup=inline_fav_keyboard(rid, is_fav),
        )
        return

    # ===== fallback =====
    await update.message.reply_text(
        "أريد أمشي وياك بنظام 😄\nاختَر من الأزرار أو اكتب رقم ملف (#12) حتى أرسله لك.",
        reply_markup=MAIN_KB,
    )

# =======================
# File Handler (archiving)
# =======================
async def handle_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_mode(context) != "awaiting_file":
        await update.message.reply_text(
            "حتى أؤرشف صح ✅\n"
            "اختَر مادة أولاً: 📚 المواد → ثم 📤 أرشفة ملف جديد",
            reply_markup=MAIN_KB,
        )
        return

    subject = current_subject(context) or "Other"
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    message_id = update.message.message_id
    caption = (update.message.caption or "").strip()

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
        await update.message.reply_text("هذا النوع حالياً ما أدعمه. أرسل PDF/صورة/فيديو/صوت ✅")
        return

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

    # بعد الأرشفة نرجعك لقائمة المادة
    set_mode(context, "subject_home")
    await update.message.reply_text(
        "✅ *تمت الأرشفة بنجاح!*\n"
        f"📘 المادة: *{subject}*\n"
        f"📦 النوع: `{file_type}`\n"
        f"🆔 رقم الأرشفة: `#{row_id}`\n"
        f"🧾 الوصف: {caption or 'بدون وصف'}\n\n"
        "تريد تعرض ملفات المادة؟ اضغط *📂 عرض ملفات المادة*",
        reply_markup=subject_actions_keyboard(subject),
        parse_mode=ParseMode.MARKDOWN,
    )

# =======================
# Callback Handler (fav + subject list nav)
# =======================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = (q.data or "")
    await q.answer()

    # fav toggle
    if data.startswith("fav:"):
        _, rid, val = data.split(":")
        rid_i = int(rid)
        val_i = int(val)
        set_fav(update.effective_user.id, rid_i, val_i)

        # نقرأ السجل حتى نعرف الحالة الجديدة
        rec = get_file_record(update.effective_user.id, rid_i)
        is_fav = rec[-1] if rec else val_i

        # نحدّث أزرار الرسالة (بدون تغيير النص كثير)
        try:
            await q.edit_message_reply_markup(reply_markup=inline_fav_keyboard(rid_i, is_fav))
        except Exception:
            pass
        return

    # subject files navigation
    if data.startswith("subnav:"):
        _, subject, offset, page_size = data.split(":")
        subject = subject.strip()
        offset = int(offset)
        page_size = int(page_size)

        # نعرض نفس الرسالة بصفحة جديدة
        await send_subject_files_list(q, context, subject, offset, page_size)
        return

# =======================
# Main
# =======================
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it as an environment variable (BOT_TOKEN).")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))

    # callbacks
    app.add_handler(CallbackQueryHandler(on_callback))

    # text menus
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_text))

    # file uploads
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

    print("Bot is running...")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()