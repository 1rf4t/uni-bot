# uni_bot.py
# Raafat Archive Bot — مكتبة جامعية ذكية داخل تلگرام ✅
# - أرشفة: PDF/صور/فيديو/صوت/Voice
# - مواد بايموجي + عدد العناصر داخل كل مادة
# - فتح الملفات مباشرة بزر واحد (بدون كتابة أرقام)
# - صفحات (التالي/السابق)
# - مفضلة ⭐ + حذف 🗑️
# - بحث 🔎
# - نسخة احتياطية 🗄️ ترسل قاعدة البيانات لك
#
# تشغيل آمن للتوكن (بدون وضعه بالكود):
# 1) في Termux:
#    export BOT_TOKEN="ضع_توكن_البوت_هنا"
# 2) ثم:
#    python uni_bot.py
#
# متطلبات:
#   pip install -U python-telegram-bot==21.6

import os
import re
import sqlite3
from datetime import datetime, timedelta

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
DB_PATH = os.getenv("DB_PATH", "archive.db")

if not BOT_TOKEN:
    raise SystemExit(
        "❌ BOT_TOKEN غير موجود.\n"
        "في Termux نفّذ:\n"
        'export BOT_TOKEN="ضع_التوكن_هنا"\n'
        "ثم شغّل البوت."
    )

PAGE_SIZE = 10
PIN_MINUTES = 10  # تثبيت المادة لمدة

# =========================
# SUBJECTS (عدل هنا)
# =========================
# الاسم الظاهر للمستخدم -> الاسم الداخلي للتخزين بالـ DB
SUBJECTS = [
    ("✒️ Poetry", "Poetry"),
    ("📝 Writing", "Writing"),
    ("🧠 Psychological Health", "Psychological Health"),
    ("🎭 Drama", "Drama"),
    ("🧩 Linguistics", "Linguistics"),
    ("📖 Novel", "Novel"),
    ("🎓 Pedagogy & Curriculum Innovation", "Pedagogy and Curriculum Innovation"),
    ("📘 Grammar", "Grammar"),
    ("🎧 Listening & Speaking", "Listening and speaking"),
    ("📦 Other", "Other"),
]

# =========================
# UI (قائمة رئيسية احترافية)
# =========================
MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📚 المواد"), KeyboardButton("➕ أرشفة")],
        [KeyboardButton("🆕 آخر الملفات"), KeyboardButton("⭐ المفضلة")],
        [KeyboardButton("🔎 بحث"), KeyboardButton("🗄️ نسخة احتياطية")],
        [KeyboardButton("ℹ️ مساعدة")],
    ],
    resize_keyboard=True,
)

# =========================
# DB
# =========================
def db():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL;")
    return con


def init_db():
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS archives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            kind TEXT NOT NULL,              -- document/photo/video/audio/voice
            tg_file_id TEXT NOT NULL,        -- file_id on Telegram
            filename TEXT,
            description TEXT,
            created_at TEXT NOT NULL,
            is_fav INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_subject ON archives(user_id, subject);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_fav ON archives(user_id, is_fav);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_text ON archives(user_id, filename, description, subject);")
    con.commit()
    con.close()


def add_item(user_id: int, subject: str, kind: str, tg_file_id: str, filename: str, description: str):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO archives(user_id, subject, kind, tg_file_id, filename, description, created_at)
        VALUES(?,?,?,?,?,?,?)
        """,
        (user_id, subject, kind, tg_file_id, filename, description, datetime.now().strftime("%Y-%m-%d %H:%M")),
    )
    con.commit()
    new_id = cur.lastrowid
    con.close()
    return new_id


def subject_counts(user_id: int):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT subject, COUNT(*)
        FROM archives
        WHERE user_id=?
        GROUP BY subject
        """,
        (user_id,),
    )
    rows = cur.fetchall()
    con.close()
    return {s: c for s, c in rows}


def list_subject_items(user_id: int, subject: str, limit: int, offset: int):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, kind, filename, description, created_at, is_fav
        FROM archives
        WHERE user_id=? AND subject=?
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, subject, limit, offset),
    )
    rows = cur.fetchall()

    cur.execute(
        """
        SELECT 1
        FROM archives
        WHERE user_id=? AND subject=?
        ORDER BY id DESC
        LIMIT 1 OFFSET ?
        """,
        (user_id, subject, offset + limit),
    )
    has_next = cur.fetchone() is not None
    con.close()
    return rows, has_next


def get_item(user_id: int, item_id: int):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, kind, tg_file_id, filename, description, created_at, is_fav
        FROM archives
        WHERE user_id=? AND id=?
        LIMIT 1
        """,
        (user_id, item_id),
    )
    row = cur.fetchone()
    con.close()
    return row


def set_fav(user_id: int, item_id: int, fav: int):
    con = db()
    cur = con.cursor()
    cur.execute(
        "UPDATE archives SET is_fav=? WHERE user_id=? AND id=?",
        (fav, user_id, item_id),
    )
    con.commit()
    con.close()


def delete_item(user_id: int, item_id: int):
    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM archives WHERE user_id=? AND id=?", (user_id, item_id))
    con.commit()
    con.close()


def list_recent(user_id: int, limit: int = 15):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, kind, filename, description, created_at, is_fav
        FROM archives
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    rows = cur.fetchall()
    con.close()
    return rows


def list_favs(user_id: int, limit: int = 30):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, kind, filename, description, created_at, is_fav
        FROM archives
        WHERE user_id=? AND is_fav=1
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    rows = cur.fetchall()
    con.close()
    return rows


def search_items(user_id: int, q: str, limit: int = 30):
    q2 = f"%{q}%"
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, kind, filename, description, created_at, is_fav
        FROM archives
        WHERE user_id=?
        AND (
            subject LIKE ? OR
            filename LIKE ? OR
            description LIKE ?
        )
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, q2, q2, q2, limit),
    )
    rows = cur.fetchall()
    con.close()
    return rows


# =========================
# Helpers
# =========================
def normalize_subject(text: str):
    t = text.strip().lower()
    for display, internal in SUBJECTS:
        if t == internal.lower() or t == display.lower():
            return internal
        # السماح بكتابة الاسم بدون ايموجي
        disp_no_emoji = re.sub(r"^[^\wA-Za-z]+", "", display).strip().lower()
        if t == disp_no_emoji:
            return internal
    return None


def pinned_subject(context: ContextTypes.DEFAULT_TYPE):
    info = context.user_data.get("pinned_subject")
    if not info:
        return None
    subject, expires = info
    if datetime.now() > expires:
        context.user_data.pop("pinned_subject", None)
        return None
    return subject


def pin_subject(context: ContextTypes.DEFAULT_TYPE, subject: str):
    context.user_data["pinned_subject"] = (subject, datetime.now() + timedelta(minutes=PIN_MINUTES))


def subjects_keyboard(user_id: int):
    counts = subject_counts(user_id)
    buttons = []
    row = []
    for display, internal in SUBJECTS:
        cnt = counts.get(internal, 0)
        txt = f"{display} ({cnt})"
        row.append(InlineKeyboardButton(txt, callback_data=f"subj:{internal}:0"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("↩️ رجوع", callback_data="home")])
    return InlineKeyboardMarkup(buttons)


def archive_subjects_keyboard():
    buttons = []
    row = []
    for display, internal in SUBJECTS:
        row.append(InlineKeyboardButton(display, callback_data=f"pin:{internal}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("↩️ رجوع", callback_data="home")])
    return InlineKeyboardMarkup(buttons)


def icon_for_kind(kind: str):
    return {
        "document": "📄",
        "photo": "🖼️",
        "video": "🎬",
        "audio": "🎵",
        "voice": "🎙️",
    }.get(kind, "📎")


def fmt_item_line(row):
    # row: (id, kind, filename, description, created_at, is_fav)
    item_id, kind, filename, desc, created_at, is_fav = row
    ico = icon_for_kind(kind)
    star = "⭐" if is_fav else "☆"
    title = filename or f"Item #{item_id}"
    if desc:
        title = f"{title} — {desc}"
    if len(title) > 45:
        title = title[:42] + "..."
    return f"{star} #{item_id} | {ico} {title} | {created_at}"


def files_keyboard(subject: str, rows: list, page: int, has_next: bool):
    buttons = []
    for (item_id, kind, filename, desc, created_at, is_fav) in rows:
        ico = icon_for_kind(kind)
        title = filename or f"#{item_id}"
        if desc:
            title = f"{title} — {desc}"
        if len(title) > 35:
            title = title[:32] + "..."
        buttons.append([InlineKeyboardButton(f"{ico} {title}", callback_data=f"open:{item_id}:{subject}:{page}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"subj:{subject}:{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton("التالي ➡️", callback_data=f"subj:{subject}:{page+1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton("↩️ رجوع للمواد", callback_data="subjects")])
    return InlineKeyboardMarkup(buttons)


def item_actions_kb(item_id: int, subject: str, page: int, is_fav: int):
    fav_txt = "⭐ إزالة من المفضلة" if is_fav else "⭐ إضافة للمفضلة"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(fav_txt, callback_data=f"fav:{item_id}:{subject}:{page}:{1 if not is_fav else 0}")],
            [InlineKeyboardButton("🗑️ حذف", callback_data=f"del:{item_id}:{subject}:{page}")],
            [InlineKeyboardButton("↩️ رجوع", callback_data=f"subj:{subject}:{page}")],
        ]
    )


# =========================
# Handlers
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pin = pinned_subject(context)
    msg = (
        "👋 *هلا رأفت!*\n"
        "أنا *مكتبتك الجامعية* داخل تلگرام ✅\n\n"
        "📌 طريقتين للأرشفة:\n"
        "1) اضغط *➕ أرشفة* واختر المادة ثم أرسل ملفاتك.\n"
        "2) اكتب اسم المادة (مثلاً: `Linguistics`) لتثبيتها 10 دقائق ثم أرسل ملفاتك.\n\n"
        f"🔒 التوكن آمن لأنّه ليس داخل الكود.\n"
    )
    if pin:
        msg += f"\n✅ المادة المثبّتة حالياً: *{pin}* (صالحة {PIN_MINUTES} دقائق)"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KB)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *مساعدة سريعة*\n\n"
        "📚 المواد: يعرض المواد مع عدد العناصر.\n"
        "➕ أرشفة: تختار مادة ثم ترسل ملفات.\n"
        "🆕 آخر الملفات: آخر ما حفظته.\n"
        "⭐ المفضلة: ملفاتك المميزة.\n"
        "🔎 بحث: اكتب كلمة ويطلع نتائج.\n"
        "🗄️ نسخة احتياطية: يرسل لك ملف قاعدة البيانات.\n\n"
        "💡 تقدر تثبّت مادة بسرعة بكتابة اسمها فقط: `Linguistics`\n",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=MAIN_KB,
    )


async def home_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏠 القائمة الرئيسية:", reply_markup=MAIN_KB)


async def show_subjects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 *موادك (مع عدد الملفات):*\nاضغط مادة لعرض ملفاتها وفتحها مباشرة ✅",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=subjects_keyboard(update.effective_user.id),
    )


async def choose_archive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "➕ *اختر مادة لتثبيتها للأرشفة (10 دقائق)* ثم ارسل ملفاتك مباشرة.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=archive_subjects_keyboard(),
    )


async def show_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = list_recent(update.effective_user.id, 20)
    if not rows:
        await update.message.reply_text("ما عندك أرشيف بعد. ابدأ بـ ➕ أرشفة ✅", reply_markup=MAIN_KB)
        return

    buttons = []
    for (item_id, subject, kind, filename, desc, created_at, is_fav) in rows:
        ico = icon_for_kind(kind)
        title = filename or f"#{item_id}"
        if desc:
            title = f"{title} — {desc}"
        if len(title) > 35:
            title = title[:32] + "..."
        buttons.append([InlineKeyboardButton(f"{ico} {subject} | {title}", callback_data=f"open:{item_id}:{subject}:0")])
    buttons.append([InlineKeyboardButton("↩️ رجوع", callback_data="home")])

    await update.message.reply_text(
        "🆕 *آخر الملفات:* اضغط لفتح أي ملف.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_favs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = list_favs(update.effective_user.id, 30)
    if not rows:
        await update.message.reply_text("⭐ ما عندك مفضلة بعد.", reply_markup=MAIN_KB)
        return

    buttons = []
    for (item_id, subject, kind, filename, desc, created_at, is_fav) in rows:
        ico = icon_for_kind(kind)
        title = filename or f"#{item_id}"
        if desc:
            title = f"{title} — {desc}"
        if len(title) > 35:
            title = title[:32] + "..."
        buttons.append([InlineKeyboardButton(f"{ico} {subject} | {title}", callback_data=f"open:{item_id}:{subject}:0")])
    buttons.append([InlineKeyboardButton("↩️ رجوع", callback_data="home")])

    await update.message.reply_text(
        "⭐ *المفضلة:* اضغط لفتح أي ملف.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def start_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["search_mode"] = True
    await update.message.reply_text("🔎 اكتب كلمة البحث (اسم ملف / وصف / مادة):", reply_markup=MAIN_KB)


async def backup_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # إرسال ملف قاعدة البيانات
    if not os.path.exists(DB_PATH):
        await update.message.reply_text("لا توجد قاعدة بيانات بعد.", reply_markup=MAIN_KB)
        return
    await update.message.reply_document(
        document=open(DB_PATH, "rb"),
        filename=os.path.basename(DB_PATH),
        caption="🗄️ نسخة احتياطية لقاعدة بيانات الأرشيف.",
        reply_markup=MAIN_KB,
    )


# =========================
# Callbacks
# =========================
async def cb_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    await q.answer()

    if data == "home":
        await q.edit_message_text("🏠 رجعتك للقائمة الرئيسية ✅")
        return

    if data == "subjects":
        await q.edit_message_text(
            "📚 *موادك (مع عدد الملفات):*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=subjects_keyboard(q.from_user.id),
        )
        return

    if data.startswith("pin:"):
        subject = data.split(":", 1)[1]
        pin_subject(context, subject)
        await q.edit_message_text(
            f"✅ ثبّتت المادة مؤقتاً: *{subject}*\n"
            f"الآن أرسل ملفاتك… (صالحة {PIN_MINUTES} دقائق)\n\n"
            "💡 إذا تريد وصف: اكتب النص كـ *Caption* مع الملف.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data.startswith("subj:"):
        # subj:<subject>:<page>
        _, subject, page_str = data.split(":", 2)
        page = int(page_str)
        rows, has_next = list_subject_items(q.from_user.id, subject, PAGE_SIZE, page * PAGE_SIZE)

        if not rows:
            await q.edit_message_text(
                f"📚 *{subject}*\n\nلا توجد ملفات محفوظة بهذه المادة بعد.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ رجوع للمواد", callback_data="subjects")]]),
            )
            return

        await q.edit_message_text(
            f"📚 *{subject}* — اختر ملف لفتحه:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=files_keyboard(subject, rows, page, has_next),
        )
        return

    if data.startswith("open:"):
        # open:<id>:<subject>:<page>
        _, item_id_str, subject, page_str = data.split(":", 3)
        item_id = int(item_id_str)
        page = int(page_str)

        rec = get_item(q.from_user.id, item_id)
        if not rec:
            await q.answer("الملف غير موجود أو تم حذفه.", show_alert=True)
            return

        _, r_subject, kind, tg_file_id, filename, desc, created_at, is_fav = rec
        cap = f"*{r_subject}* | #{item_id}\n{created_at}"
        if filename:
            cap += f"\n📄 {filename}"
        if desc:
            cap += f"\n📝 {desc}"

        # إرسال الملف نفسه
        if kind == "document":
            await q.message.reply_document(document=tg_file_id, caption=cap, parse_mode=ParseMode.MARKDOWN)
        elif kind == "photo":
            await q.message.reply_photo(photo=tg_file_id, caption=cap, parse_mode=ParseMode.MARKDOWN)
        elif kind == "video":
            await q.message.reply_video(video=tg_file_id, caption=cap, parse_mode=ParseMode.MARKDOWN)
        elif kind == "audio":
            await q.message.reply_audio(audio=tg_file_id, caption=cap, parse_mode=ParseMode.MARKDOWN)
        elif kind == "voice":
            await q.message.reply_voice(voice=tg_file_id, caption=cap, parse_mode=ParseMode.MARKDOWN)
        else:
            await q.message.reply_document(document=tg_file_id, caption=cap, parse_mode=ParseMode.MARKDOWN)

        # رسالة تحكم (مفضلة/حذف/رجوع)
        await q.message.reply_text(
            "⚙️ إدارة الملف:",
            reply_markup=item_actions_kb(item_id, subject, page, is_fav),
        )
        return

    if data.startswith("fav:"):
        # fav:<id>:<subject>:<page>:<fav>
        _, item_id_str, subject, page_str, fav_str = data.split(":", 4)
        item_id = int(item_id_str)
        page = int(page_str)
        fav = int(fav_str)

        set_fav(q.from_user.id, item_id, fav)
        await q.answer("تم ✅" if fav else "تمت الإزالة ✅", show_alert=False)
        # تحديث رسالة الأزرار
        rec = get_item(q.from_user.id, item_id)
        if rec:
            is_fav = rec[-1]
            await q.edit_message_reply_markup(reply_markup=item_actions_kb(item_id, subject, page, is_fav))
        return

    if data.startswith("del:"):
        # del:<id>:<subject>:<page>
        _, item_id_str, subject, page_str = data.split(":", 3)
        item_id = int(item_id_str)
        page = int(page_str)

        delete_item(q.from_user.id, item_id)
        await q.edit_message_text("🗑️ تم حذف الملف ✅")
        # رجّع المستخدم لنفس صفحة المادة
        rows, has_next = list_subject_items(q.from_user.id, subject, PAGE_SIZE, page * PAGE_SIZE)
        if rows:
            await q.message.reply_text(
                f"📚 *{subject}* — اختر ملف لفتحه:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=files_keyboard(subject, rows, page, has_next),
            )
        else:
            await q.message.reply_text(
                f"📚 *{subject}*\nلا توجد ملفات الآن.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ رجوع للمواد", callback_data="subjects")]]),
            )
        return


# =========================
# Messages: subject pin / search / archive
# =========================
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    uid = update.effective_user.id

    # إذا وضع البحث مفعّل
    if context.user_data.get("search_mode"):
        context.user_data["search_mode"] = False
        if not text:
            await update.message.reply_text("اكتب كلمة بحث صحيحة.", reply_markup=MAIN_KB)
            return
        rows = search_items(uid, text, 30)
        if not rows:
            await update.message.reply_text("🔎 لا توجد نتائج.", reply_markup=MAIN_KB)
            return

        buttons = []
        for (item_id, subject, kind, filename, desc, created_at, is_fav) in rows:
            ico = icon_for_kind(kind)
            title = filename or f"#{item_id}"
            if desc:
                title = f"{title} — {desc}"
            if len(title) > 35:
                title = title[:32] + "..."
            buttons.append([InlineKeyboardButton(f"{ico} {subject} | {title}", callback_data=f"open:{item_id}:{subject}:0")])
        buttons.append([InlineKeyboardButton("↩️ رجوع", callback_data="home")])

        await update.message.reply_text(
            f"🔎 *نتائج البحث عن:* `{text}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    # تثبيت مادة إذا كتب اسم مادة
    subj = normalize_subject(text)
    if subj:
        pin_subject(context, subj)
        await update.message.reply_text(
            f"✅ ثبّتت المادة: *{subj}* لمدة {PIN_MINUTES} دقائق.\n"
            "الآن أرسل ملفاتك…\n"
            "💡 إذا تريد وصف: اكتب الوصف كـ *Caption* مع الملف.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=MAIN_KB,
        )
        return

    # نص عادي
    await update.message.reply_text("اكتب اسم مادة لتثبيتها أو استخدم الأزرار 👇", reply_markup=MAIN_KB)


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    subject = pinned_subject(context)
    if not subject:
        await update.message.reply_text("🧠 اكتب اسم مادة لتثبيتها أولاً (مثلاً: Linguistics) أو اضغط ➕ أرشفة.", reply_markup=MAIN_KB)
        return

    msg = update.message
    kind = None
    tg_file_id = None
    filename = None
    desc = (msg.caption or "").strip()

    # document
    if msg.document:
        kind = "document"
        tg_file_id = msg.document.file_id
        filename = msg.document.file_name or "document"
    # photo
    elif msg.photo:
        kind = "photo"
        tg_file_id = msg.photo[-1].file_id
        filename = "photo"
    # video
    elif msg.video:
        kind = "video"
        tg_file_id = msg.video.file_id
        filename = msg.video.file_name or "video"
    # audio
    elif msg.audio:
        kind = "audio"
        tg_file_id = msg.audio.file_id
        filename = msg.audio.file_name or "audio"
    # voice
    elif msg.voice:
        kind = "voice"
        tg_file_id = msg.voice.file_id
        filename = "voice"
    else:
        await update.message.reply_text("⚠️ هذا النوع غير مدعوم حالياً.", reply_markup=MAIN_KB)
        return

    new_id = add_item(uid, subject, kind, tg_file_id, filename, desc)
    await update.message.reply_text(
        f"✅ تمت الأرشفة بنجاح!\n"
        f"📚 المادة: {subject}\n"
        f"🆔 رقم: #{new_id}\n"
        f"⏳ تثبيت المادة ما زال فعّالاً.",
        reply_markup=MAIN_KB,
    )


# =========================
# Main menu buttons
# =========================
async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()

    if t == "📚 المواد":
        await show_subjects(update, context)
    elif t == "➕ أرشفة":
        await choose_archive(update, context)
    elif t == "🆕 آخر الملفات":
        await show_recent(update, context)
    elif t == "⭐ المفضلة":
        await show_favs(update, context)
    elif t == "🔎 بحث":
        await start_search(update, context)
    elif t == "🗄️ نسخة احتياطية":
        await backup_db(update, context)
    elif t == "ℹ️ مساعدة":
        await help_cmd(update, context)
    else:
        await on_text(update, context)


# =========================
# Boot
# =========================
def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    # callbacks
    app.add_handler(CallbackQueryHandler(cb_router))

    # menu buttons + text
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_menu))

    # media
    media_filter = filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE
    app.add_handler(MessageHandler(media_filter, on_media))

    print("✅ Bot is running...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()