"""
collectors/rss_collector.py — RSS フィードをポーリングして events テーブルに保存

【処理】
  1. RSS_FEEDS を feedparser でポーリング
  2. 各エントリから event_id(uuid) / source / title / url / raw_text /
     timestamp_utc / event_hash を生成
  3. SQLite の events テーブルに INSERT（event_hash で重複排除）

使用法:
  from collectors.rss_collector import run_rss_collector
  n = run_rss_collector()
"""

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Tuple

import feedparser

from storage.db import get_conn, init_db

logger = logging.getLogger(__name__)


# Phase 1: exploit専門メディアとSolana公式を追加
RSS_FEEDS: List[Tuple[str, str]] = [
    ("coindesk",        "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("cointelegraph",   "https://cointelegraph.com/rss"),
    ("decrypt",         "https://decrypt.co/feed"),
    ("theblock",        "https://www.theblock.co/rss.xml"),
    ("bitcoinmagazine", "https://bitcoinmagazine.com/feed"),
    ("rekt",            "https://rekt.news/rss/"),           # exploit専門
    ("solana_official", "https://solana.com/news/rss.xml"),  # Solana公式
]


def _parse_entry_timestamp(entry) -> str:
    """feedparser の entry から UTC ISO8601 タイムスタンプを返す。"""
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, key, None) or entry.get(key) if hasattr(entry, "get") else None
        if t:
            try:
                dt = datetime(*t[:6], tzinfo=timezone.utc)
                return dt.isoformat()
            except (TypeError, ValueError):
                continue
    return datetime.now(timezone.utc).isoformat()


def _build_event_hash(source: str, url: str, title: str) -> str:
    """source + url + title から一意ハッシュを生成。url が空なら title 側で。"""
    base = f"{source}|{url or ''}|{title or ''}".encode("utf-8", errors="replace")
    return hashlib.sha256(base).hexdigest()


def _entry_raw_text(entry) -> str:
    """title + summary + content を結合したテキスト。"""
    parts = []
    title = getattr(entry, "title", "") or ""
    if title:
        parts.append(title)
    summary = getattr(entry, "summary", "") or ""
    if summary:
        parts.append(summary)
    # content は list of dict
    contents = getattr(entry, "content", None)
    if contents:
        for c in contents:
            val = c.get("value") if isinstance(c, dict) else None
            if val:
                parts.append(val)
    return "\n\n".join(parts).strip()


def run_rss_collector() -> int:
    """RSS_FEEDS を巡回し、新規イベントを events テーブルに保存する。
    戻り値: 新規保存された件数。
    """
    inserted = 0
    with get_conn() as conn:
        init_db(conn)
        cur = conn.cursor()

        for feed_name, feed_url in RSS_FEEDS:
            source = f"rss:{feed_name}"
            logger.info(f"[RSS] fetching {feed_name} ({feed_url})")
            try:
                parsed = feedparser.parse(feed_url)
            except Exception as e:
                logger.error(f"[RSS] parse error {feed_name}: {e}")
                continue

            if parsed.bozo and not parsed.entries:
                logger.warning(f"[RSS] {feed_name} no entries (bozo={parsed.bozo_exception})")
                continue

            for entry in parsed.entries:
                title = getattr(entry, "title", "") or ""
                url = getattr(entry, "link", "") or ""
                raw_text = _entry_raw_text(entry)
                if not raw_text:
                    continue

                event_hash = _build_event_hash(source, url, title)
                timestamp_utc = _parse_entry_timestamp(entry)
                event_id = str(uuid.uuid4())

                try:
                    cur.execute(
                        """
                        INSERT OR IGNORE INTO events
                            (event_id, source, url, title, raw_text, timestamp_utc, event_hash)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (event_id, source, url, title, raw_text, timestamp_utc, event_hash),
                    )
                    if cur.rowcount > 0:
                        inserted += 1
                except Exception as e:
                    logger.error(f"[RSS] insert error {feed_name}: {e}")
                    continue

            conn.commit()

    logger.info(f"[RSS] 新規 {inserted} 件保存")
    return inserted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_rss_collector()
