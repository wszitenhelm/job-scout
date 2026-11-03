"""SQLite store. One file, no server.

Why a store at all: idempotency. The job runs every day, and the same
2,000 jobs come back every time. The store remembers what was already
seen and already posted, so Slack only ever gets new roles, and a rerun
after a crash never double-posts.
"""

import sqlite3
from datetime import datetime, timezone

STATUSES = ("new", "shortlist", "skip", "applied", "interview", "rejected", "offer", "closed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,   -- board:job_id, stable across runs
    board        TEXT NOT NULL,
    company      TEXT NOT NULL,
    title        TEXT NOT NULL,
    location     TEXT,
    url          TEXT,
    where_ok     TEXT,               -- yes / maybe
    asia         TEXT,               -- yes / ask
    description  TEXT,
    updated_at   TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'new',
    score        INTEGER,            -- 0-100, set by the Claude skill
    reason       TEXT,               -- one line: why that score
    posted_at    TEXT                -- when it went to Slack, NULL = not yet
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def upsert(db, job):
    """Insert a new job, or refresh last_seen on a known one.
    Returns True if the job is new. Status, score and posted_at are never
    overwritten by a fetch, so your decisions survive every rerun."""
    ts = now()
    if db.execute("SELECT 1 FROM jobs WHERE id = ?", (job["id"],)).fetchone():
        db.execute(
            "UPDATE jobs SET last_seen = ?, updated_at = ?, description = ? WHERE id = ?",
            (ts, job["updated_at"], job["description"], job["id"]),
        )
        return False
    db.execute(
        """INSERT INTO jobs (id, board, company, title, location, url, where_ok,
                             asia, description, updated_at, first_seen, last_seen)
           VALUES (:id, :board, :company, :title, :location, :url, :where,
                   :asia, :description, :updated_at, :ts, :ts)""",
        {**job, "ts": ts},
    )
    return True


def mark_closed(db, board, seen_ids):
    """Jobs from this board that weren't in today's fetch are gone.
    An empty fetch is treated as a glitch, not as 'everything closed'."""
    if not seen_ids:
        return
    placeholders = ",".join("?" * len(seen_ids))
    db.execute(
        f"""UPDATE jobs SET status = 'closed'
            WHERE board = ? AND status IN ('new', 'shortlist')
              AND id NOT IN ({placeholders})""",
        (board, *seen_ids),
    )


def list_jobs(db, status=None, unscored=False, unposted=False):
    sql, args = "SELECT * FROM jobs WHERE 1=1", []
    if status:
        sql += " AND status = ?"
        args.append(status)
    if unscored:
        sql += " AND score IS NULL"
    if unposted:
        sql += " AND posted_at IS NULL"
    sql += " ORDER BY COALESCE(score, -1) DESC, first_seen DESC"
    return [dict(r) for r in db.execute(sql, args)]


def get(db, job_id):
    row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise KeyError(job_id)
    return dict(row)


def set_score(db, job_id, score, reason):
    if not 0 <= score <= 100:
        raise ValueError("score must be 0-100")
    get(db, job_id)
    db.execute("UPDATE jobs SET score = ?, reason = ? WHERE id = ?", (score, reason, job_id))


def set_status(db, job_id, status):
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    get(db, job_id)
    db.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))


def mark_posted(db, job_ids):
    ts = now()
    db.executemany("UPDATE jobs SET posted_at = ? WHERE id = ?", [(ts, i) for i in job_ids])


def counts(db):
    return dict(db.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
