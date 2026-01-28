import os
import re
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict, Any

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
DB_PATH = os.getenv("DB_PATH", "archive.db")
TZ_LABEL = os.getenv("TZ_LABEL", "UTC")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("raafat-archive-bot")

# =========================
# Subjects (اسماء المواد)
# =========================
SUBJECTS = [
    "Grammar", "Phonetics", "Poetry", "Drama", "Novel",
    "Listening", "Writing", "Linguistics", "ELT", "Other"
]

# Alias: لو المستخدم كتب اسم قريب/مختصر
SUBJECT_ALIASES = {
    "ling": "Linguistics",
    "linguistic": "Linguistics",
    "phon": "Phonetics",
    "gram": "Grammar",
    "lit": "Poetry",
    "elt": "ELT",
    "listen": "Listening",
    "write": "Writing",
}

SUBJECT_SET = {s.lower(): s for s in SUBJECTS}

# =========================
# Keyboards
# =========================
MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📚 المواد"), KeyboardButton("🗂️ نسخة احتياطية")],
        [KeyboardButton("🧾 آخر الملفات"), KeyboardButton("⭐ المفضلة")],
        [KeyboardButton("🔎 بحث"), KeyboardButton("ℹ️ مساعدة")],
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

BACK_KB = ReplyKeyboardMarkup([[KeyboardButton("⬅️ رجوع")]], resize_keyboard=True)

# =========================
# DB
# =========================
def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL;")
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

            file_name TEXT,
            mime_type TEXT,
            file_size INTEGER,

            caption TEXT,
            created_at TEXT NOT NULL,
            is_fav INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    con.commit()
    return con

def insert_file(data: Dict[str, Any]) -> int:
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO files (
            user_id, chat_id, message_id,
            subject, file_type,
            file_id, file_unique_id,
            file_name, mime_type, file_size,
            caption, created_at, is_fav
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data["user_id"], data["chat_id"], data["message_id"],
            data["subject"], data["file_type"],
            data["file_id"], data["file_unique_id"],
            data.get("file_name", ""),
            data.get("mime_type", ""),
            int(data.get("file_size") or 0),
            data.get("caption", ""),
            data["created_at"],
            int(data.get("is_fav") or 0),
        ),
    )
    con.commit()
    new_id = cur.lastrowid
    con.close()
    return new_id

def set_fav(user_id: int, row_id: int, fav: int) -> None:
    con = db()
    con.execute("UPDATE files SET is_fav=? WHERE user_id=? AND id=?", (fav, user_id, row_id))
    con.commit()
    con.close()

def get_row(user_id: int, row_id: int) -> Optional[tuple]:
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, file_type, file_id, file_unique_id, file_name, mime_type, file_size, caption, created_at, is_fav
        FROM files
        WHERE user_id=? AND id=?
        """,
        (user_id, row_id),
    )
    row = cur.fetchone()
    con.close()
    return row

def list_recent(user_id: int, limit: int = 10) -> List[tuple]:
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, file_type, file_name, caption, created_at, is_fav
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

def list_favs(user_id: int, limit: int = 20) -> List[tuple]:
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, file_type, file_name, caption, created_at
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

def search_files(user_id: int, q: str, limit: int = 20) -> List[tuple]:
    q = (q or "").strip().lower()
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, file_type, file_name, caption, created_at, is_fav
        FROM files
        WHERE user_id=?
          AND (
              LOWER(subject) LIKE ?
              OR LOWER(caption) LIKE ?
              OR LOWER(file_name) LIKE ?
          )
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, f"%{q}%", f"%{q}%", f"%{q}%", limit),
    )
    rows = cur.fetchall()
    con.close()
    return rows

def list_by_subject(user_id: int, subject: str, limit: int = 10, offset: int = 0) -> List[tuple]:
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, file_type, file_name, caption, created_at, is_fav
        FROM files
        WHERE user_id=? AND subject=?
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, subject, limit, offset),
    )
    rows = cur.fetchall()
    con.close()
    return rows

def count_by_subject(user_id: int, subject: str) -> int:
    con = db()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM files WHERE user_id=? AND subject=?", (user_id, subject))
    n = cur.fetchone()[0]
    con.close()
    return int(n)

# =========================
# Smart Subject Detection
# =========================
def normalize_subject(s: str) -> Optional[str]:
    if not s:
        return None
    t = s.strip().lower()

    # إزالة رموز شائعة
    t = re.sub(r"^[#@•\-_\s]+", "", t)
    t = re.sub(r"[:\|\-].*$", "", t).strip()  # خذ أول كلمة قبل ':' أو '-' أو '|'

    if t in SUBJECT_SET:
        return SUBJECT_SET[t]
    if t in SUBJECT_ALIASES:
        return SUBJECT_ALIASES[t]

    # محاولة مطابقة تقريبية: لو كتب "linguistics unit1"
    parts = re.split(r"\s+", s.strip(), maxsplit=1)
    if parts:
        head = parts[0].strip().lower()
        if head in SUBJECT_SET:
            return SUBJECT_SET[head]
        if head in SUBJECT_ALIASES:
            return SUBJECT_ALIASES[head]

    return None

def extract_subject_from_caption_or_text(text: str) -> Optional[str]:
    if not text:
        return None
    # أمثلة مدعومة:
    # "Linguistics"
    # "Linguistics - أصل اللغة"
    # "Poetry: sonnet"
    # "#Grammar Unit 1"
    # "gram unit 2"
    return normalize_subject(text)

def set_pending_subject(context: ContextTypes.DEFAULT_TYPE, subject: str, minutes: int = 5):
    context.user_data["pending_subject"] = subject
    context.user_data["pending_until"] = (datetime.utcnow() + timedelta(minutes=minutes)).isoformat()

def get_pending_subject(context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    subj = context.user_data.get("pending_subject")
    until = context.user_data.get("pending_until")
    if not subj or not until:
        return None
    try:
        if datetime.utcnow() <= datetime.fromisoformat(until):
            return subj
    except Exception:
        return None
    context.user_data.pop("pending_subject", None)
    context.user_data.pop("pending_until", None)
    return None

# =========================
# Pretty formatting
# =========================
def pretty_row(row: tuple) -> str:
    # (id, subject, file_type, file_name, caption, created_at, is_fav)
    fid, subj, ftype, fname, cap, created, fav = row
    star = "⭐" if fav else "☆"
    cap = (cap or "").strip()
    fname = (fname or "").strip()
    date = (created or "").split("T")[0] if created else "—"
    label = cap if cap else (fname if fname else "بدون وصف")
    if len(label) > 60:
        label = label[:60] + "…"
    return f"{star} #{fid} | {subj} | {ftype} | {label} | {date}"

def file_actions_kb(row_id: int, is_fav: int) -> InlineKeyboardMarkup:
    fav_btn = InlineKeyboardButton("⭐ مفضلة" if not is_fav else "☆ إزالة المفضلة", callback_data=f"fav:{row_id}:{0 if is_fav else 1}")
    send_btn = InlineKeyboardButton("📎 إرسال الملف", callback_data=f"send:{row_id}")
    return InlineKeyboardMarkup([[send_btn], [fav_btn]])

def subject_list_nav_kb(subject: str, page: int, per_page: int, total: int) -> InlineKeyboardMarkup:
    # page starts at 0
    max_page = max((total - 1) // per_page, 0)
    prev_btn = InlineKeyboardButton("⬅️ السابق", callback_data=f"sub:{subject}:{max(page-1,0)}") if page > 0 else InlineKeyboardButton("—", callback_data="noop")
    next_btn = InlineKeyboardButton("التالي ➡️", callback_data=f"sub:{subject}:{min(page+1,max_page)}") if page < max_page else InlineKeyboardButton("—", callback_data="noop")
    return InlineKeyboardMarkup([[prev_btn, next_btn]])

# =========================
# Commands
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "يا هلا رأفت 👋\n"
        "أنا بوت الأرشفة الذكي 📚\n\n"
        "✅ تقدر تأرشف بطريقتين:\n"
        "1) من الأزرار: 📚 المواد ثم اختر المادة ثم أرسل الملف.\n"
        "2) *الأسرع*: أرسل الملف واكتب بالكابشن اسم المادة مثل: `Linguistics - أصل اللغة`.\n"
        "   أو اكتب رسالة: `Poetry` وبعدها أرسل الملف مباشرة.\n\n"
        "اختَر من القائمة 👇",
        reply_markup=MAIN_KB,
        parse_mode=ParseMode.MARKDOWN,
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ **طريقة الاستخدام**\n\n"
        "✅ **أسرع طريقة**:\n"
        "• أرسل ملف + اكتب بالكابشن اسم المادة بالبداية:\n"
        "`Grammar - Unit 1`\n"
        "`Linguistics: أصل اللغة`\n\n"
        "✅ **طريقة الأزرار**:\n"
        "• 📚 المواد → اختر مادة → أرسل الملف\n\n"
        "📌 **عرض الملفات**:\n"
        "• 📚 المواد → اختر مادة (سيظهر لك ملفاتها مع صفحات)\n\n"
        "⭐ **مفضلة**: من زر ⭐ تحت رسالة الأرشفة.\n"
        "🔎 **بحث**: اكتب كلمة وسيعرض النتائج.\n"
        "🗂️ **نسخة احتياطية**: يرسل archive.db.\n",
        reply_markup=MAIN_KB,
        parse_mode=ParseMode.MARKDOWN,
    )

# =========================
# Menu handler
# =========================
async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # Back
    if text == "⬅️ رجوع":
        context.user_data.pop("awaiting_file", None)
        context.user_data.pop("subject", None)
        context.user_data.pop("search_mode", None)
        await update.message.reply_text("رجعناك للقائمة الرئيسية ✅", reply_markup=MAIN_KB)
        return

    # If user typed a subject name alone -> pending subject for next file
    subj_direct = normalize_subject(text)
    if subj_direct and text.lower() in (subj_direct.lower(),) or subj_direct and len(text.split()) == 1:
        # لا نخرب تجربة الأزرار: نخليها ميزة
        set_pending_subject(context, subj_direct, minutes=10)
        await update.message.reply_text(
            f"تمام ✅\nالمادة المؤقتة للملف القادم: **{subj_direct}**\n"
            "الآن أرسل الملف مباشرة (خلال 10 دقائق).",
            reply_markup=MAIN_KB,
            parse_mode=ParseMode.MARKDOWN,
        )
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
            f"تمام ✅ المادة الحالية: **{subject}**\n"
            "أرسل ملف/صورة/PDF الآن.\n"
            "وتقدر تكتب وصف بالكابشن مثل: `Unit 1`",
            reply_markup=BACK_KB,
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if text == "🧾 آخر الملفات":
        context.user_data.pop("search_mode", None)
        rows = list_recent(update.effective_user.id, 10)
        if not rows:
            await update.message.reply_text("لسّا ما عندك ملفات مؤرشفة. ابدأ من 📚 المواد ✅", reply_markup=MAIN_KB)
            return
        msg = "🧾 **آخر الملفات**:\n\n" + "\n".join(pretty_row(r) for r in rows)
        await update.message.reply_text(msg, reply_markup=MAIN_KB, parse_mode=ParseMode.MARKDOWN)
        return

    if text == "⭐ المفضلة":
        context.user_data.pop("search_mode", None)
        rows = list_favs(update.effective_user.id, 20)
        if not rows:
            await update.message.reply_text("المفضلة فارغة ⭐\nخلّيك تضيف من زر ⭐ بعد الأرشفة.", reply_markup=MAIN_KB)
            return
        msg_lines = []
        for r in rows:
            fid, subj, ftype, fname, cap, created = r
            label = (cap or fname or "بدون وصف")
            label = (label[:60] + "…") if len(label) > 60 else label
            msg_lines.append(f"⭐ #{fid} | {subj} | {ftype} | {label} | {(created or '').split('T')[0]}")
        await update.message.reply_text("⭐ **المفضلة**:\n\n" + "\n".join(msg_lines), reply_markup=MAIN_KB, parse_mode=ParseMode.MARKDOWN)
        return

    if text == "🔎 بحث":
        context.user_data["search_mode"] = True
        await update.message.reply_text("اكتب كلمة البحث الآن 🔎\nمثال: `unit 1` أو `linguistics`", reply_markup=MAIN_KB, parse_mode=ParseMode.MARKDOWN)
        return

    if text == "🗂️ نسخة احتياطية":
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

    if text == "ℹ️ مساعدة":
        await help_cmd(update, context)
        return

    # Search mode
    if context.user_data.get("search_mode"):
        q = text
        rows = search_files(update.effective_user.id, q, 30)
        if not rows:
            await update.message.reply_text(f"ما لقيت شي عن: {q}", reply_markup=MAIN_KB)
            return
        msg = f"🔎 **نتائج البحث عن:** `{q}`\n\n" + "\n".join(pretty_row(r) for r in rows)
        await update.message.reply_text(msg, reply_markup=MAIN_KB, parse_mode=ParseMode.MARKDOWN)
        return

    await update.message.reply_text("اختَر من الأزرار حتى أمشي صح ✅", reply_markup=MAIN_KB)

# =========================
# Subject list via callback
# =========================
async def show_subject_files(update: Update, context: ContextTypes.DEFAULT_TYPE, subject: str, page: int = 0):
    user_id = update.effective_user.id
    per_page = 10
    total = count_by_subject(user_id, subject)
    offset = page * per_page
    rows = list_by_subject(user_id, subject, limit=per_page, offset=offset)

    if total == 0:
        text = f"📘 **{subject}**\n\nماكو ملفات بعد بهاي المادة.\n\nارسل ملف واكتب بالكابشن اسم المادة: `{subject} - وصف`"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📚 رجوع للمواد", callback_data="subjects")]])
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KB)
        return

    lines = [f"📘 **{subject}** — ({total} ملف)\n"]
    for r in rows:
        lines.append(pretty_row(r))

    nav = subject_list_nav_kb(subject, page, per_page, total)
    extra = InlineKeyboardMarkup(nav.inline_keyboard + [[InlineKeyboardButton("📚 رجوع للمواد", callback_data="subjects")]])

    if update.callback_query:
        await update.callback_query.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=extra)
    else:
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KB)

# =========================
# File handler (smart save)
# =========================
async def handle_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    message_id = update.message.message_id

    caption = (update.message.caption or "").strip()

    # 1) Try subject from caption
    subject = extract_subject_from_caption_or_text(caption)

    # 2) If not, try pending subject from last text
    if not subject:
        subject = get_pending_subject(context)

    # 3) If still not, use selected subject from menu mode
    if not subject and context.user_data.get("awaiting_file") and context.user_data.get("subject"):
        subject = context.user_data.get("subject")

    # 4) Fallback
    if not subject:
        subject = "Other"

    file_type = None
    file_id = None
    file_unique_id = None
    file_name = ""
    mime_type = ""
    file_size = 0

    if update.message.document:
        doc = update.message.document
        file_type = "document"
        file_id = doc.file_id
        file_unique_id = doc.file_unique_id
        file_name = doc.file_name or ""
        mime_type = doc.mime_type or ""
        file_size = doc.file_size or 0

    elif update.message.photo:
        file_type = "photo"
        ph = update.message.photo[-1]  # أعلى دقة
        file_id = ph.file_id
        file_unique_id = ph.file_unique_id
        file_size = ph.file_size or 0

    elif update.message.video:
        v = update.message.video
        file_type = "video"
        file_id = v.file_id
        file_unique_id = v.file_unique_id
        mime_type = v.mime_type or ""
        file_size = v.file_size or 0

    elif update.message.audio:
        a = update.message.audio
        file_type = "audio"
        file_id = a.file_id
        file_unique_id = a.file_unique_id
        file_name = a.file_name or ""
        mime_type = a.mime_type or ""
        file_size = a.file_size or 0

    elif update.message.voice:
        vc = update.message.voice
        file_type = "voice"
        file_id = vc.file_id
        file_unique_id = vc.file_unique_id
        mime_type = vc.mime_type or ""
        file_size = vc.file_size or 0

    else:
        await update.message.reply_text("هذا النوع حالياً ما أدعمه. أرسل PDF/صورة/فيديو/صوت ✅", reply_markup=MAIN_KB)
        return

    # Clean caption: لو الكابشن يبدأ باسم مادة، نخليه بدون تكرار في الوصف
    cleaned_caption = caption
    maybe = extract_subject_from_caption_or_text(caption)
    if maybe:
        # احذف بداية المادة من الوصف، وخلي الباقي
        cleaned_caption = re.sub(r"^\s*[#@]?\s*" + re.escape(maybe) + r"\s*([:\-|\u2013\u2014])?\s*", "", caption, flags=re.IGNORECASE).strip()

    data = {
        "user_id": user_id,
        "chat_id": chat_id,
        "message_id": message_id,
        "subject": subject,
        "file_type": file_type,
        "file_id": file_id,
        "file_unique_id": file_unique_id,
        "file_name": file_name,
        "mime_type": mime_type,
        "file_size": file_size,
        "caption": cleaned_caption,
        "created_at": datetime.utcnow().isoformat(),
        "is_fav": 0,
    }

    row_id = insert_file(data)

    # Human response
    hint = ""
    if subject == "Other":
        hint = "\n\n💡 تلميح: اكتب بالكابشن اسم المادة بالبداية مثل:\n`Linguistics - أصل اللغة`"

    text = (
        "✅ **تمت الأرشفة بنجاح!**\n\n"
        f"📚 المادة: **{subject}**\n"
        f"📦 النوع: `{file_type}`\n"
        f"🆔 رقم الأرشفة: `#{row_id}`\n"
        f"📝 الوصف: {cleaned_caption if cleaned_caption else '—'}"
        f"{hint}"
    )

    await update.message.reply_text(
        text,
        reply_markup=file_actions_kb(row_id, 0),
        parse_mode=ParseMode.MARKDOWN,
    )

# =========================
# Callbacks
# =========================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = (q.data or "")
    await q.answer()

    if data == "noop":
        return

    if data == "subjects":
        await q.edit_message_text("اختر المادة من الأزرار 👇\n(أو استخدم 📚 المواد)", reply_markup=None)
        await q.message.reply_text("📚 المواد:", reply_markup=subjects_keyboard())
        return

    # pagination: sub:<subject>:<page>
    if data.startswith("sub:"):
        _, subject, page_s = data.split(":")
        await show_subject_files(update, context, subject, int(page_s))
        return

    # fav:<id>:<0|1>
    if data.startswith("fav:"):
        _, rid, val = data.split(":")
        rid_i = int(rid)
        val_i = int(val)
        set_fav(update.effective_user.id, rid_i, val_i)
        row = get_row(update.effective_user.id, rid_i)
        is_fav = row[-1] if row else val_i
        await q.edit_message_reply_markup(reply_markup=file_actions_kb(rid_i, is_fav))
        return

    # send:<id>
    if data.startswith("send:"):
        _, rid = data.split(":")
        rid_i = int(rid)
        row = get_row(update.effective_user.id, rid_i)
        if not row:
            await q.message.reply_text("ما لقيت هذا الملف. يمكن انحذف من قاعدة البيانات.", reply_markup=MAIN_KB)
            return

        (fid, subj, ftype, file_id, fuid, fname, mime, fsize, cap, created, is_fav) = row
        header = f"📎 هذا هو الملف `#{fid}` من مادة **{subj}**"
        await q.message.reply_text(header, parse_mode=ParseMode.MARKDOWN)

        # resend by type
        if ftype == "document":
            await q.message.reply_document(document=file_id, caption=cap or "")
        elif ftype == "photo":
            await q.message.reply_photo(photo=file_id, caption=cap or "")
        elif ftype == "video":
            await q.message.reply_video(video=file_id, caption=cap or "")
        elif ftype == "audio":
            await q.message.reply_audio(audio=file_id, caption=cap or "")
        elif ftype == "voice":
            await q.message.reply_voice(voice=file_id, caption=cap or "")
        else:
            await q.message.reply_text("نوع الملف غير مدعوم للإرسال.", reply_markup=MAIN_KB)
        return

# =========================
# Extra: show subject files from menu
# =========================
async def handle_subject_pick_from_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text.startswith("📘 "):
        subject = text.replace("📘 ", "").strip()
        await update.message.reply_text(
            f"📘 {subject}\nجاري جلب الملفات…",
            reply_markup=MAIN_KB
        )
        await show_subject_files(update, context, subject, 0)

# =========================
# Main
# =========================
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it in Railway Variables.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))

    # Callbacks
    app.add_handler(CallbackQueryHandler(on_callback))

    # Files (لازم قبل نصوص القائمة؟ لا، لأن filters.TEXT لن يلتقط الملفات.
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

    # Text menus
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_subject_pick_from_keyboard))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_text))

    log.info("Bot is running...")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()