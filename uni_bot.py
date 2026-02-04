#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sqlite3
import shutil
import hashlib
from pathlib import Path
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

# ============================================================
# CONFIG (Railway / Any host via ENV)
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN غير مضبوط")

DB_PATH = os.getenv("DB_PATH", "/data/archive.db").strip()
FILES_DIR = os.getenv("FILES_DIR", "/data/files").strip()
BACKUP_DIR = os.getenv("BACKUP_DIR", "/data/backups").strip()

SEED_DB_PATH = os.getenv("SEED_DB_PATH", "").strip()

OWNER_ID = int(os.getenv("OWNER_ID", "0"))

LIBRARY_ID_ENV = os.getenv("LIBRARY_ID", "").strip()
LIBRARY_ID = int(LIBRARY_ID_ENV) if LIBRARY_ID_ENV.isdigit() else 0

ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS = set()
if ADMIN_IDS_RAW:
    for x in ADMIN_IDS_RAW.split(","):
        x = x.strip()
        if x.isdigit():
            ADMIN_IDS.add(int(x))
if OWNER_ID:
    ADMIN_IDS.add(OWNER_ID)

AUTO_BACKUP_MINUTES = int(os.getenv("AUTO_BACKUP_MINUTES", "60"))
BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", "30"))
SILENT_BACKUP_TO_OWNER = os.getenv("SILENT_BACKUP_TO_OWNER", "false").strip().lower() == "true"
TRASH_RETENTION_DAYS = int(os.getenv("TRASH_RETENTION_DAYS", "30"))

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

MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📚 المواد"), KeyboardButton("🧾 آخر الملفات")],
        [KeyboardButton("⭐ المفضلة"), KeyboardButton("🔎 بحث")],
        [KeyboardButton("📦 نسخة احتياطية"), KeyboardButton("ℹ️ مساعدة")],
    ],
    resize_keyboard=True,
)

# ============================================================
# UTIL
# ============================================================
def utcnow_str():
    return datetime.utcnow().isoformat(timespec="seconds")

def ensure_dirs():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(FILES_DIR).mkdir(parents=True, exist_ok=True)
    Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def safe_filename(name: str, fallback: str) -> str:
    name = (name or "").strip()
    if not name:
        return fallback
    name = re.sub(r"[^\w\-. ()\[\]{}]+", "_", name, flags=re.UNICODE)
    name = name.strip(" ._")
    return name or fallback

def normalize_subject(text: str):
    t = (text or "").strip()
    for s in SUBJECTS:
        if t.lower() == s.lower():
            return s
    return None

def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

# ============================================================
# DB
# ============================================================
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    # تحسين بسيط للأداء
    try:
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute("PRAGMA temp_store=MEMORY;")
    except Exception:
        pass
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
            tg_unique_id TEXT,
            filename TEXT,
            caption TEXT,
            local_path TEXT,
            file_size INTEGER,
            content_hash TEXT,                 -- ✅ NEW: SHA256
            added_at TEXT NOT NULL,
            is_fav INTEGER NOT NULL DEFAULT 0,
            is_deleted INTEGER NOT NULL DEFAULT 0,
            deleted_at TEXT
        );
        """
    )

    # Migration
    try:
        cur.execute("PRAGMA table_info(files)")
        cols = {row[1] for row in cur.fetchall()}

        if "local_path" not in cols:
            cur.execute("ALTER TABLE files ADD COLUMN local_path TEXT")
        if "is_fav" not in cols:
            cur.execute("ALTER TABLE files ADD COLUMN is_fav INTEGER NOT NULL DEFAULT 0")
        if "is_deleted" not in cols:
            cur.execute("ALTER TABLE files ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0")
        if "deleted_at" not in cols:
            cur.execute("ALTER TABLE files ADD COLUMN deleted_at TEXT")
        if "tg_unique_id" not in cols:
            cur.execute("ALTER TABLE files ADD COLUMN tg_unique_id TEXT")
        if "file_size" not in cols:
            cur.execute("ALTER TABLE files ADD COLUMN file_size INTEGER")
        if "content_hash" not in cols:
            cur.execute("ALTER TABLE files ADD COLUMN content_hash TEXT")
    except Exception:
        pass

    cur.execute("CREATE INDEX IF NOT EXISTS idx_files_user_subject ON files(user_id, subject);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_files_user_added ON files(user_id, added_at);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_files_deleted ON files(user_id, is_deleted);")

    # قديم: unique by tg_unique_id (يبقى مفيد)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_files_user_unique ON files(user_id, tg_unique_id);")
    # ✅ جديد: unique by file content hash (هذا اللي يحل مشكلتك نهائيًا)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_files_user_hash ON files(user_id, content_hash);")

    con.commit()
    con.close()

def db_has_data() -> bool:
    try:
        con = db()
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM files")
        n = cur.fetchone()[0]
        con.close()
        return n > 0
    except Exception:
        return False

def seed_db_if_needed():
    if not SEED_DB_PATH:
        return
    if db_has_data():
        return
    seed = Path(SEED_DB_PATH)
    if seed.exists() and seed.is_file() and seed.stat().st_size > 10_000:
        shutil.copy2(str(seed), DB_PATH)

def _has_is_deleted_column() -> bool:
    try:
        con = db()
        cur = con.cursor()
        cur.execute("PRAGMA table_info(files)")
        cols = {row[1] for row in cur.fetchall()}
        con.close()
        return "is_deleted" in cols
    except Exception:
        return False

def detect_library_id_legacy() -> int:
    try:
        con = db()
        cur = con.cursor()
        cur.execute("PRAGMA table_info(files)")
        cols = {row[1] for row in cur.fetchall()}
        has_deleted = "is_deleted" in cols

        if OWNER_ID:
            if has_deleted:
                cur.execute("SELECT COUNT(*) FROM files WHERE user_id=? AND is_deleted=0", (OWNER_ID,))
            else:
                cur.execute("SELECT COUNT(*) FROM files WHERE user_id=?", (OWNER_ID,))
            if cur.fetchone()[0] > 0:
                con.close()
                return OWNER_ID

        if has_deleted:
            cur.execute("""
                SELECT user_id, COUNT(*) AS cnt
                FROM files
                WHERE is_deleted=0
                GROUP BY user_id
                ORDER BY cnt DESC
                LIMIT 1
            """)
        else:
            cur.execute("""
                SELECT user_id, COUNT(*) AS cnt
                FROM files
                GROUP BY user_id
                ORDER BY cnt DESC
                LIMIT 1
            """)

        row = cur.fetchone()
        con.close()
        if row:
            return int(row[0])
        return 0
    except Exception:
        return 0

def library_has_any_files(user_id: int) -> bool:
    try:
        con = db()
        cur = con.cursor()
        if _has_is_deleted_column():
            cur.execute("SELECT COUNT(*) FROM files WHERE user_id=? AND is_deleted=0", (user_id,))
        else:
            cur.execute("SELECT COUNT(*) FROM files WHERE user_id=?", (user_id,))
        n = cur.fetchone()[0]
        con.close()
        return n > 0
    except Exception:
        return False

def get_file_by_unique(user_id: int, tg_unique_id: str):
    if not tg_unique_id:
        return None
    con = db()
    cur = con.cursor()
    cur.execute("SELECT * FROM files WHERE user_id=? AND tg_unique_id=? LIMIT 1", (user_id, tg_unique_id))
    row = cur.fetchone()
    con.close()
    return row

def get_file_by_hash(user_id: int, content_hash: str):
    if not content_hash:
        return None
    con = db()
    cur = con.cursor()
    cur.execute("SELECT * FROM files WHERE user_id=? AND content_hash=? LIMIT 1", (user_id, content_hash))
    row = cur.fetchone()
    con.close()
    return row

def add_file_row(
    user_id: int,
    subject: str,
    file_type: str,
    tg_file_id: str,
    tg_unique_id: str | None,
    filename: str | None,
    caption: str | None,
    local_path: str | None,
    file_size: int | None,
    content_hash: str | None,
):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO files (user_id, subject, file_type, tg_file_id, tg_unique_id, filename, caption, local_path, file_size, content_hash, added_at, is_fav, is_deleted)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
        """,
        (user_id, subject, file_type, tg_file_id, tg_unique_id, filename, caption, local_path, file_size, content_hash, utcnow_str()),
    )
    con.commit()
    new_id = cur.lastrowid
    con.close()
    return new_id

def update_existing_file_from_duplicate(user_id: int, existing_id: int, tg_file_id: str, filename: str | None, caption: str | None, local_path: str | None, file_size: int | None, content_hash: str | None):
    con = db()
    cur = con.cursor()
    cur.execute(
        """
        UPDATE files
        SET tg_file_id=?,
            filename=COALESCE(?, filename),
            caption=COALESCE(?, caption),
            local_path=COALESCE(?, local_path),
            file_size=COALESCE(?, file_size),
            content_hash=COALESCE(?, content_hash),
            is_deleted=0,
            deleted_at=NULL
        WHERE user_id=? AND id=?
        """,
        (tg_file_id, filename, caption, local_path, file_size, content_hash, user_id, existing_id),
    )
    con.commit()
    con.close()

def count_by_subject(user_id: int):
    con = db()
    cur = con.cursor()
    if _has_is_deleted_column():
        cur.execute("SELECT subject, COUNT(*) cnt FROM files WHERE user_id=? AND is_deleted=0 GROUP BY subject", (user_id,))
    else:
        cur.execute("SELECT subject, COUNT(*) cnt FROM files WHERE user_id=? GROUP BY subject", (user_id,))
    rows = cur.fetchall()
    con.close()
    return [(r[0], r[1]) for r in rows]

def list_files_by_subject(user_id: int, subject: str, limit: int = 50):
    con = db()
    cur = con.cursor()
    if _has_is_deleted_column():
        cur.execute(
            """
            SELECT id, file_type, filename, caption, added_at, is_fav
            FROM files
            WHERE user_id=? AND subject=? AND is_deleted=0
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, subject, limit),
        )
    else:
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
    cur.execute("SELECT * FROM files WHERE user_id=? AND id=?", (user_id, file_id))
    row = cur.fetchone()
    con.close()
    return row

def set_fav(user_id: int, file_id: int, fav: int):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE files SET is_fav=? WHERE user_id=? AND id=?", (fav, user_id, file_id))
    con.commit()
    con.close()

def soft_delete_file(user_id: int, file_id: int):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE files SET is_deleted=1, deleted_at=? WHERE user_id=? AND id=?", (utcnow_str(), user_id, file_id))
    con.commit()
    con.close()

def restore_file(user_id: int, file_id: int):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE files SET is_deleted=0, deleted_at=NULL WHERE user_id=? AND id=?", (user_id, file_id))
    con.commit()
    con.close()

def list_recent(user_id: int, limit: int = 10):
    con = db()
    cur = con.cursor()
    if _has_is_deleted_column():
        cur.execute(
            """
            SELECT id, subject, file_type, filename, caption, added_at, is_fav
            FROM files
            WHERE user_id=? AND is_deleted=0
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
    else:
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
    if _has_is_deleted_column():
        cur.execute(
            """
            SELECT id, subject, file_type, filename, caption, added_at, is_fav
            FROM files
            WHERE user_id=? AND is_deleted=0 AND is_fav=1
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
    else:
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
    if _has_is_deleted_column():
        cur.execute(
            """
            SELECT id, subject, file_type, filename, caption, added_at, is_fav
            FROM files
            WHERE user_id=? AND is_deleted=0
              AND (filename LIKE ? OR caption LIKE ?)
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, like, like, limit),
        )
    else:
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

def purge_trash(user_id: int):
    cutoff = datetime.utcnow() - timedelta(days=TRASH_RETENTION_DAYS)
    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM files WHERE user_id=? AND is_deleted=1 AND deleted_at < ?", (user_id, cutoff.isoformat(timespec="seconds")))
    con.commit()
    con.close()

# ============================================================
# BACKUP
# ============================================================
def make_backup_name() -> str:
    return f"archive_backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.db"

def make_sqlite_backup(dest_path: str):
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

def cleanup_old_backups():
    if BACKUP_KEEP <= 0:
        return
    bdir = Path(BACKUP_DIR)
    files = sorted(bdir.glob("archive_backup_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[BACKUP_KEEP:]:
        try:
            p.unlink()
        except Exception:
            pass

async def send_backup_to_owner(context: ContextTypes.DEFAULT_TYPE, backup_path: Path, caption: str):
    if OWNER_ID == 0:
        return
    try:
        with open(backup_path, "rb") as f:
            await context.bot.send_document(chat_id=OWNER_ID, document=f, filename=backup_path.name, caption=caption)
    except Exception:
        pass

async def auto_backup_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        backup_name = make_backup_name()
        backup_path = Path(BACKUP_DIR) / backup_name
        make_sqlite_backup(str(backup_path))
        cleanup_old_backups()
        if not SILENT_BACKUP_TO_OWNER:
            await send_backup_to_owner(context, backup_path, "✅ Auto-backup (DB)")
    except Exception:
        pass

def restore_from_latest_backup() -> str:
    bdir = Path(BACKUP_DIR)
    files = sorted(bdir.glob("archive_backup_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return "❌ ماكو أي Backup داخل السيرفر."
    latest = files[0]
    shutil.copy2(str(latest), DB_PATH)
    return f"✅ تم الاسترجاع من: {latest.name}"

# ============================================================
# UI helpers
# ============================================================
def subjects_keyboard(user_id: int):
    counts = dict(count_by_subject(user_id))
    items = []
    for s in SUBJECTS:
        emoji = SUBJECT_EMOJI.get(s, "📘")
        cnt = counts.get(s, 0)
        items.append(InlineKeyboardButton(f"{emoji} {s} ({cnt})", callback_data=f"subj:{s}"))
    buttons = []
    for i in range(0, len(items), 2):
        buttons.append(items[i:i+2])
    buttons.append([InlineKeyboardButton("↩️ رجوع", callback_data="back:home")])
    return InlineKeyboardMarkup(buttons)

def files_keyboard(subject: str, rows):
    items = []
    for r in rows:
        fid = int(r["id"])
        name = (r["filename"] or "").strip() or (r["caption"] or f"file_{fid}")
        clean = name.replace("\n", " ").strip()
        if len(clean) > 26:
            clean = clean[:23] + "…"
        items.append(InlineKeyboardButton(f"📄 {clean}", callback_data=f"open:{fid}"))
    buttons = []
    for i in range(0, len(items), 2):
        buttons.append(items[i:i+2])
    buttons.append([InlineKeyboardButton("↩️ رجوع للمواد", callback_data="back:subjects")])
    return InlineKeyboardMarkup(buttons)

def manage_keyboard_admin(file_id: int, is_fav: int, is_deleted: int):
    fav_btn = InlineKeyboardButton("⭐ إزالة من المفضلة" if is_fav else "⭐ إضافة للمفضلة", callback_data=f"fav:{file_id}")
    if is_deleted:
        restore_btn = InlineKeyboardButton("♻️ استرجاع", callback_data=f"restore:{file_id}")
        return InlineKeyboardMarkup([[restore_btn], [InlineKeyboardButton("↩️ رجوع", callback_data="back:subjects")]])
    del_confirm = InlineKeyboardButton("🗑️ حذف (تأكيد)", callback_data=f"del2:{file_id}")
    back_btn = InlineKeyboardButton("↩️ رجوع", callback_data="back:subjects")
    return InlineKeyboardMarkup([[fav_btn, del_confirm], [back_btn]])

def manage_keyboard_viewer():
    return InlineKeyboardMarkup([[InlineKeyboardButton("↩️ رجوع", callback_data="back:subjects")]])

def pretty_file_line(r):
    subj = r["subject"]
    emoji = SUBJECT_EMOJI.get(subj, "📘")
    name = (r["filename"] or "").strip() or (r["caption"] or f"file_{r['id']}")
    fav = "⭐" if r["is_fav"] else ""
    return f"{fav}{emoji} <b>{subj}</b> | #{r['id']} | {name} | {r['added_at']}"

# ============================================================
# Handlers
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    text = (
        "👋 أهلاً وسهلاً بك\n\n"
        "هذا بوت أرشفة خاص بمواد الجامعة 📚\n"
        "⬇️ اضغط من القائمة واختر المادة التي تريدها"
    )
    await update.message.reply_text(text, reply_markup=MAIN_KB)

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(f"🆔 Telegram ID:\n<code>{uid}</code>", parse_mode=ParseMode.HTML)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    admin = is_admin(uid)
    msg = (
        "ℹ️ مساعدة:\n"
        "• 📚 المواد: عرض المواد.\n"
        "• 🧾 آخر الملفات: آخر الأرشيف.\n"
        "• ⭐ المفضلة.\n"
        "• 🔎 بحث.\n"
        "• 📦 نسخة احتياطية: يدوي.\n\n"
    )
    if admin:
        msg += (
            "👑 أوامر الأدمن:\n"
            "• اكتب اسم المادة ثم ارسل ملفات لإضافتها.\n"
            "• /restore_latest لاسترجاع DB من آخر Backup.\n"
            "• /purge_trash تنظيف سلة المحذوفات.\n"
            "• /health فحص الحالة.\n"
        )
    else:
        msg += "👀 أنت Viewer: تقدر تشوف وتفتح الملفات فقط."
    await update.message.reply_text(msg, reply_markup=MAIN_KB)

def get_fixed_subject(context: ContextTypes.DEFAULT_TYPE):
    subj = context.user_data.get("fixed_subject")
    until = context.user_data.get("fixed_until", 0)
    if subj and datetime.utcnow().timestamp() <= until:
        return subj
    context.user_data.pop("fixed_subject", None)
    context.user_data.pop("fixed_until", None)
    return None

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    uid = update.effective_user.id

    if context.user_data.get("search_mode"):
        context.user_data["search_mode"] = False
        rows = search_files(LIBRARY_ID, text)
        if not rows:
            await update.message.reply_text("🔎 ماكو نتائج.", reply_markup=MAIN_KB)
            return
        msg = "🔎 نتائج البحث:\n\n" + "\n".join(pretty_file_line(r) for r in rows)
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)
        return

    if text == "📚 المواد":
        await update.message.reply_text("📚 المواد:\n👇 اضغط مادة", reply_markup=subjects_keyboard(LIBRARY_ID))
        return

    if text == "🧾 آخر الملفات":
        rows = list_recent(LIBRARY_ID, 12)
        if not rows:
            await update.message.reply_text("ماكو أرشيف بعد.", reply_markup=MAIN_KB)
            return
        msg = "🧾 آخر الملفات:\n\n" + "\n".join(pretty_file_line(r) for r in rows)
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)
        return

    if text == "⭐ المفضلة":
        rows = list_favorites(LIBRARY_ID, 50)
        if not rows:
            await update.message.reply_text("⭐ ماكو مفضلة بعد.", reply_markup=MAIN_KB)
            return
        msg = "⭐ المفضلة:\n\n" + "\n".join(pretty_file_line(r) for r in rows)
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)
        return

    if text == "🔎 بحث":
        context.user_data["search_mode"] = True
        await update.message.reply_text("🔎 اكتب كلمة من اسم الملف/الوصف:", reply_markup=MAIN_KB)
        return

    if text == "📦 نسخة احتياطية":
        if not is_admin(uid):
            await update.message.reply_text("⛔ النسخ الاحتياطي للأدمن فقط.", reply_markup=MAIN_KB)
            return
        try:
            backup_name = make_backup_name()
            backup_path = Path(BACKUP_DIR) / backup_name
            make_sqlite_backup(str(backup_path))
            cleanup_old_backups()
            with open(backup_path, "rb") as f:
                await update.message.reply_document(document=f, filename=backup_name, caption="📦 Backup (DB)")
        except Exception as e:
            await update.message.reply_text(f"❌ فشل النسخ: {e}")
        return

    if text == "ℹ️ مساعدة":
        await help_cmd(update, context)
        return

    subj = normalize_subject(text)
    if subj:
        if not is_admin(uid):
            await update.message.reply_text("👀 تقدر تتصفح فقط. إضافة ملفات للأدمن فقط.", reply_markup=MAIN_KB)
            return
        context.user_data["fixed_subject"] = subj
        context.user_data["fixed_until"] = datetime.utcnow().timestamp() + (10 * 60)
        emoji = SUBJECT_EMOJI.get(subj, "📘")
        await update.message.reply_text(
            f"✅ ثبتت المادة مؤقتاً: {emoji} <b>{subj}</b>\n"
            "الآن ارسل/حوّل ملفات… (10 دقائق)",
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KB,
        )
        return

    await update.message.reply_text("ما فهمت 😅\nاضغط 📚 المواد أو 🔎 بحث.", reply_markup=MAIN_KB)

def extract_tg_unique_id(message) -> str | None:
    try:
        if message.document:
            return message.document.file_unique_id
        if message.photo:
            return message.photo[-1].file_unique_id
        if message.video:
            return message.video.file_unique_id
        if message.audio:
            return message.audio.file_unique_id
        if message.voice:
            return message.voice.file_unique_id
    except Exception:
        pass
    return None

def extract_file_size(message) -> int | None:
    try:
        if message.document:
            return message.document.file_size
        if message.photo:
            return message.photo[-1].file_size
        if message.video:
            return message.video.file_size
        if message.audio:
            return message.audio.file_size
        if message.voice:
            return message.voice.file_size
    except Exception:
        pass
    return None

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ الإضافة للأدمن فقط. أنت تقدر تتصفح الملفات.", reply_markup=MAIN_KB)
        return

    subj = get_fixed_subject(context)
    if not subj:
        await update.message.reply_text("👇 اكتب اسم المادة أولاً (مثل Linguistics) ثم ارسل الملف.", reply_markup=MAIN_KB)
        return

    msg = update.message
    caption = (msg.caption or "").strip() or None

    file_type = None
    tg_file_id = None
    orig_name = None

    if msg.document:
        file_type = "document"
        tg_file_id = msg.document.file_id
        orig_name = msg.document.file_name
    elif msg.photo:
        file_type = "photo"
        tg_file_id = msg.photo[-1].file_id
        orig_name = "photo.jpg"
    elif msg.video:
        file_type = "video"
        tg_file_id = msg.video.file_id
        orig_name = "video.mp4"
    elif msg.audio:
        file_type = "audio"
        tg_file_id = msg.audio.file_id
        orig_name = msg.audio.file_name or "audio.mp3"
    elif msg.voice:
        file_type = "voice"
        tg_file_id = msg.voice.file_id
        orig_name = "voice.ogg"
    else:
        await update.message.reply_text("⚠️ نوع غير مدعوم.", reply_markup=MAIN_KB)
        return

    file_size = extract_file_size(msg)

    # 1) حاول dedup سريع بالـ unique_id (أحيانًا ينفع)
    tg_unique_id = extract_tg_unique_id(msg)
    if tg_unique_id:
        existing_u = get_file_by_unique(LIBRARY_ID, tg_unique_id)
        if existing_u and int(existing_u["is_deleted"] or 0) == 0:
            await update.message.reply_text(
                "⚠️ هذا الملف موجود مسبقاً بالمكتبة (حسب Telegram Unique ID).\n"
                f"• رقم الملف: #{int(existing_u['id'])}\n"
                "✅ ما راح أضيف نسخة ثانية.",
                reply_markup=MAIN_KB,
            )
            return

    emoji = SUBJECT_EMOJI.get(subj, "📘")
    subject_dir = Path(FILES_DIR) / subj
    subject_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_name = safe_filename(orig_name, f"{file_type}_{ts}")
    local_path = subject_dir / f"{ts}_{safe_name}"

    # نزّل الملف (لازم حتى نحسب HASH)
    status_msg = await update.message.reply_text("⬇️ جاري التحميل…", reply_markup=MAIN_KB)

    try:
        tg_file = await context.bot.get_file(tg_file_id)
        await tg_file.download_to_drive(custom_path=str(local_path))
    except Exception as e:
        await status_msg.edit_text(f"❌ فشل تنزيل الملف: {e}")
        return

    # 2) ✅ Dedup الحقيقي: Hash على محتوى الملف
    try:
        content_hash = sha256_file(local_path)
    except Exception as e:
        # إذا فشل الهاش لأي سبب، نخليها فاضية (بس نادراً)
        content_hash = None

    if content_hash:
        existing_h = get_file_by_hash(LIBRARY_ID, content_hash)
        if existing_h:
            # الملف نفسه موجود بالفعل -> احذف النسخة اللي نزلتها الآن
            try:
                local_path.unlink(missing_ok=True)
            except Exception:
                pass

            ex_id = int(existing_h["id"])
            ex_subj = existing_h["subject"]
            await status_msg.edit_text(
                "⚠️ نفس محتوى الملف موجود مسبقاً بالمكتبة (SHA-256).\n"
                f"• المادة: {ex_subj}\n"
                f"• رقم الملف: #{ex_id}\n"
                "✅ ما راح أضيف نسخة ثانية.",
                reply_markup=MAIN_KB,
            )
            return

    # إذا الملف كان موجود بالـunique_id ومحذوف -> رجّعه بدل إضافة
    if tg_unique_id:
        existing_u = get_file_by_unique(LIBRARY_ID, tg_unique_id)
        if existing_u and int(existing_u["is_deleted"] or 0) == 1:
            ex_id = int(existing_u["id"])
            update_existing_file_from_duplicate(
                user_id=LIBRARY_ID,
                existing_id=ex_id,
                tg_file_id=tg_file_id,
                filename=safe_name,
                caption=caption,
                local_path=str(local_path),
                file_size=file_size,
                content_hash=content_hash,
            )
            await status_msg.edit_text(
                "♻️ هذا الملف كان موجود بالسلة وتم استرجاعه بدل ما نضيف نسخة مكررة.\n"
                f"{emoji} {subj}\n"
                f"رقم: #{ex_id}\n"
                "✅ تم الحفظ المحلي.",
                reply_markup=MAIN_KB,
            )
            return

    # إدخال جديد
    try:
        new_id = add_file_row(
            user_id=LIBRARY_ID,
            subject=subj,
            file_type=file_type,
            tg_file_id=tg_file_id,
            tg_unique_id=tg_unique_id,
            filename=safe_name,
            caption=caption,
            local_path=str(local_path),
            file_size=file_size,
            content_hash=content_hash,
        )
    except sqlite3.IntegrityError:
        # لو صار سباق/تكرار: احذف الملف المحلي لأن DB رفضه
        try:
            local_path.unlink(missing_ok=True)
        except Exception:
            pass
        await status_msg.edit_text("⚠️ تكرار (منعته قاعدة البيانات).", reply_markup=MAIN_KB)
        return

    await status_msg.edit_text(
        f"✅ تمت الإضافة للمكتبة العامة!\n"
        f"{emoji} {subj}\n"
        f"رقم: #{new_id}\n"
        "✅ تم الحفظ المحلي.",
        reply_markup=MAIN_KB,
    )

# ============================================================
# CALLBACKS
# ============================================================
async def cb_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    subject = query.data.split(":", 1)[1]
    rows = list_files_by_subject(LIBRARY_ID, subject, 50)
    emoji = SUBJECT_EMOJI.get(subject, "📘")
    if not rows:
        await query.message.reply_text(f"{emoji} {subject}\nماكو ملفات بعد.", reply_markup=MAIN_KB)
        return
    await query.message.reply_text(
        f"{emoji} <b>{subject}</b> — اختر ملف:",
        parse_mode=ParseMode.HTML,
        reply_markup=files_keyboard(subject, rows),
    )

async def cb_open_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    file_id = int(query.data.split(":", 1)[1])

    row = get_file_by_id(LIBRARY_ID, file_id)
    if not row:
        await query.message.reply_text("❌ الملف غير موجود.")
        return

    try:
        if int(row["is_deleted"] or 0) == 1:
            await query.message.reply_text("❌ الملف محذوف.")
            return
    except Exception:
        pass

    filename = (row["filename"] or "").strip() or f"file_{file_id}"
    caption = row["caption"] or filename
    local_path = row["local_path"]
    sent = False

    if local_path:
        p = Path(local_path)
        if p.exists() and p.is_file():
            try:
                if row["file_type"] == "photo":
                    with open(p, "rb") as f:
                        await query.message.reply_photo(photo=f, caption=caption)
                elif row["file_type"] == "video":
                    with open(p, "rb") as f:
                        await query.message.reply_video(video=f, caption=caption)
                elif row["file_type"] == "audio":
                    with open(p, "rb") as f:
                        await query.message.reply_audio(audio=f, caption=caption)
                elif row["file_type"] == "voice":
                    with open(p, "rb") as f:
                        await query.message.reply_voice(voice=f, caption=caption)
                else:
                    with open(p, "rb") as f:
                        await query.message.reply_document(document=f, caption=caption, filename=filename)
                sent = True
            except Exception:
                sent = False

    if not sent:
        try:
            ft = row["file_type"]
            fid = row["tg_file_id"]
            if ft == "document":
                await query.message.reply_document(document=fid, caption=caption)
            elif ft == "photo":
                await query.message.reply_photo(photo=fid, caption=caption)
            elif ft == "video":
                await query.message.reply_video(video=fid, caption=caption)
            elif ft == "audio":
                await query.message.reply_audio(audio=fid, caption=caption)
            elif ft == "voice":
                await query.message.reply_voice(voice=fid, caption=caption)
            else:
                await query.message.reply_text("⚠️ نوع غير مدعوم.")
                return
        except Exception as e:
            await query.message.reply_text(f"❌ تعذر إرسال الملف: {e}")
            return

    if is_admin(uid):
        is_fav_val = int(row["is_fav"] or 0)
        is_deleted_val = int(row["is_deleted"] or 0)
        await query.message.reply_text(
            f"⚙️ <b>إدارة</b> | #{file_id}",
            parse_mode=ParseMode.HTML,
            reply_markup=manage_keyboard_admin(file_id, is_fav_val, is_deleted_val),
        )
    else:
        await query.message.reply_text("✅", reply_markup=manage_keyboard_viewer())

async def cb_fav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if not is_admin(uid):
        await query.message.reply_text("⛔ هذا الخيار للأدمن فقط.")
        return
    file_id = int(query.data.split(":", 1)[1])
    row = get_file_by_id(LIBRARY_ID, file_id)
    if not row:
        await query.message.reply_text("❌ الملف غير موجود.")
        return
    new_fav = 0 if int(row["is_fav"] or 0) else 1
    set_fav(LIBRARY_ID, file_id, new_fav)
    await query.message.reply_text("⭐ تم تحديث المفضلة.")

async def cb_del_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if not is_admin(uid):
        await query.message.reply_text("⛔ هذا الخيار للأدمن فقط.")
        return
    file_id = int(query.data.split(":", 1)[1])
    row = get_file_by_id(LIBRARY_ID, file_id)
    if not row:
        await query.message.reply_text("❌ الملف غير موجود.")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نعم احذف", callback_data=f"del:{file_id}"),
         InlineKeyboardButton("❌ تراجع", callback_data="back:subjects")]
    ])
    await query.message.reply_text("🗑️ تأكيد الحذف؟ (سيروح للسلة ويمكن استرجاعه)", reply_markup=kb)

async def cb_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if not is_admin(uid):
        await query.message.reply_text("⛔ هذا الخيار للأدمن فقط.")
        return
    file_id = int(query.data.split(":", 1)[1])
    soft_delete_file(LIBRARY_ID, file_id)
    await query.message.reply_text("🗑️ تم نقل الملف إلى السلة (Soft Delete).")

async def cb_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if not is_admin(uid):
        await query.message.reply_text("⛔ هذا الخيار للأدمن فقط.")
        return
    file_id = int(query.data.split(":", 1)[1])
    restore_file(LIBRARY_ID, file_id)
    await query.message.reply_text("♻️ تم استرجاع الملف.")

async def cb_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    where = query.data.split(":", 1)[1]
    if where == "subjects":
        await query.message.reply_text("📚 المواد:\n👇 اضغط مادة", reply_markup=subjects_keyboard(LIBRARY_ID))
    else:
        await query.message.reply_text("✅", reply_markup=MAIN_KB)

# ============================================================
# ADMIN COMMANDS
# ============================================================
async def restore_latest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ للأدمن فقط.")
        return
    msg = restore_from_latest_backup()
    await update.message.reply_text(msg)
    global LIBRARY_ID
    detected = detect_library_id_legacy()
    if detected:
        LIBRARY_ID = detected

async def purge_trash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ للأدمن فقط.")
        return
    purge_trash(LIBRARY_ID)
    await update.message.reply_text("✅ تم تنظيف السلة حسب مدة الاحتفاظ.")

async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ للأدمن فقط.")
        return
    p_db = Path(DB_PATH)
    p_files = Path(FILES_DIR)
    p_bak = Path(BACKUP_DIR)

    # فحص وجود index hash
    has_hash_index = False
    try:
        con = db()
        cur = con.cursor()
        cur.execute("PRAGMA index_list(files)")
        idxs = [r[1] for r in cur.fetchall()]
        has_hash_index = "idx_files_user_hash" in idxs
        con.close()
    except Exception:
        pass

    msg = (
        "🧪 Health Check:\n"
        f"• DB exists: {'✅' if p_db.exists() else '❌'} | size: {p_db.stat().st_size if p_db.exists() else 0}\n"
        f"• Files dir: {'✅' if p_files.exists() else '❌'}\n"
        f"• Backups dir: {'✅' if p_bak.exists() else '❌'}\n"
        f"• Admins: {len(ADMIN_IDS)}\n"
        f"• Auto backup: {AUTO_BACKUP_MINUTES} min\n"
        f"• Silent backup: {'✅' if SILENT_BACKUP_TO_OWNER else '❌'}\n"
        f"• LIBRARY_ID: {LIBRARY_ID}\n"
        f"• OWNER_ID: {OWNER_ID}\n"
        f"• SEED_DB_PATH: {SEED_DB_PATH or '(empty)'}\n"
        f"• Hash unique index: {'✅' if has_hash_index else '❌'}\n"
    )
    await update.message.reply_text(msg)

# ============================================================
# MAIN
# ============================================================
def main():
    ensure_dirs()
    seed_db_if_needed()
    init_db()

    global LIBRARY_ID
    if LIBRARY_ID == 0:
        detected = detect_library_id_legacy()
        if detected:
            LIBRARY_ID = detected
        if LIBRARY_ID == 0 and OWNER_ID:
            LIBRARY_ID = OWNER_ID
    if LIBRARY_ID and not library_has_any_files(LIBRARY_ID):
        detected2 = detect_library_id_legacy()
        if detected2:
            LIBRARY_ID = detected2

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    if AUTO_BACKUP_MINUTES > 0:
        app.job_queue.run_repeating(auto_backup_job, interval=AUTO_BACKUP_MINUTES * 60, first=60)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("restore_latest", restore_latest))
    app.add_handler(CommandHandler("purge_trash", purge_trash_cmd))
    app.add_handler(CommandHandler("health", health))

    app.add_handler(CallbackQueryHandler(cb_subject, pattern=r"^subj:"))
    app.add_handler(CallbackQueryHandler(cb_open_file, pattern=r"^open:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_fav, pattern=r"^fav:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_del_confirm, pattern=r"^del2:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_del, pattern=r"^del:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_restore, pattern=r"^restore:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_back, pattern=r"^back:"))

    app.add_handler(
        MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE, handle_file)
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Bot is running...")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()