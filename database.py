import logging
import sqlite3
from datetime import datetime

logger = logging.getLogger("bot.database")

DB_PATH = "bot_database.db"

def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            nickname TEXT NOT NULL,
            static_id TEXT NOT NULL,
            rank TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            nickname TEXT NOT NULL,
            static_id TEXT NOT NULL,
            rank TEXT NOT NULL,
            method TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            reviewer_id INTEGER,
            reviewer_name TEXT,
            message_id INTEGER,
            docs TEXT,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        CREATE TABLE IF NOT EXISTS blacklist (
            user_id INTEGER PRIMARY KEY,
            nickname TEXT,
            static_id TEXT,
            reason TEXT,
            added_by_id INTEGER,
            added_by_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP
        )
        CREATE TABLE IF NOT EXISTS audit_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            target_user_id INTEGER NOT NULL,
            target_user_name TEXT NOT NULL,
            target_static_id TEXT,
            target_rank TEXT,
            target_position TEXT,
            method TEXT,
            reason TEXT,
            performed_by_id INTEGER NOT NULL,
            performed_by_name TEXT NOT NULL,
            issued_roles TEXT,
            removed_roles TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        INSERT INTO users (user_id, nickname, static_id, rank, status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            nickname = excluded.nickname,
            static_id = excluded.static_id,
            rank = excluded.rank,
            status = excluded.status,
            updated_at = excluded.updated_at
        INSERT INTO blacklist (user_id, nickname, static_id, reason, added_by_id, added_by_name, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            nickname = excluded.nickname,
            static_id = excluded.static_id,
            reason = excluded.reason,
            added_by_id = excluded.added_by_id,
            added_by_name = excluded.added_by_name,
            expires_at = excluded.expires_at
        INSERT INTO applications (user_id, user_name, nickname, static_id, rank, method, message_id, docs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        UPDATE applications
        SET status = ?, reviewer_id = ?, reviewer_name = ?, updated_at = ?
        WHERE id = ?
        INSERT INTO audit_records
        (action, target_user_id, target_user_name, target_static_id, target_rank,
         target_position, method, reason, performed_by_id, performed_by_name,
         issued_roles, removed_roles)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        SELECT * FROM audit_records
        ORDER BY created_at DESC
        LIMIT ?
        SELECT nickname, static_id, rank FROM applications
        WHERE user_id = ?
        ORDER BY id DESC LIMIT 1
        SELECT reason, performed_by_name, created_at FROM audit_records
        WHERE action = 'Повысить' AND target_user_id = ?
        ORDER BY created_at DESC LIMIT 1
    """, (target_user_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    reason, performer_name, created_at_str = row

    try:
        if "T" in created_at_str:
            dt = datetime.fromisoformat(created_at_str)
        else:
            dt = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")

        from datetime import timezone
        dt_utc = dt.replace(tzinfo=timezone.utc)
        dt_local = dt_utc.astimezone()

        today_local = datetime.now().date()
        if dt_local.date() == today_local:
            old_rank = "Неизвестно"
            new_rank = "Неизвестно"

            import re
            m = re.match(r"С (.*?) на (.*?)\.(.*)", reason)
            if m:
                old_rank = m.group(1).strip()
                new_rank = m.group(2).strip()
            elif "С " in reason and " на " in reason:
                parts = reason.split(" на ")
                old_rank = parts[0].replace("С ", "").strip()
                new_rank = parts[1].split(".")[0].strip()

            return {
                "old_rank": old_rank,
                "new_rank": new_rank,
                "performer": performer_name,
                "date": dt_local.strftime("%Y-%m-%d")
            }
    except Exception as e:
        logger.error("Ошибка при проверке даты повышения: %s", e)

    return None
