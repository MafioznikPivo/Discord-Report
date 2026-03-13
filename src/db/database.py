from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON;")
        await self._conn.execute("PRAGMA journal_mode = WAL;")
        await self._initialize_schema()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> int:
        conn = self._require_connection()
        async with self._lock:
            cursor = await conn.execute(query, params)
            await conn.commit()
            rowcount = cursor.rowcount
            await cursor.close()
        return rowcount

    async def execute_insert(self, query: str, params: tuple[Any, ...] = ()) -> int:
        conn = self._require_connection()
        async with self._lock:
            cursor = await conn.execute(query, params)
            await conn.commit()
            lastrowid = cursor.lastrowid
            await cursor.close()
        return int(lastrowid)

    async def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        conn = self._require_connection()
        async with self._lock:
            cursor = await conn.execute(query, params)
            row = await cursor.fetchone()
            await cursor.close()
        return dict(row) if row is not None else None

    async def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        conn = self._require_connection()
        async with self._lock:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            await cursor.close()
        return [dict(row) for row in rows]

    def _require_connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База данных не подключена")
        return self._conn

    async def _initialize_schema(self) -> None:
        conn = self._require_connection()
        async with self._lock:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    reporter_id INTEGER NOT NULL,
                    offender_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    accepted_at TEXT,
                    closed_at TEXT,
                    accepted_by_mod_id INTEGER,
                    closed_by_mod_id INTEGER,
                    rejected_by_mod_id INTEGER,
                    reject_reason TEXT,
                    close_reason TEXT,
                    intake_message_id INTEGER,
                    control_message_id INTEGER,
                    report_text_channel_id INTEGER,
                    report_voice_channel_id INTEGER,
                    reporter_deadline_ts INTEGER,
                    offender_deadline_ts INTEGER
                );

                CREATE TABLE IF NOT EXISTS help_tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    question_text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    closed_at TEXT,
                    intake_message_id INTEGER,
                    closed_by_mod_id INTEGER,
                    close_reason TEXT
                );

                CREATE TABLE IF NOT EXISTS help_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER NOT NULL,
                    direction TEXT NOT NULL,
                    author_id INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(ticket_id) REFERENCES help_tickets(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_reports_status
                    ON reports(status);

                CREATE INDEX IF NOT EXISTS idx_reports_deadlines
                    ON reports(status, reporter_deadline_ts, offender_deadline_ts);

                CREATE INDEX IF NOT EXISTS idx_help_tickets_status
                    ON help_tickets(status);

                CREATE INDEX IF NOT EXISTS idx_help_tickets_user_status
                    ON help_tickets(user_id, status);
                """
            )
            await conn.commit()
