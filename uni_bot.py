import os
import re
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Any, List

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
DB_PATH = os.getenv("DB_PATH", "archive.db")

# آخر مادة اختارها/كتبها المستخدم تبقى “فعّالة” لهذه المدة إذا أرسل ملف بدون Caption
SUBJECT_STICKY_MINUTES = 30

# عدد العناصر في القوائم
RECENT_LIMIT = 10
SUBJECT_LIST_LIMIT = 30
FAVS_LIMIT = 30
SEARCH_LIMIT = 30

# ================== Logging ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("raafat-archive-bot")

# ================== UI ==================
MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📤 أرشفة سريعة"), KeyboardButton("📚 المواد")],
        [KeyboardButton("📂 ملفات مادة"), KeyboardButton("🧾 آخر الملفات")],
        [KeyboardButton("⭐ المفضلة"), KeyboardButton("🔎 بحث")],
        [KeyboardButton("🗂️ نسخة احتياطية"), KeyboardButton("ℹ️ مساعدة")],
    ],
    resize_keyboard=True,
)

SUBJECTS = [
    "Grammar", "Phonetics", "Poetry", "Drama", "Novel",
    "Listening", "Writing", "Linguistics", "ELT", "Other"
]

# aliases: اكتبها بأي شكل والبوت يفهم
ALIASES = {
    "grammar": "Grammar",
    "gram": "Grammar",
    "phonetics": "Phonetics",
    "phonet": "Phonetics",
    "poetry": "Poetry",
    "poem": "Poetry",
    "drama": "Drama",
    "novel": "Novel",
    "listening": "Listening",
    "listen": "Listening",
    "writing": "Writing",
    "write": "Writing",
    "linguistics": "Linguistics",
    "ling": "Linguistics",
    "elt": "ELT",
    "other": "Other",
}

def subjects_inline(prefix: str = "picksubj") -> InlineKeyboardMarkup:
    # callback_data: f"{prefix}:{SubjectName}"
    rows = []
    row = []
    for i, s in enumerate(SUBJECTS, 1):
        row.append(InlineKeyboardButton(f"📘 {s}", callback_data=f"{prefix}:{s}"))
        if i % 2 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ إلغاء", callback_data=f"{prefix}:__cancel__")])
    return InlineKeyboardMarkup(rows)

def subjects_reply_kb() -> ReplyKeyboardMarkup:
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

# ================== DB ==================
def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
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
    # فهارس بسيطة لتسريع البحث
    con.execute("CREATE INDEX IF NOT EXISTS idx_files_user_id ON files(user_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_files_subject ON files(user_id, subject)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_files_fav ON files(user_id, is_fav)")
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
    file_name: Optional[str],
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
            caption or "",
            datetime.utcnow().isoformat(),
        ),
    )
    con.commit()
    new_id = cur.lastrowid
    con.close()
    return new_id

def list_recent(user_id: int, limit: int = RECENT_LIMIT):
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

def list_subject(user_id: int, subject: str, limit: int = SUBJECT_LIST_LIMIT):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, subject, file_type, file_name, caption, created_at, is_fav
        FROM files
        WHERE user_id = ? AND subject = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, subject, limit),
    )
    rows = cur.fetchall()
    con.close()
    return rows

def list_favs(user_id: int, limit: int = FAVS_LIMIT):
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

def search_files(user_id: int, q: str, limit: int = SEARCH_LIMIT):
    q = q.strip().lower()
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

# ================== Subject detection ==================
def normalize_text(s: str) -> str:
    s = (s or "").strip()
    s = s.replace("📘", "").replace("📚", "").replace("✅", "").replace("⭐", "")
    s = s.replace("—", "-")
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def detect_subject(text: str) -> Tuple[Optional[str], str]:
    """
    Returns: (subject or None, cleaned_caption_without_subject_prefix)
    """
    t = normalize_text(text)
    if not t:
        return None, ""

    # remove leading hashtags (#linguistics ...)
    t2 = re.sub(r"^#+", "", t)

    # split by common separators: -, :, |, newline
    parts = re.split(r"[\n\|\:]+", t2, maxsplit=1)
    head = parts[0].strip()

    # also consider "subject - rest"
    head2, rest = head, ""
    if " - " in head:
        head2, rest = head.split(" - ", 1)
    elif "-" in head and len(head.split("-", 1)[0]) <= 20:
        head2, rest = head.split("-", 1)

    head_key = re.sub(r"[^a-zA-Z]", "", head2).lower().strip()

    # direct match subjects (case-insensitive)
    for s in SUBJECTS:
        if head2.strip().lower() == s.lower():
            cleaned =