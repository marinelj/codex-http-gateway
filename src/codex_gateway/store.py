from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .config import Settings
from .models import ACTIVE


class GatewayError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def now() -> str:
    return datetime.now(UTC).isoformat()


def fingerprint(prompt: str, thread_id: str | None) -> str:
    return hashlib.sha256(json.dumps([prompt, thread_id]).encode()).hexdigest()


class Store:
    """One event-loop thread owns this connection; transactions never cross awaits."""

    def __init__(self, settings: Settings):
        self.lock_fd = os.open(settings.data_dir / "instance.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.lock_fd)
            raise RuntimeError("Another gateway already owns this data directory") from None
        try:
            path = settings.data_dir / "gateway.sqlite3"
            self.db = sqlite3.connect(path)
            path.chmod(0o600)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS threads (
                    thread_id TEXT PRIMARY KEY, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL UNIQUE,
                    requested_thread_id TEXT,
                    thread_id TEXT REFERENCES threads(thread_id),
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    final_output TEXT,
                    exit_code INTEGER,
                    usage TEXT,
                    error TEXT,
                    idempotency_key TEXT UNIQUE,
                    fingerprint TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run_per_thread
                    ON runs(thread_id)
                    WHERE thread_id IS NOT NULL AND status IN ('queued','starting','running');
                CREATE INDEX IF NOT EXISTS runs_by_thread ON runs(thread_id, seq);
                CREATE TABLE IF NOT EXISTS events (
                    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_by_run ON events(run_id, cursor);
            """)
            with self.db:
                for key, value in (
                    ("schema_version", "1"),
                    ("workspace", str(settings.workspace)),
                    ("codex_home", str(settings.codex_home)),
                ):
                    row = self.db.execute(
                        "SELECT value FROM metadata WHERE key=?", (key,)
                    ).fetchone()
                    if row and row[0] != value:
                        raise RuntimeError(f"Data directory belongs to a different {key}")
                    self.db.execute("INSERT OR IGNORE INTO metadata VALUES (?, ?)", (key, value))
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            os.close(self.lock_fd)
            raise

    def close(self) -> None:
        self.db.close()
        os.close(self.lock_fd)

    @staticmethod
    def decode(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for key in ("usage", "error"):
            result[key] = json.loads(result[key]) if result[key] else None
        for key in ("seq", "idempotency_key", "fingerprint"):
            result.pop(key, None)
        return result

    def get(self, run_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise GatewayError(404, "run_not_found", "Run not found")
        return self.decode(row)

    def replay(self, key: str | None, digest: str) -> dict[str, Any] | None:
        if key is None:
            return None
        row = self.db.execute("SELECT * FROM runs WHERE idempotency_key=?", (key,)).fetchone()
        if row is None:
            return None
        if row["fingerprint"] != digest:
            raise GatewayError(409, "idempotency_conflict", "Key was used with a different request")
        return self.decode(row)

    def active_count(self) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM runs WHERE status IN ('queued','starting','running')"
        ).fetchone()[0]

    def create(self, prompt: str, thread_id: str | None, key: str | None) -> dict[str, Any]:
        run_id = "run_" + uuid4().hex
        try:
            with self.db:
                if thread_id:
                    self.db.execute(
                        "INSERT OR IGNORE INTO threads VALUES (?, ?)", (thread_id, now())
                    )
                self.db.execute(
                    """INSERT INTO runs
                    (run_id,requested_thread_id,thread_id,prompt,status,created_at,
                     idempotency_key,fingerprint) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        run_id,
                        thread_id,
                        thread_id,
                        prompt,
                        "queued",
                        now(),
                        key,
                        fingerprint(prompt, thread_id),
                    ),
                )
        except sqlite3.IntegrityError:
            raise GatewayError(409, "thread_busy", "Thread already has an active run") from None
        self.event(run_id, "gateway", {"type": "gateway.queued"})
        return self.get(run_id)

    def update(self, run_id: str, **fields: Any) -> None:
        allowed = {
            "status",
            "started_at",
            "finished_at",
            "final_output",
            "exit_code",
            "usage",
            "error",
        }
        if not fields or not fields.keys() <= allowed:
            raise ValueError("Invalid update fields")
        values = [json.dumps(v) if k in {"usage", "error"} else v for k, v in fields.items()]
        with self.db:
            self.db.execute(
                "UPDATE runs SET " + ",".join(f"{k}=?" for k in fields) + " WHERE run_id=?",
                [*values, run_id],
            )

    def bind_thread(self, run_id: str, thread_id: str) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO threads VALUES (?, ?)", (thread_id, now()))
            self.db.execute("UPDATE runs SET thread_id=? WHERE run_id=?", (thread_id, run_id))

    def finish(self, run_id: str, status: str, error: dict | None = None, **fields: Any) -> None:
        self.update(run_id, status=status, finished_at=now(), error=error, **fields)
        self.event(run_id, "gateway", {"type": f"gateway.{status}", "error": error})

    def recover(self) -> None:
        rows = self.db.execute(
            "SELECT run_id FROM runs WHERE status IN ('queued','starting','running')"
        ).fetchall()
        for row in rows:
            self.finish(
                row[0],
                "interrupted",
                {
                    "code": "service_restarted",
                    "message": "Service stopped before this run finished",
                },
            )

    def event(self, run_id: str, source: str, data: dict) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO events(run_id,created_at,source,data) VALUES (?,?,?,?)",
                (run_id, now(), source, json.dumps(data, ensure_ascii=False)),
            )

    def events(
        self,
        *,
        run_id: str | None = None,
        thread_id: str | None = None,
        after: int = 0,
        limit: int = 100,
    ) -> dict:
        if run_id is not None:
            query = "SELECT e.* FROM events e WHERE e.run_id=? AND e.cursor>?"
            value = run_id
        else:
            query = (
                "SELECT e.* FROM events e JOIN runs r ON r.run_id=e.run_id "
                "WHERE r.thread_id=? AND e.cursor>?"
            )
            value = thread_id
        rows = self.db.execute(
            query + " ORDER BY e.cursor LIMIT ?", (value, after, limit + 1)
        ).fetchall()
        items = [dict(row) | {"data": json.loads(row["data"])} for row in rows[:limit]]
        return {
            "events": items,
            "next_cursor": items[-1]["cursor"] if items else after,
            "has_more": len(rows) > limit,
        }

    def latest(self, thread_id: str) -> dict:
        row = self.db.execute(
            "SELECT * FROM runs WHERE thread_id=? ORDER BY seq DESC LIMIT 1", (thread_id,)
        ).fetchone()
        if row is None:
            raise GatewayError(404, "thread_not_found", "No gateway history for this thread")
        return self.decode(row)

    def runs(self, *, thread_id: str | None, after: str | None, limit: int) -> dict:
        seq = 0
        if after:
            row = self.db.execute("SELECT seq FROM runs WHERE run_id=?", (after,)).fetchone()
            if row is None:
                raise GatewayError(400, "invalid_cursor", "Unknown run cursor")
            seq = row[0]
        query = "SELECT * FROM runs WHERE seq>?"
        args: list[Any] = [seq]
        if thread_id:
            query += " AND thread_id=?"
            args.append(thread_id)
        rows = self.db.execute(query + " ORDER BY seq LIMIT ?", [*args, limit + 1]).fetchall()
        return {
            "runs": [self.decode(row) for row in rows[:limit]],
            "next_after": rows[min(len(rows), limit) - 1]["run_id"] if rows else after,
            "has_more": len(rows) > limit,
        }

    def is_active(self, run_id: str) -> bool:
        return self.get(run_id)["status"] in ACTIVE
