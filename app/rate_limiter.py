import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import PROJECT_ROOT

_RATE_DB_PATH: Path = PROJECT_ROOT / "data" / "rate_limits.db"
_conn: Optional[sqlite3.Connection] = None

# Global cleanup: purge all entries older than this threshold once per interval.
_GLOBAL_CLEANUP_INTERVAL: float = 3600.0   # seconds between full-table sweeps
_GLOBAL_CLEANUP_MAX_AGE: float = 86400.0   # 24 h — safely above any real window
_last_global_cleanup: float = 0.0


def _open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS rate_limit_events "
        "(key TEXT NOT NULL, ts REAL NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_key_ts ON rate_limit_events(key, ts)"
    )
    conn.commit()
    return conn


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _open_db(_RATE_DB_PATH)
    return _conn


def _reset_connection(path: Optional[Path] = None) -> None:
    """Close current connection and redirect to a new DB path. For test isolation."""
    global _conn, _RATE_DB_PATH, _last_global_cleanup
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None
    if path is not None:
        _RATE_DB_PATH = path
    _last_global_cleanup = 0.0


def _maybe_global_cleanup(now: float) -> None:
    """Remove all entries older than _GLOBAL_CLEANUP_MAX_AGE from every key.

    Called at most once per _GLOBAL_CLEANUP_INTERVAL. Prevents indefinite table
    growth for keys that stop receiving traffic (their per-key cleanup never fires).
    """
    global _last_global_cleanup
    if now - _last_global_cleanup < _GLOBAL_CLEANUP_INTERVAL:
        return
    _last_global_cleanup = now
    cutoff = now - _GLOBAL_CLEANUP_MAX_AGE
    db = _db()
    with db:
        db.execute("DELETE FROM rate_limit_events WHERE ts<?", (cutoff,))


def check_rate_limit(key: str, max_calls: int, window_seconds: int) -> bool:
    now = datetime.utcnow().timestamp()
    cutoff = now - window_seconds
    db = _db()

    # Fast read-only path: no write lock acquired when limit is already exceeded.
    (count,) = db.execute(
        "SELECT COUNT(*) FROM rate_limit_events WHERE key=? AND ts>=?",
        (key, cutoff),
    ).fetchone()
    if count >= max_calls:
        return False

    # Write path: clean expired entries for this key, re-count, then insert.
    # Re-count inside the transaction is defensive correctness (single-process
    # in practice, but costs nothing and documents the intent clearly).
    with db:
        db.execute(
            "DELETE FROM rate_limit_events WHERE key=? AND ts<?", (key, cutoff)
        )
        (count,) = db.execute(
            "SELECT COUNT(*) FROM rate_limit_events WHERE key=?", (key,)
        ).fetchone()
        if count >= max_calls:
            return False
        db.execute(
            "INSERT INTO rate_limit_events (key, ts) VALUES (?,?)", (key, now)
        )

    _maybe_global_cleanup(now)
    return True


def reset_rate_limit(key: str) -> None:
    db = _db()
    with db:
        db.execute("DELETE FROM rate_limit_events WHERE key=?", (key,))


def clear_all_rate_limits() -> None:
    db = _db()
    with db:
        db.execute("DELETE FROM rate_limit_events")
