"""SQLite 저장소 (contracts § 6).

업무 원본(이름·마감·상태)은 Eureka에 있다. 여기에는 알림 설정, 알림 이력, 재질문 대기만 저장한다.
"""

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from desk_pet_backend.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
  item_id TEXT PRIMARY KEY,
  offset_min INTEGER NOT NULL DEFAULT 60
);

CREATE TABLE IF NOT EXISTS alerts (
  item_id TEXT NOT NULL,
  due_at INTEGER NOT NULL,      -- epoch ms, Eureka 값 그대로
  offset_min INTEGER NOT NULL,
  status TEXT NOT NULL,         -- sent | undelivered
  sent_at INTEGER,
  PRIMARY KEY (item_id, due_at, offset_min)
);

CREATE TABLE IF NOT EXISTS pending (
  user_id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,        -- 재질문 전 원래 요청 (JSON)
  expires_at INTEGER NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    """DB 파일을 열고, 테이블이 없으면 만든다."""
    path = Path(settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def set_reminder(item_id: str, offset_minutes: int) -> None:
    """Eureka에 등록된 업무의 알림 시점을 저장한다."""
    with closing(connect()) as conn, conn:
        conn.execute(
            "INSERT INTO reminders (item_id, offset_min) VALUES (?, ?) "
            "ON CONFLICT(item_id) DO UPDATE SET offset_min = excluded.offset_min",
            (item_id, offset_minutes),
        )


def delete_reminder(item_id: str) -> None:
    """삭제된 업무의 알림 시점을 지운다."""
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM reminders WHERE item_id = ?", (item_id,))


def get_pending(user_id: str) -> dict | None:
    """만료되지 않은 재질문 맥락을 돌려주고, 만료된 항목은 지운다."""
    now_ms = int(time.time() * 1000)
    with closing(connect()) as conn, conn:
        row = conn.execute(
            "SELECT payload, expires_at FROM pending WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] <= now_ms:
            conn.execute("DELETE FROM pending WHERE user_id = ?", (user_id,))
            return None
        return json.loads(row["payload"])


def set_pending(user_id: str, payload: dict) -> None:
    """재질문 맥락을 사용자별로 60초간 보관한다."""
    expires_at = int(time.time() * 1000) + 60_000
    with closing(connect()) as conn, conn:
        conn.execute(
            "INSERT INTO pending (user_id, payload, expires_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET payload = excluded.payload, "
            "expires_at = excluded.expires_at",
            (user_id, json.dumps(payload, ensure_ascii=False), expires_at),
        )


def clear_pending(user_id: str) -> None:
    """확정된 요청의 재질문 맥락을 지운다."""
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM pending WHERE user_id = ?", (user_id,))
