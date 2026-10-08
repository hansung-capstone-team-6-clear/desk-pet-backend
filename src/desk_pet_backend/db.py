"""SQLite 저장소 (contracts § 6).

업무 원본(이름·마감·상태)은 Eureka에 있다. 여기에는 알림 설정, 알림 이력, 재질문 대기만 저장한다.
"""

import sqlite3
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
