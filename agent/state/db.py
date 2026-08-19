"""
state/db.py — aiosqlite database setup and access.

Creates the ``data/`` directory and initialises the SQLite schema on first run.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import aiosqlite

from agent.config import settings

logger = logging.getLogger(__name__)

_db: aiosqlite.Connection | None = None

CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    tick          INTEGER NOT NULL,
    underlying    TEXT    NOT NULL,
    action        TEXT    NOT NULL,
    contracts     INTEGER NOT NULL DEFAULT 0,
    winner_agent  TEXT    NOT NULL,
    arbiter_score REAL,
    reasoning     TEXT,
    proposals_json TEXT,
    greeks_before  TEXT,
    expected_cost  REAL,
    order_id      TEXT,
    order_status  TEXT,
    guardrail_block TEXT,
    dry_run       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    tick      INTEGER NOT NULL,
    equity    REAL,
    cash      REAL,
    day_pl    REAL,
    net_delta REAL,
    net_vega  REAL,
    positions_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON portfolio_snapshots(ts);
"""


async def init_db() -> aiosqlite.Connection:
    """Open (or create) the SQLite database and run migrations."""
    global _db
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    _db = await aiosqlite.connect(str(db_path))
    _db.row_factory = aiosqlite.Row
    await _db.executescript(CREATE_SCHEMA)
    await _db.commit()
    logger.info("Database ready at %s", db_path)
    return _db


def get_db() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _db


async def close_db() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None
