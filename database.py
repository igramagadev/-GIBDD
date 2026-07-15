import logging
import sqlite3
from datetime import datetime

logger = logging.getLogger("bot.database")

DB_PATH = "bot_database.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = _connect()
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
    """)

    cursor.execute("""
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
    """)

    cursor.execute("""
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
    """)

    cursor.execute("""
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
    """)

    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")


def add_or_update_user(user_id: int, nickname: str, static_id: str,
                       rank: str, status: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO users (user_id, nickname, static_id, rank, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                nickname = excluded.nickname,
                static_id = excluded.static_id,
                rank = excluded.rank,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (user_id, nickname, static_id, rank, status,
             datetime.now().isoformat()),
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.error("Ошибка add_or_update_user(%s): %s", user_id, exc)
    finally:
        conn.close()


def get_user(user_id: int) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None
    except sqlite3.Error as exc:
        logger.error("Ошибка get_user(%s): %s", user_id, exc)
        return None
    finally:
        conn.close()


def set_user_status(user_id: int, status: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE users SET status = ?, updated_at = ? WHERE user_id = ?",
            (status, datetime.now().isoformat(), user_id),
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.error("Ошибка set_user_status(%s, %s): %s", user_id, status, exc)
    finally:
        conn.close()


def add_application(user_id: int, user_name: str, nickname: str,
                    static_id: str, rank: str, method: str,
                    message_id: int, docs: str = "") -> int:
    conn = _connect()
    try:
        cursor = conn.execute(
            """
            INSERT INTO applications
                (user_id, user_name, nickname, static_id, rank, method, message_id, docs)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, user_name, nickname, static_id, rank, method,
             message_id, docs),
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.Error as exc:
        logger.error("Ошибка add_application(%s): %s", user_id, exc)
        return 0
    finally:
        conn.close()


def update_application_status(app_id: int, status: str,
                              reviewer_id: int, reviewer_name: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            UPDATE applications
            SET status = ?, reviewer_id = ?, reviewer_name = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, reviewer_id, reviewer_name,
             datetime.now().isoformat(), app_id),
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.error("Ошибка update_application_status(%s): %s", app_id, exc)
    finally:
        conn.close()


def get_application_by_message_id(message_id: int) -> tuple | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM applications WHERE message_id = ?", (message_id,)
        ).fetchone()
        return tuple(row) if row else None
    except sqlite3.Error as exc:
        logger.error("Ошибка get_application_by_message_id(%s): %s",
                      message_id, exc)
        return None
    finally:
        conn.close()


def get_user_latest_application(user_id: int) -> tuple | None:
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT nickname, static_id, rank FROM applications
            WHERE user_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        return tuple(row) if row else None
    except sqlite3.Error as exc:
        logger.error("Ошибка get_user_latest_application(%s): %s",
                      user_id, exc)
        return None
    finally:
        conn.close()



def add_to_blacklist(user_id: int, nickname: str, static_id: str,
                     reason: str, added_by_id: int, added_by_name: str,
                     expires_at: str | None = None) -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO blacklist
                (user_id, nickname, static_id, reason, added_by_id, added_by_name, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                nickname = excluded.nickname,
                static_id = excluded.static_id,
                reason = excluded.reason,
                added_by_id = excluded.added_by_id,
                added_by_name = excluded.added_by_name,
                expires_at = excluded.expires_at
            """,
            (user_id, nickname, static_id, reason, added_by_id,
             added_by_name, expires_at),
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.error("Ошибка add_to_blacklist(%s): %s", user_id, exc)
    finally:
        conn.close()


def remove_from_blacklist(user_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
        conn.commit()
    except sqlite3.Error as exc:
        logger.error("Ошибка remove_from_blacklist(%s): %s", user_id, exc)
    finally:
        conn.close()


def is_blacklisted(user_id: int) -> bool:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT user_id, expires_at FROM blacklist WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return False
        expires_at = row["expires_at"]
        if expires_at:
            try:
                exp_dt = datetime.fromisoformat(expires_at)
                if exp_dt < datetime.now():
                    conn.execute(
                        "DELETE FROM blacklist WHERE user_id = ?", (user_id,)
                    )
                    conn.commit()
                    return False
            except (ValueError, TypeError):
                pass
        return True
    except sqlite3.Error as exc:
        logger.error("Ошибка is_blacklisted(%s): %s", user_id, exc)
        return False
    finally:
        conn.close()


def get_blacklist() -> list[tuple]:
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT user_id, nickname, static_id, reason,
                   added_by_name, created_at, expires_at
            FROM blacklist
            ORDER BY created_at DESC
            """
        ).fetchall()
        return [tuple(r) for r in rows]
    except sqlite3.Error as exc:
        logger.error("Ошибка get_blacklist: %s", exc)
        return []
    finally:
        conn.close()



def add_audit_record(*, action: str, target_user_id: int,
                     target_user_name: str, target_static_id: str = "",
                     target_rank: str = "", target_position: str = "",
                     method: str = "", reason: str = "",
                     performed_by_id: int, performed_by_name: str,
                     issued_roles: str = "", removed_roles: str = "") -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO audit_records
                (action, target_user_id, target_user_name, target_static_id,
                 target_rank, target_position, method, reason,
                 performed_by_id, performed_by_name, issued_roles, removed_roles)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (action, target_user_id, target_user_name, target_static_id,
             target_rank, target_position, method, reason,
             performed_by_id, performed_by_name, issued_roles, removed_roles),
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.error("Ошибка add_audit_record: %s", exc)
    finally:
        conn.close()


def get_recent_audit_records(limit: int = 20) -> list[tuple]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM audit_records ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [tuple(r) for r in rows]
    except sqlite3.Error as exc:
        logger.error("Ошибка get_recent_audit_records: %s", exc)
        return []
    finally:
        conn.close()


def get_last_promotion(target_user_id: int) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT reason, performed_by_name, created_at FROM audit_records
            WHERE action = 'Повысить' AND target_user_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (target_user_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        logger.error("Ошибка get_last_promotion(%s): %s", target_user_id, exc)
        return None
    finally:
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
