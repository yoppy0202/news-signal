"""
storage/db.py — SQLite 初期化と接続ヘルパ

【テーブル】
  - events           : RSS 等で収集したイベント本文
                       Phase 1: sentiment / sentiment_label / event_type カラム追加
  - price_snapshots  : イベントに紐づく T0 価格スナップショット
  - price_impact     : Phase 2: T+5m/15m/1h/4h/24h の価格変化率
  - notified_events  : Phase 4: Telegram 送信済み event_id の記録（重複防止）

使用法:
  from storage.db import get_conn, init_db
  with get_conn() as conn:
      init_db(conn)
"""

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

# DB パス（環境変数 NEWS_SIGNAL_DB があれば優先）
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "news_signal.sqlite3"
DB_PATH = Path(os.environ.get("NEWS_SIGNAL_DB", str(DEFAULT_DB_PATH)))


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id       TEXT PRIMARY KEY,     -- UUID
    source         TEXT NOT NULL,        -- 'rss:<feed_name>' など
    url            TEXT,
    title          TEXT,
    raw_text       TEXT NOT NULL,
    timestamp_utc  TEXT NOT NULL,        -- ISO8601 UTC
    event_hash     TEXT NOT NULL UNIQUE, -- 重複排除キー
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_events_source    ON events(source);

CREATE TABLE IF NOT EXISTS price_snapshots (
    snapshot_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id       TEXT NOT NULL,
    symbol         TEXT,                 -- 抽出シンボル（例: SOL, BONK）
    contract_addr  TEXT,                 -- 抽出 CA（Solana/EVM 等）
    chain          TEXT,                 -- 'solana' | 'evm' | 'cex' | 'unknown'
    price_usd      REAL,
    source         TEXT,                 -- 'jupiter' | 'binance' | 'dexscreener'
    fetched_at_utc TEXT NOT NULL,
    raw_response   TEXT,                 -- デバッグ用
    FOREIGN KEY(event_id) REFERENCES events(event_id)
);

CREATE INDEX IF NOT EXISTS idx_snap_event  ON price_snapshots(event_id);
CREATE INDEX IF NOT EXISTS idx_snap_symbol ON price_snapshots(symbol);

CREATE TABLE IF NOT EXISTS price_impact (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id       TEXT NOT NULL,
    token          TEXT NOT NULL,       -- シンボル or CA
    window_label   TEXT NOT NULL,       -- 't_plus_5m' | 't_plus_15m' | 't_plus_1h' | 't_plus_4h' | 't_plus_24h'
    price          REAL,               -- 対象時刻の価格
    pct_change     REAL,               -- (price - price_t0) / price_t0 * 100
    calculated_at  TEXT NOT NULL,
    UNIQUE(event_id, token, window_label)
);

CREATE INDEX IF NOT EXISTS idx_impact_event  ON price_impact(event_id);
CREATE INDEX IF NOT EXISTS idx_impact_token  ON price_impact(token);

CREATE TABLE IF NOT EXISTS notified_events (
    event_id     TEXT PRIMARY KEY,
    notified_at  TEXT NOT NULL
);
"""


# Phase 1: events テーブルに追加するカラム（既存DBへの ALTER TABLE 用）
MIGRATIONS = [
    "ALTER TABLE events ADD COLUMN sentiment       REAL",
    "ALTER TABLE events ADD COLUMN sentiment_label TEXT",
    "ALTER TABLE events ADD COLUMN event_type      TEXT",
]


def init_db(conn: sqlite3.Connection) -> None:
    """スキーマを作成（IF NOT EXISTS なので冪等）。マイグレーションも実行。"""
    conn.executescript(SCHEMA)
    conn.commit()
    _run_migrations(conn)
    logger.debug(f"DB 初期化完了: {DB_PATH}")


def _run_migrations(conn: sqlite3.Connection) -> None:
    """MIGRATIONS リストを順に試し、既存カラムへの追加はスキップ。"""
    for sql in MIGRATIONS:
        try:
            conn.execute(sql)
            conn.commit()
            logger.info(f"  Migration 適用: {sql}")
        except Exception:
            # 'duplicate column name' は正常スキップ
            pass


@contextmanager
def get_conn():
    """コンテキストマネージャ。parent dir は自動作成。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
