import os
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Tuple, List

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

# ======================
# CONFIG
# ======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "archive.db")

# عدّل قائمة المواد هنا كما تريد (كل موادك)
SUBJECTS = [
    "Grammar",
    "Phonetics",
    "Poetry",
    "Drama",
    "Novel",
    "Listening",
    "Writing",
    "Linguistics",
    "ELT",
    "Curriculum",
    "Translation",
    "Psychological Health",
    "Other",
]

# كم ملف بالصفحة عند عرض ملفات مادة
PAGE_SIZE = 10

# مدة تثبيت المادة بالوضع السريع (بالدقائق)
FAST_LOCK_MINUTES = 10


# ======================
# UI (Reply Keyboard)
# ======================
MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📚 المواد"), KeyboardButton("🧾 آخر الملفات")],
        [KeyboardButton("⭐ المفضلة"), KeyboardButton("🔎 بحث")],
        [KeyboardButton("🗂️ نسخة احتياطية"), KeyboardButton("ℹ️ مساعدة")],
    ],
    resize_keyboard=True,
)

BACK_KB = ReplyKeyboardMarkup([[KeyboardButton("⬅️ رجوع")]], resize_keyboard=True)


# ======================
# DB / SCHEMA
# ======================
def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def ensure_schema() -> None:
    con = db()
    cur = con.cursor()

    cur.execute(
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
            file_name TEXT DEFAULT '',
            caption TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            is_fav INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    # ترقيات لو قاعدة قديمة
    cur.execute("PRAGMA table_info(files)")
    cols = {r["name"] for r in cur.fetchall()}

    def add_col(name: str, ddl: str):
        if name not in cols:
            cur.execute(ddl)

    add_col("file_name", "ALTER TABLE files ADD COLUMN file_name TEXT DEFAULT ''")
    add_col("caption", "ALTER TABLE files ADD COLUMN caption TEXT DEFAULT ''")

    con.commit()
    con.close()


def insert_file(
    user_id: int,
    chat_id: int,
    message_id: int,
    subject: str,
    file_type: str,
    file_id: str,
    file_unique_id: str,
    file_name: str,
    caption: str,
) -> int:
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO files
        (user_id, chat_id, message_id, subject, file_type, file_id, file_unique_id, file_name, caption, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            chat_id,
            message_id,
            subject,
            file_type,
            file_id,
            file_unique_id,
            file_name or "",
            caption or "",
            datetime.utcnow().isoformat(timespec="seconds"),
        ),
    )
    con.commit()
    rid = cur.lastrowid
    con.close()
    return rid


def get_file(user_id: int, rid: int) -> Optional[sqlite3.Row]:
    con = db()
    cur = con.cursor()
    cur.execute(
        "SELECT * FROM files WHERE user_id = ? AND id = ?",
        (user_id, rid),
    )
    row = cur.fetchone()
    con.close()
    return row


def delete_file(user_id: int, rid: int) -> None:
    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM files WHERE user_id = ? AND id = ?", (user_id, rid))
    con.commit()
    con.close()


def set_fav(user_id: int, rid: int, fav: int) -> None:
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE files SET is_fav = ? WHERE user_id = ? AND id = ?", (fav, user_id, rid))
    con.commit()
    con.close()


def move_file(user_id: int, rid: int, new_subject: str) -> None:
    con = db()
    cur = con.cursor()
    cur.execute(
        "UPDATE files SET subject = ? WHERE user_id = ? AND id = ?",
        (new_subject, user_id, rid),
    )
    con.commit()
    con.close()


def count_by_subject(user_id: int) -> List[Tuple[str, int]]:
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT subject, COUNT(*) as cnt
        FROM files
        WHERE user_id = ?
        GROUP BY subject
        ORDER BY cnt DESC, subject ASC
        """,
        (user_id,),
    )
    rows = [(r["subject"], int(r["cnt"])) for r in cur.fetchall()]
    con.close()
    return rows


def list_by_subject(user_id: int, subject: str, page: int = 0) -> Tuple[List[sqlite3.Row], int]:
    offset = page * PAGE_SIZE
    con = db()
    cur = con.cursor()

    cur.execute(
        "SELECT COUNT(*) as c FROM files WHERE user_id = ? AND subject = ?",
        (user_id, subject),
    )
    total = int(cur.fetchone()["c"])

    cur.execute(
        """
        SELECT *
        FROM files
        WHERE user_id = ? AND subject = ?
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, subject, PAGE_SIZE, offset),
    )
    items = cur.fetchall()
    con.close()
    return items, total


def list_recent(user_id: int, limit: int = 10) -> List[sqlite3.Row]:
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT *
        FROM files
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    rows = cur.fetchall()
    con.close()
    return rows


def list_favs(user_id: int, limit: int = 30) -> List[sqlite3.Row]:
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT *
        FROM files
        WHERE user_id = ? AND is_fav = 1
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    rows = cur.fetchall()
    con.close()
    return rows


def search_files(user_id: int, q: str, limit: int = 20) -> List[sqlite3.Row]:
    q = (q or "").strip().lower()
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT *
        FROM files
        WHERE user_id = ?
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


# ======================
# SMART PARSING
# ======================
def normalize_subject(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "Other"
    # توحيد بسيط
    s = re.sub(r"\s+", " ", s)
    # مطابقة مع القائمة بشكل case-insensitive
    for subj in SUBJECTS:
        if s.lower() == subj.lower():
            return subj
    # لو كتب اختصار أو كلمة قريبة
    for subj in SUBJECTS:
        if s.lower() in subj.lower() or subj.lower() in s.lower():
            return subj
    return "Other"


def extract_subject_and_caption(text: str) -> Tuple[Optional[str], str]:
    """
    يقبل أمثلة:
    - "Linguistics - أصل اللغة"
    - "Linguistics: أصل اللغة"
    - "Linguistics / Unit 1"
    - "Linguistics" (بدون وصف)
    """
    t = (text or "").strip()
    if not t:
        return None, ""

    m = re.match(r"^\s*([A-Za-z][A-Za-z\s&\-]+?)\s*[-:/|/]\s*(.+)$", t)
    if m:
        subj = normalize_subject(m.group(1).strip())
        cap = m.group(2).strip()
        return subj, cap

    # إذا مجرد اسم مادة
    if re.match(r"^[A-Za-z][A-Za-z\s&\-]+$", t):
        subj = normalize_subject(t)
        return subj, ""

    return None, t


def fast_lock_set(context: ContextTypes.DEFAULT_TYPE, subject: str):
    context.user_data["fast_subject"] = subject
    context.user_data["fast_until"] = (datetime.utcnow() + timedelta(minutes=FAST_LOCK_MINUTES)).isoformat(timespec="seconds")


def fast_lock_get(context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    subj = context.user_data.get("fast_subject")
    until = context.user_data.get("fast_until")
    if not subj or not until:
        return None
    try:
        dt_until = datetime.fromisoformat(until)
    except Exception:
        return None
    if datetime.utcnow() <= dt_until:
        return subj
    # انتهت
    context.user_data.pop("fast_subject", None)
    context.user_data.pop("fast_until", None)
    return None


# ======================
# RENDERING HELPERS
# ======================
def file_icon(file_type: str) -> str:
    return {
        "document": "📄",
        "photo": "🖼️",
        "video": "🎬",
        "audio": "🎵",
        "voice": "🎙️",
    }.get(file_type, "📦")


def short_text(s: str, n: int = 40) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def build_subjects_inline(user_id: int) -> InlineKeyboardMarkup:
    # اعرض كل المواد (حتى الفارغة) + العدد
    counts = dict(count_by_subject(user_id))
    buttons = []
    row = []
    for i, subj in enumerate(SUBJECTS, 1):
        cnt = counts.get(subj, 0)
        row.append(InlineKeyboardButton(f"📘 {subj} ({cnt})", callback_data=f"subj:{subj}:0"))
        if i % 2 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("⬅️ رجوع", callback_data="nav:home")])
    return InlineKeyboardMarkup(buttons)


def build_files_list_inline(subject: str, page: int, total: int) -> InlineKeyboardMarkup:
    # أزرار تنقل صفحات
    last_page = max((total - 1) // PAGE_SIZE, 0)
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"subj:{subject}:{page-1}"))
    nav_row.append(InlineKeyboardButton(f"📄 صفحة {page+1}/{last_page+1}", callback_data="noop"))
    if page < last_page:
        nav_row.append(InlineKeyboardButton("التالي ➡️", callback_data=f"subj:{subject}:{page+1}"))

    buttons = []
    if nav_row:
        buttons.append(nav_row)

    buttons.append(
        [
            InlineKeyboardButton("➕ أرشفة هنا", callback_data=f"fast:{subject}"),
            InlineKeyboardButton("⬅️ المواد", callback_data="nav:subjects"),
        ]
    )
    buttons.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="nav:home")])
    return InlineKeyboardMarkup(buttons)


def build_file_actions_inline(rid: int, is_fav: int) -> InlineKeyboardMarkup:
    fav_btn = InlineKeyboardButton("⭐ إزالة من المفضلة" if is_fav else "⭐ إضافة للمفضلة", callback_data=f"fav:{rid}:{0 if is_fav else 1}")
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📥 فتح/إرسال الملف", callback_data=f"open:{rid}")],
            [fav_btn],
            [InlineKeyboardButton("🏷️ نقل لمادة أخرى", callback_data=f"move:{rid}")],
            [InlineKeyboardButton("🗑️ حذف من الأرشيف", callback_data=f"del:{rid}")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data="nav:back")],
        ]
    )


def build_move_subjects_inline(rid: int) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for i, subj in enumerate(SUBJECTS, 1):
        row.append(InlineKeyboardButton(f"📘 {subj}", callback_data=f"mset:{rid}:{subj}"))
        if i % 2 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("⬅️ إلغاء", callback_data="nav:back")])
    return InlineKeyboardMarkup(buttons)


# ======================
# COMMANDS
# ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_schema()
    context.user_data.clear()
    await update.message.reply_text(
        "👋 هلا رأفت!\n"
        "أنا *بوت المكتبة الذكي* 📚\n\n"
        "✅ طريقتين للأرشفة:\n"
        "1) من القائمة: 📚 المواد → اختر مادة → أرسل الملف.\n"
        "2) الأسرع: اكتب اسم المادة لوحده (مثلاً: *Linguistics*) ثم حوّل/أرسل ملفات بعدها (يثبت 10 دقائق).\n"
        "   أو اكتب بالكابشن: `Linguistics - أصل اللغة`.\n\n"
        "اختر من القائمة 👇",
        reply_markup=MAIN_KB,
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *المساعدة*\n\n"
        "📌 *الأرشفة السريعة:*\n"
        "• اكتب اسم المادة فقط: `Linguistics`\n"
        "  بعدها ارسل/حوّل ملفات لمدة 10 دقائق.\n\n"
        "📌 *أرشفة مع وصف (كابشن ذكي):*\n"
        "• `Linguistics - أصل اللغة`\n"
        "• `Grammar / Unit 1`\n\n"
        "📌 *المكتبة:*\n"
        "• 📚 المواد: تعرض كل المواد + عدد الملفات\n"
        "• عند الضغط على مادة: تظهر ملفاتها\n"
        "• اضغط على ملف: تطلع لك لوحة (فتح/مفضلة/نقل/حذف)\n\n"
        "🗂️ *نسخة احتياطية:* ترسل archive.db\n",
        reply_markup=MAIN_KB,
        parse_mode=ParseMode.MARKDOWN,
    )


# ======================
# MENU TEXT HANDLER
# ======================
async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_schema()
    text = (update.message.text or "").strip()

    # رجوع
    if text == "⬅️ رجوع":
        context.user_data.pop("search_mode", None)
        await update.message.reply_text("✅ رجعناك للقائمة الرئيسية", reply_markup=MAIN_KB)
        return

    # أوامر قائمة
    if text == "📚 المواد":
        context.user_data.pop("search_mode", None)
        await update.message.reply_text("📚 *موادك (مع عدد الملفات):*", reply_markup=MAIN_KB, parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text("اضغط مادة 👇", reply_markup=build_subjects_inline(update.effective_user.id))
        return

    if text == "🧾 آخر الملفات":
        context.user_data.pop("search_mode", None)
        rows = list_recent(update.effective_user.id, 10)
        if not rows:
            await update.message.reply_text("✅ ما عندك أرشيف. أرشف أول ملف.", reply_markup=MAIN_KB)
            return
        lines = []
        for r in rows:
            star = "⭐" if r["is_fav"] else "☆"
            lines.append(
                f"{star} #{r['id']} | {r['subject']} | {file_icon(r['file_type'])} {r['file_type']} | "
                f"{short_text(r['file_name'] or r['caption'] or 'بدون وصف', 35)} | {r['created_at'][:10]}"
            )
        await update.message.reply_text("🧾 *آخر الملفات:*\n\n" + "\n".join(lines), reply_markup=MAIN_KB, parse_mode=ParseMode.MARKDOWN)
        return

    if text == "⭐ المفضلة":
        context.user_data.pop("search_mode", None)
        rows = list_favs(update.effective_user.id, 30)
        if not rows:
            await update.message.reply_text("⭐ المفضلة فارغة حاليًا.", reply_markup=MAIN_KB)
            return
        lines = []
        for r in rows:
            lines.append(
                f"⭐ #{r['id']} | {r['subject']} | {file_icon(r['file_type'])} "
                f"{short_text(r['file_name'] or r['caption'] or 'بدون وصف', 40)} | {r['created_at'][:10]}"
            )
        await update.message.reply_text("⭐ *المفضلة:*\n\n" + "\n".join(lines), reply_markup=MAIN_KB, parse_mode=ParseMode.MARKDOWN)
        return

    if text == "🔎 بحث":
        context.user_data["search_mode"] = True
        await update.message.reply_text("🔎 اكتب كلمة البحث الآن (اسم ملف/وصف/مادة)…", reply_markup=MAIN_KB)
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
            await update.message.reply_text("بعد ماكو قاعدة بيانات. أرشف أول ملف حتى تنخلق ✅", reply_markup=MAIN_KB)
        return

    if text == "ℹ️ مساعدة":
        await help_cmd(update, context)
        return

    # وضع البحث
    if context.user_data.get("search_mode"):
        q = text
        rows = search_files(update.effective_user.id, q, 20)
        if not rows:
            await update.message.reply_text(f"ما لقيت نتائج عن: {q}", reply_markup=MAIN_KB)
            return
        msg = [f"🔎 *نتائج البحث عن:* `{q}`\n"]
        for r in rows:
            star = "⭐" if r["is_fav"] else "☆"
            msg.append(
                f"{star} #{r['id']} | {r['subject']} | {file_icon(r['file_type'])} "
                f"{short_text(r['file_name'] or r['caption'] or 'بدون وصف', 45)}"
            )
        await update.message.reply_text("\n".join(msg), reply_markup=MAIN_KB, parse_mode=ParseMode.MARKDOWN)
        return

    # ✅ ميزة: إذا كتب اسم مادة لوحده → ثبّت المادة 10 دقائق
    subj, cap = extract_subject_and_caption(text)
    if subj and cap == "":
        fast_lock_set(context, subj)
        await update.message.reply_text(
            f"✅ *ثبتّت المادة مؤقتًا:* `{subj}`\n"
            f"الآن أرسل/حوّل ملفات… (صالح لمدة {FAST_LOCK_MINUTES} دقائق)\n"
            "إذا تريد وصف: اكتب بالكابشن مثل: `Unit 1 / أصل اللغة`",
            reply_markup=MAIN_KB,
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # أي شيء غير مفهوم
    await update.message.reply_text("اختَر من الأزرار أو اكتب اسم مادة للتثبيت السريع ✅", reply_markup=MAIN_KB)


# ======================
# FILE HANDLER (Smart Save)
# ======================
async def handle_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_schema()
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    message_id = update.message.message_id

    # حاول تحدد subject من:
    # 1) الكابشن بنمط: Subject - Caption
    caption_raw = update.message.caption or ""
    subj_from_caption, cap_after = extract_subject_and_caption(caption_raw)

    # 2) fast lock
    fast_subj = fast_lock_get(context)

    # 3) إذا لا هذا ولا ذاك، خليه Other
    subject = normalize_subject(subj_from_caption or fast_subj or "Other")
    caption_final = cap_after if subj_from_caption else caption_raw

    file_type = None
    file_id = None
    file_unique_id = None
    file_name = ""

    if update.message.document:
        file_type = "document"
        file_id = update.message.document.file_id
        file_unique_id = update.message.document.file_unique_id
        file_name = update.message.document.file_name or ""
    elif update.message.photo:
        file_type = "photo"
        ph = update.message.photo[-1]
        file_id = ph.file_id
        file_unique_id = ph.file_unique_id
        file_name = "photo.jpg"
    elif update.message.video:
        file_type = "video"
        file_id = update.message.video.file_id
        file_unique_id = update.message.video.file_unique_id
        file_name = update.message.video.file_name or "video.mp4"
    elif update.message.audio:
        file_type = "audio"
        file_id = update.message.audio.file_id
        file_unique_id = update.message.audio.file_unique_id
        file_name = update.message.audio.file_name or "audio.mp3"
    elif update.message.voice:
        file_type = "voice"
        file_id = update.message.voice.file_id
        file_unique_id = update.message.voice.file_unique_id
        file_name = "voice.ogg"
    else:
        await update.message.reply_text("هذا النوع حالياً ما أدعمه. أرسل PDF/صورة/فيديو/صوت ✅", reply_markup=MAIN_KB)
        return

    rid = insert_file(
        user_id=user_id,
        chat_id=chat_id,
        message_id=message_id,
        subject=subject,
        file_type=file_type,
        file_id=file_id,
        file_unique_id=file_unique_id,
        file_name=file_name,
        caption=caption_final,
    )

    hint = ""
    if subject == "Other":
        hint = "\n\n💡 *تلميح:* اكتب اسم المادة لوحده لتثبيتها 10 دقائق (مثل: `Linguistics`) أو اكتب بالكابشن: `Linguistics - أصل اللغة`"

    await update.message.reply_text(
        "✅ *تمت الأرشفة بنجاح!*\n\n"
        f"📚 المادة: `{subject}`\n"
        f"{file_icon(file_type)} النوع: `{file_type}`\n"
        f"🆔 رقم الأرشفة: `#{rid}`\n"
        f"📝 الوصف: `{(caption_final or '—')}`"
        f"{hint}",
        reply_markup=build_file_actions_inline(rid, 0),
        parse_mode=ParseMode.MARKDOWN,
    )


# ======================
# CALLBACKS (Inline Buttons)
# ======================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_schema()
    q = update.callback_query
    await q.answer()

    data = (q.data or "")

    # noop
    if data == "noop":
        return

    # nav
    if data == "nav:home":
        context.user_data.pop("last_view", None)
        await q.edit_message_text("🏠 رجعناك للرئيسية ✅")
        await q.message.reply_text("اختر من القائمة 👇", reply_markup=MAIN_KB)
        return

    if data == "nav:subjects":
        context.user_data["last_view"] = ("subjects", None)
        await q.edit_message_text("📚 موادك (مع عدد الملفات):")
        await q.message.reply_text("اضغط مادة 👇", reply_markup=build_subjects_inline(update.effective_user.id))
        return

    if data == "nav:back":
        # يرجع لآخر عرض (مادة أو مواد)
        last = context.user_data.get("last_view")
        if not last:
            await q.edit_message_text("✅ رجوع")
            return
        view, payload = last
        if view == "subjects":
            await q.edit_message_text("📚 موادك (مع عدد الملفات):")
            await q.message.reply_text("اضغط مادة 👇", reply_markup=build_subjects_inline(update.effective_user.id))
            return
        if view == "subject_files" and payload:
            subj, page = payload
            items, total = list_by_subject(update.effective_user.id, subj, page)
            await q.edit_message_text(render_subject_files_text(subj, items, page, total))
            await q.message.reply_text("اختر ملف/أو تنقل 👇", reply_markup=build_files_list_inline(subj, page, total))
            return
        await q.edit_message_text("✅ رجوع")
        return

    # fast lock from inline
    if data.startswith("fast:"):
        subj = data.split(":", 1)[1]
        fast_lock_set(context, subj)
        await q.edit_message_text(
            f"✅ ثبتّت المادة مؤقتًا: {subj}\n"
            f"ارسل/حوّل ملفات الآن… (صالح {FAST_LOCK_MINUTES} دقائق)"
        )
        return

    # open subject page
    if data.startswith("subj:"):
        _, subj, page_s = data.split(":")
        page = int(page_s)
        items, total = list_by_subject(update.effective_user.id, subj, page)
        context.user_data["last_view"] = ("subject_files", (subj, page))
        await q.edit_message_text(render_subject_files_text(subj, items, page, total))
        await q.message.reply_text("اختر ملف/أو تنقل 👇", reply_markup=build_files_list_inline(subj, page, total))
        return

    # file actions
    if data.startswith("open:"):
        rid = int(data.split(":")[1])
        row = get_file(update.effective_user.id, rid)
        if not row:
            await q.edit_message_text("هذا الملف غير موجود (يمكن محذوف).")
            return

        await send_file_to_user(q.message, row)
        await q.message.reply_text(
            f"✅ هذا ملفك من الأرشيف: #{rid}\n"
            f"📚 {row['subject']} | {file_icon(row['file_type'])} {row['file_type']}\n"
            f"📝 {row['caption'] or '—'}",
        )
        return

    if data.startswith("fav:"):
        _, rid_s, val_s = data.split(":")
        rid = int(rid_s)
        val = int(val_s)
        set_fav(update.effective_user.id, rid, val)
        row = get_file(update.effective_user.id, rid)
        if not row:
            await q.edit_message_text("تم التعديل ✅ (لكن الملف غير موجود الآن).")
            return
        await q.edit_message_text(
            "✅ تم التحديث\n\n"
            f"📚 المادة: {row['subject']}\n"
            f"{file_icon(row['file_type'])} النوع: {row['file_type']}\n"
            f"🆔 #{row['id']}\n"
            f"⭐ المفضلة: {'نعم' if val else 'لا'}\n"
            f"📝 الوصف: {row['caption'] or '—'}",
            reply_markup=build_file_actions_inline(rid, val),
        )
        return

    if data.startswith("del:"):
        rid = int(data.split(":")[1])
        delete_file(update.effective_user.id, rid)
        await q.edit_message_text("🗑️ تم حذف الملف من الأرشيف ✅")
        return

    if data.startswith("move:"):
        rid = int(data.split(":")[1])
        row = get_file(update.effective_user.id, rid)
        if not row:
            await q.edit_message_text("الملف غير موجود.")
            return
        await q.edit_message_text(
            f"🏷️ اختر المادة الجديدة للملف #{rid}\n(الحالية: {row['subject']})",
            reply_markup=build_move_subjects_inline(rid),
        )
        return

    if data.startswith("mset:"):
        _, rid_s, new_subj = data.split(":", 2)
        rid = int(rid_s)
        new_subj = normalize_subject(new_subj)
        move_file(update.effective_user.id, rid, new_subj)
        row = get_file(update.effective_user.id, rid)
        await q.edit_message_text(
            f"✅ تم نقل الملف #{rid} إلى مادة: {new_subj}\n"
            f"📝 الوصف: {row['caption'] or '—'}",
            reply_markup=build_file_actions_inline(rid, int(row["is_fav"]) if row else 0),
        )
        return


def render_subject_files_text(subject: str, items: List[sqlite3.Row], page: int, total: int) -> str:
    if total == 0:
        return f"📘 *{subject}*\n\nماكو ملفات بهالمادة بعد.\n\nاضغط (➕ أرشفة هنا) أو ثبت المادة واكتب اسمها ثم ارسل ملفات."
    last_page = max((total - 1) // PAGE_SIZE, 0)
    header = f"📘 *{subject}* — (صفحة {page+1}/{last_page+1})\n"
    lines = []
    for r in items:
        star = "⭐" if r["is_fav"] else "☆"
        title = r["file_name"] or r["caption"] or "بدون وصف"
        lines.append(
            f"{star} `#{r['id']}` {file_icon(r['file_type'])} {short_text(title, 45)} — {r['created_at'][:10]}\n"
            f"   ↳ اضغط: /open_{r['id']}"
        )
    return header + "\n".join(lines)


async def send_file_to_user(message, row: sqlite3.Row):
    # يرسل نفس الملف عبر file_id
    ftype = row["file_type"]
    fid = row["file_id"]
    cap = row["caption"] or ""
    name = row["file_name"] or ""

    if ftype == "document":
        await message.reply_document(document=fid, caption=cap or name or "")
    elif ftype == "photo":
        await message.reply_photo(photo=fid, caption=cap or "")
    elif ftype == "video":
        await message.reply_video(video=fid, caption=cap or name or "")
    elif ftype == "audio":
        await message.reply_audio(audio=fid, caption=cap or name or "")
    elif ftype == "voice":
        await message.reply_voice(voice=fid, caption=cap or "")
    else:
        # fallback
        await message.reply_document(document=fid, caption=cap or name or "")


# ======================
# Optional: /open_# command style
# ======================
async def open_by_id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # يسمح تكتب /open_123
    m = re.match(r"^/open_(\d+)$", (update.message.text or "").strip())
    if not m:
        return
    rid = int(m.group(1))
    row = get_file(update.effective_user.id, rid)
    if not row:
        await update.message.reply_text("هذا الملف غير موجود.")
        return
    await send_file_to_user(update.message, row)


# ======================
# MAIN
# ======================
def main():
    ensure_schema()
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it as an environment variable.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))

    app.add_handler(CallbackQueryHandler(on_callback))

    # ملفات
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

    # نصوص + أوامر فتح /open_123
    app.add_handler(MessageHandler(filters.Regex(r"^/open_\d+$"), open_by_id_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_text))

    print("Bot is running...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()