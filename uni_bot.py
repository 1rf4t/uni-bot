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
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================== Config ==================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "archive.db").strip()

# مدة "جلسة المادة" لما تكتب اسم المادة لوحدها ثم ترسل ملفات
SUBJECT_SESSION_MINUTES = 10

SUBJECTS = [
    "Grammar", "Phonetics", "Poetry", "Drama", "Novel",
    "Listening", "Writing", "Linguistics", "ELT", "Other"
]

# Map للاسماء/الاختصارات الشائعة (تقدر تضيف لاحقاً)
ALIASES = {
    "ling": "Linguistics",
    "linguistic": "Linguistics",
    "phon": "Phonetics",
    "gram": "Grammar",
    "drama": "Drama",
    "poem": "Poetry",
    "poetry": "Poetry",
    "elt": "ELT",
}

# ================== UI ==================
MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📚 المواد"), KeyboardButton("🗂️ نسخة احتياطية")],
        [KeyboardButton("🧾 آخر الملفات"), KeyboardButton("⭐ المفضلة")],
        [KeyboardButton("🔎 بحث"), KeyboardButton("ℹ️ مساعدة")],
    ],
    resize_keyboard=True,
)

def subjects_keyboard():
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

def materials_menu_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("➕ أرشفة حسب مادة"), KeyboardButton("📂 عرض موادّي")],
            [KeyboardButton("⬅️ رجوع")],
        ],
        resize_keyboard=True,
    )

# ================== DB ==================
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
            file_name TEXT,
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
    file_name: str,
    caption: Optional[str],
) -> int:
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO files (user_id, chat_id, message_id, subject, file_type, file_id, file_unique_id, file_name, caption, created_at)
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
        SELECT id, subject, file_type, file_name, caption, created_at, is_fav
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

def list_favs(user_id: int, limit: int = 20):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, file_type, file_name, caption, created_at, is_fav
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
    q = (q or "").strip().lower()
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, file_type, file_name, caption, created_at, is_fav
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

def count_by_subject(user_id: int):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT subject, COUNT(*)
        FROM files
        WHERE user_id = ?
        GROUP BY subject
        ORDER BY COUNT(*) DESC, subject ASC
        """,
        (user_id,),
    )
    rows = cur.fetchall()
    con.close()
    return rows

def list_by_subject(user_id: int, subject: str, limit: int = 10, offset: int = 0):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, file_type, file_name, caption, created_at, is_fav
        FROM files
        WHERE user_id = ? AND subject = ?
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, subject, limit, offset),
    )
    rows = cur.fetchall()
    con.close()
    return rows

# ================== Helpers ==================
def normalize_subject(s: str) -> Optional[str]:
    if not s:
        return None
    s0 = s.strip()
    if not s0:
        return None

    key = s0.lower().strip()
    if key in ALIASES:
        return ALIASES[key]

    # تطابق مباشر مع SUBJECTS
    for subj in SUBJECTS:
        if key == subj.lower():
            return subj

    # لو كتب بالعربي / ايموجي / خربطة بسيطة: نحاول نقتنص كلمة انجليزية
    # مثال: "Linguistics - أصل اللغة"
    m = re.match(r"^\s*([A-Za-z]+)\s*(?:[-:|]\s*)?.*$", s0)
    if m:
        cand = m.group(1).lower()
        if cand in ALIASES:
            return ALIASES[cand]
        for subj in SUBJECTS:
            if cand == subj.lower():
                return subj

    return None

def extract_subject_from_caption(caption: str) -> Tuple[Optional[str], str]:
    """
    يقبل:
    - Linguistics - أصل اللغة
    - Linguistics: أصل اللغة
    - Linguistics | أصل اللغة
    - Linguistics
    """
    if not caption:
        return None, ""

    cap = caption.strip()
    # خذ أول سطر فقط للفحص
    first_line = cap.splitlines()[0].strip()

    subj = normalize_subject(first_line)
    if subj:
        # إذا الكابشن فيه فاصل بعد اسم المادة، احذف اسم المادة فقط وخلي الوصف
        # Linguistics - أصل اللغة => "أصل اللغة"
        rest = re.sub(r"^\s*[A-Za-z]+\s*([-:|])?\s*", "", first_line).strip()
        # الوصف النهائي: باقي الكابشن (مع السطور) لكن بدون تكرار اول سطر لو كان مادة فقط
        if len(cap.splitlines()) > 1:
            tail = "\n".join(cap.splitlines()[1:]).strip()
            final_desc = (rest + ("\n" + tail if tail else "")).strip()
        else:
            final_desc = rest
        return subj, final_desc

    return None, cap

def pretty_row(row) -> str:
    # row: (id, subject, file_type, file_name, caption, created_at, is_fav)
    fid, subj, ftype, fname, cap, created, fav = row
    star = "⭐" if fav else "☆"
    cap = (cap or "").strip()
    fname = (fname or "").strip()
    if len(cap) > 45:
        cap = cap[:45] + "…"
    if len(fname) > 30:
        fname = fname[:30] + "…"
    date = created.split("T")[0] if created else ""
    # مثال: ⭐ #12 | Linguistics | document | linguistics.pdf | أصل اللغة | 2026-01-29
    return f"{star} #{fid} | {subj} | {ftype} | {fname} | {cap or '—'} | {date}"

def set_subject_session(context: ContextTypes.DEFAULT_TYPE, subject: str):
    context.user_data["quick_subject"] = subject
    context.user_data["quick_subject_until"] = (datetime.utcnow() + timedelta(minutes=SUBJECT_SESSION_MINUTES)).isoformat()

def get_subject_session(context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    subj = context.user_data.get("quick_subject")
    until = context.user_data.get("quick_subject_until")
    if not subj or not until:
        return None
    try:
        exp = datetime.fromisoformat(until)
    except Exception:
        return None
    if datetime.utcnow() > exp:
        context.user_data.pop("quick_subject", None)
        context.user_data.pop("quick_subject_until", None)
        return None
    return subj

def archive_mode(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.user_data.get("awaiting_file", False))

# ================== Commands ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "يا هلا رأفت 👋\n"
        "أنا بوت الأرشفة الذكي 📚\n\n"
        "تقدر تأرشف بطريقتين:\n"
        "1) من الأزرار: 📚 المواد → اختر مادة → أرسل الملف.\n"
        "2) الأسرع: اكتب اسم المادة لوحده (مثلاً: Linguistics) ثم أرسل/حوّل ملفات بعدها.\n"
        "   (يبقى ثابت 10 دقائق)\n\n"
        "اختر من القائمة 👇",
        reply_markup=MAIN_KB,
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ المساعدة:\n\n"
        "✅ الأرشفة (3 طرق):\n"
        "1) 📚 المواد → ➕ أرشفة حسب مادة → اختر مادة → أرسل الملف.\n"
        "2) اكتب اسم المادة لوحده (مثلاً: Linguistics) ثم أرسل/حوّل ملفات (10 دقائق).\n"
        "3) اكتب المادة بالكابشن:\n"
        "   Linguistics - أصل اللغة\n\n"
        "📂 عرض المواد:\n"
        "📚 المواد → 📂 عرض موادّي → اضغط مادة.\n\n"
        "⭐ المفضلة: بعد الأرشفة اضغط ⭐ إضافة/إزالة.\n"
        "🔎 البحث: يفتش بالعنوان/الوصف/اسم الملف.\n\n"
        "ملاحظة: البوت يخزن بيانات الملف في SQLite، والملف نفسه يبقى على تيليجرام عبر file_id.",
        reply_markup=MAIN_KB,
    )

# ================== Menus/Text ==================
async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # رجوع
    if text == "⬅️ رجوع":
        context.user_data.pop("awaiting_file", None)
        context.user_data.pop("subject", None)
        context.user_data.pop("search_mode", None)
        await update.message.reply_text("رجعناك للقائمة الرئيسية ✅", reply_markup=MAIN_KB)
        return

    # 0) إذا المستخدم كتب اسم مادة لوحدها => فعّل جلسة سريعة
    subj_try = normalize_subject(text)
    if subj_try and text.lower() == subj_try.lower() or subj_try and text.lower() in ALIASES:
        set_subject_session(context, subj_try)
        await update.message.reply_text(
            f"✅ ثبتّيت المادة مؤقتاً: {subj_try}\n"
            f"الآن أرسل/حوّل ملفات… (صالح لمدة {SUBJECT_SESSION_MINUTES} دقائق)\n"
            "إذا تريد وصف، اكتب بالكابشن مثل: Unit 1 / أصل اللغة",
            reply_markup=MAIN_KB,
        )
        return

    # قائمة المواد
    if text == "📚 المواد":
        context.user_data.pop("search_mode", None)
        await update.message.reply_text("تريد أرشفة لو عرض المواد؟ 👇", reply_markup=materials_menu_keyboard())
        return

    if text == "➕ أرشفة حسب مادة":
        context.user_data.pop("search_mode", None)
        await update.message.reply_text("اختر مادة للأرشفة 👇", reply_markup=subjects_keyboard())
        return

    if text == "📂 عرض موادّي":
        context.user_data.pop("search_mode", None)
        rows = count_by_subject(update.effective_user.id)
        if not rows:
            await update.message.reply_text(" ما عندك أرشيف. أرشف أول ملف ✅", reply_markup=MAIN_KB)
            return

        buttons = []
        for subj, cnt in rows:
            buttons.append([InlineKeyboardButton(f"📘 {subj} ({cnt})", callback_data=f"subj:{subj}:0")])

        await update.message.reply_text(
            "📂 موادّك وعدد الملفات بكل مادة:\nاضغط مادة لعرض ملفاتها 👇",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    # اختيار مادة من الكيبورد
    if text.startswith("📘 "):
        subject = text.replace("📘 ", "").strip()
        if subject not in SUBJECTS:
            subject = "Other"
        context.user_data["subject"] = subject
        context.user_data["awaiting_file"] = True
        context.user_data.pop("search_mode", None)

        await update.message.reply_text(
            f"تمام ✅\nالمادة الحالية للأرشفة: **{subject}**\n\n"
            "الآن أرسل/حوّل ملف…\n"
            "و(اختياري) اكتب وصف بالكابشن مثل:\n"
            "`Unit 1` أو `أصل اللغة`",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("⬅️ رجوع")]], resize_keyboard=True),
            parse_mode="Markdown",
        )
        return

    # آخر الملفات
    if text == "🧾 آخر الملفات":
        context.user_data.pop("search_mode", None)
        rows = list_recent(update.effective_user.id, 10)
        if not rows:
            await update.message.reply_text("لسّا ما عندك ملفات مؤرشفة. ابدأ من 📚 المواد ✅", reply_markup=MAIN_KB)
            return
        msg = "🧾 آخر الملفات:\n\n" + "\n".join(pretty_row(r) for r in rows)
        await update.message.reply_text(msg, reply_markup=MAIN_KB)
        return

    # المفضلة
    if text == "⭐ المفضلة":
        context.user_data.pop("search_mode", None)
        rows = list_favs(update.effective_user.id, 20)
        if not rows:
            await update.message.reply_text("المفضلة فارغة ⭐\nبعد ما تؤرشف ملف، اضغط ⭐ إضافة للمفضلة.", reply_markup=MAIN_KB)
            return
        msg = "⭐ المفضلة:\n\n" + "\n".join(pretty_row(r) for r in rows)
        await update.message.reply_text(msg, reply_markup=MAIN_KB)
        return

    # البحث
    if text == "🔎 بحث":
        context.user_data["search_mode"] = True
        await update.message.reply_text("اكتب كلمة البحث الآن (مثلاً: unit 1 أو linguistics أو اسم ملف) 🔎", reply_markup=MAIN_KB)
        return

    # نسخة احتياطية
    if text == "🗂️ نسخة احتياطية":
        if os.path.exists(DB_PATH):
            await update.message.reply_document(
                document=open(DB_PATH, "rb"),
                filename=os.path.basename(DB_PATH),
                caption="🗂️ نسخة احتياطية لقاعدة البيانات (SQLite). احتفظ بها بمكان آمن.",
                reply_markup=MAIN_KB,
            )
        else:
            await update.message.reply_text("ماكو قاعدة بيانات بعد. أرشف أول ملف حتى تنخلق ✅", reply_markup=MAIN_KB)
        return

    # مساعدة
    if text == "ℹ️ مساعدة":
        await help_cmd(update, context)
        return

    # وضع البحث
    if context.user_data.get("search_mode"):
        q = text
        rows = search_files(update.effective_user.id, q, 20)
        if not rows:
            await update.message.reply_text(f"ما لقيت شي عن: {q}", reply_markup=MAIN_KB)
            return
        msg = f"🔎 نتائج البحث عن: {q}\n\n" + "\n".join(pretty_row(r) for r in rows)
        await update.message.reply_text(msg, reply_markup=MAIN_KB)
        return

    # غير مفهوم
    await update.message.reply_text(
        "ما فهمت قصدك 😅\n"
        "استخدم الأزرار أو اكتب اسم مادة لوحدها (مثل: Linguistics) ✅",
        reply_markup=MAIN_KB,
    )

# ================== Files Handler ==================
async def handle_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    message_id = update.message.message_id
    caption_raw = update.message.caption or ""

    # 1) استخراج مادة من الكابشن إن موجودة
    subj_from_caption, caption_clean = extract_subject_from_caption(caption_raw)

    # 2) إذا ماكو مادة بالكابشن، جرب جلسة المادة السريعة (بعد ما يكتب Linguistics لوحده)
    subj_from_session = get_subject_session(context)

    # 3) إذا ماكو هذا، جرب وضع اختيار المادة من القائمة
    subj_from_menu = context.user_data.get("subject") if archive_mode(context) else None

    subject = subj_from_caption or subj_from_session or subj_from_menu or "Other"
    if subject not in SUBJECTS:
        subject = "Other"

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
        file_name = update.message.audio.file_name or "audio"
    elif update.message.voice:
        file_type = "voice"
        file_id = update.message.voice.file_id
        file_unique_id = update.message.voice.file_unique_id
        file_name = "voice.ogg"
    else:
        await update.message.reply_text("هذا النوع حالياً ما أدعمه. أرسل ملف/صورة/فيديو/صوت ✅", reply_markup=MAIN_KB)
        return

    row_id = insert_file(
        user_id=user_id,
        chat_id=chat_id,
        message_id=message_id,
        subject=subject,
        file_type=file_type,
        file_id=file_id,
        file_unique_id=file_unique_id,
        file_name=file_name,
        caption=caption_clean,
    )

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⭐ إضافة للمفضلة", callback_data=f"fav:{row_id}:1"),
                InlineKeyboardButton("☆ إزالة", callback_data=f"fav:{row_id}:0"),
            ],
            [
                InlineKeyboardButton("📂 عرض ملفات المادة", callback_data=f"subj:{subject}:0"),
            ]
        ]
    )

    hint = ""
    if subject == "Other":
        hint = (
            "\n\n💡 تلميح: حتى ما ينحفظ على Other:\n"
            "- اكتب اسم المادة لوحده (مثلاً: Linguistics) ثم أرسل/حوّل ملفات.\n"
            "- أو اكتب بالكابشن: Linguistics - أصل اللغة"
        )

    await update.message.reply_text(
        "✅ تمت الأرشفة بنجاح!\n\n"
        f"📚 المادة: {subject}\n"
        f"📦 النوع: {file_type}\n"
        f"🆔 رقم الأرشفة: #{row_id}\n"
        f"📝 الوصف: {caption_clean or '—'}"
        f"{hint}",
        reply_markup=kb,
    )

# ================== Callback ==================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = (q.data or "")

    # المفضلة
    if data.startswith("fav:"):
        _, rid, val = data.split(":")
        rid_i = int(rid)
        val_i = int(val)
        set_fav(update.effective_user.id, rid_i, val_i)
        if val_i == 1:
            await q.edit_message_text(q.message.text + "\n\n⭐ تمّت الإضافة للمفضلة.")
        else:
            await q.edit_message_text(q.message.text + "\n\n☆ تمّت الإزالة من المفضلة.")
        return

    # عرض مادة + صفحات
    if data.startswith("subj:"):
        _, subject, off = data.split(":")
        offset = int(off)
        limit = 10

        rows = list_by_subject(update.effective_user.id, subject, limit=limit, offset=offset)
        if not rows:
            await q.edit_message_text(f"📘 {subject}\nماكو ملفات بعد.")
            return

        page = offset // limit + 1
        msg = f"📘 {subject} — ملفات (صفحة {page})\n\n" + "\n".join(pretty_row(r) for r in rows)

        nav = []
        if offset > 0:
            nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"subj:{subject}:{max(0, offset-limit)}"))
        if len(rows) == limit:
            nav.append(InlineKeyboardButton("التالي ➡️", callback_data=f"subj:{subject}:{offset+limit}"))

        kb = InlineKeyboardMarkup([nav] if nav else [])
        await q.edit_message_text(msg, reply_markup=kb)
        return

# ================== Main ==================
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it as an environment variable (BOT_TOKEN).")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))

    app.add_handler(CallbackQueryHandler(on_callback))

    # نصوص
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_text))

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

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()