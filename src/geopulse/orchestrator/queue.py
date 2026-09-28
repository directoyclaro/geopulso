"""Cola de trabajos simple sobre SQLite (concurrency=1 para cuidar la RAM)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type    VARCHAR NOT NULL,
    payload     TEXT,
    status      VARCHAR NOT NULL DEFAULT 'pending',
    attempts    INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMP NOT NULL,
    updated_at  TIMESTAMP NOT NULL,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


class JobQueue:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def enqueue(self, job_type: str, payload: dict[str, Any] | None = None) -> int:
        now = _utcnow()
        cur = self.conn.execute(
            "INSERT INTO jobs (job_type, payload, status, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?)",
            [job_type, json.dumps(payload or {}), now, now],
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def dequeue(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE status = 'pending' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        self.conn.execute(
            "UPDATE jobs SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
            [_utcnow(), row["id"]],
        )
        self.conn.commit()
        job = dict(row)
        job["payload"] = json.loads(job["payload"] or "{}")
        job["status"] = "running"
        return job

    def complete(self, job_id: int) -> None:
        self.conn.execute(
            "UPDATE jobs SET status='done', updated_at=? WHERE id=?", [_utcnow(), job_id]
        )
        self.conn.commit()

    def fail(self, job_id: int, error: str) -> None:
        self.conn.execute(
            "UPDATE jobs SET status='failed', error=?, updated_at=? WHERE id=?",
            [error, _utcnow(), job_id],
        )
        self.conn.commit()

    def stats(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    def close(self) -> None:
        self.conn.close()
