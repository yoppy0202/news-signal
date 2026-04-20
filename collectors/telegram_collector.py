"""
collectors/telegram_collector.py — Telegram 公開チャンネルからメッセージを収集

【処理】
  1. TELEGRAM_CHANNELS に列挙した公開チャンネルを巡回
  2. 直近24時間以内のメッセージを最大50件取得
  3. event_hash(SHA256) で重複排除して events テーブルに保存

【認証方式】
  - TELEGRAM_SESSION 環境変数が設定されている場合: StringSession（GitHub Actions 対応）
  - 未設定の場合: ファイルセッション（ローカル開発用。初回は電話番号認証が必要）

【初回認証】
  scripts/telegram_auth.py を実行してセッション文字列を取得し、
  TELEGRAM_SESSION として GitHub Secrets に登録する。

使用法:
  from collectors.telegram_collector import run_telegram_collector
  n = run_telegram_collector()
"""

import asyncio
import hashlib
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

from storage.db import get_conn, init_db

logger = logging.getLogger(__name__)

# ---- 設定 ----------------------------------------------------------------

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
try:
    from dotenv import load_dotenv
    if _ENV_FILE.exists():
        load_dotenv(_ENV_FILE)
except ImportError:
    pass

API_ID   = os.environ.get("TELEGRAM_API_ID", "")
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
SESSION  = os.environ.get("TELEGRAM_SESSION", "")
PHONE    = os.environ.get("TELEGRAM_PHONE", "")

TELEGRAM_CHANNELS = [
    "whale_alert",            # 大口送金アラート
    "CoinDesk",               # ニュース
    "cointelegraph",          # ニュース
    "solana_news_official",   # Solana 関連
    "defipulse",              # DeFi 情報
]

MAX_MESSAGES_PER_CHANNEL = 50
LOOKBACK_HOURS = 24
CHANNEL_SLEEP  = 3  # チャンネル間のスリープ（秒）

# ---- ヘルパ ---------------------------------------------------------------

def _build_event_hash(source: str, url: str, title: str) -> str:
    base = f"{source}|{url or ''}|{title or ''}".encode("utf-8", errors="replace")
    return hashlib.sha256(base).hexdigest()

# ---- 非同期収集 -----------------------------------------------------------

async def _collect_async() -> List[Dict]:
    """全チャンネルからメッセージを収集して dict リストで返す。"""
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.errors import FloodWaitError, ChannelPrivateError, UsernameNotOccupiedError
    except ImportError:
        logger.error("[TG] telethon がインストールされていません: pip install telethon")
        return []

    try:
        api_id = int(API_ID)
    except (ValueError, TypeError):
        logger.error(f"[TG] TELEGRAM_API_ID が整数でない: {API_ID!r}")
        return []

    # GitHub Actions は StringSession、ローカルはファイルセッション
    session = StringSession(SESSION) if SESSION else "news_signal_tg"

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    results: List[Dict] = []

    async with TelegramClient(session, api_id, API_HASH) as client:
        if not SESSION:
            # ファイルセッション（ローカル）: 電話番号認証
            await client.start(phone=PHONE or None)

        for channel_name in TELEGRAM_CHANNELS:
            try:
                entity = await client.get_entity(channel_name)
                messages = await client.get_messages(entity, limit=MAX_MESSAGES_PER_CHANNEL)
                count = 0

                for msg in messages:
                    if not msg.text:
                        continue

                    msg_date = msg.date
                    if msg_date.tzinfo is None:
                        msg_date = msg_date.replace(tzinfo=timezone.utc)
                    # メッセージは新しい順で返るため、cutoff より古ければ以降はすべて古い
                    if msg_date < cutoff:
                        break

                    source     = f"telegram_{channel_name}"
                    url        = f"https://t.me/{channel_name}/{msg.id}"
                    title      = msg.text[:100].replace("\n", " ")
                    raw_text   = msg.text
                    event_hash = _build_event_hash(source, url, title)
                    event_id   = str(uuid.uuid4())

                    results.append({
                        "event_id":      event_id,
                        "source":        source,
                        "url":           url,
                        "title":         title,
                        "raw_text":      raw_text,
                        "timestamp_utc": msg_date.isoformat(),
                        "event_hash":    event_hash,
                    })
                    count += 1

                logger.info(f"[TG] {channel_name}: {count} 件取得")

            except FloodWaitError as e:
                logger.warning(f"[TG] {channel_name}: FloodWait {e.seconds}s → スキップ")
            except (ChannelPrivateError, UsernameNotOccupiedError) as e:
                logger.warning(f"[TG] {channel_name}: チャンネル取得不可 → {e}")
            except Exception as e:
                logger.warning(f"[TG] {channel_name}: 取得失敗 → {e}")

            await asyncio.sleep(CHANNEL_SLEEP)

    return results


# ---- DB 保存 -------------------------------------------------------------

def _save_events(events: List[Dict]) -> int:
    """events テーブルに INSERT OR IGNORE で保存。戻り値: 新規件数。"""
    saved = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        init_db(conn)
        cur = conn.cursor()
        for ev in events:
            try:
                cur.execute(
                    """
                    INSERT OR IGNORE INTO events
                        (event_id, source, url, title, raw_text, timestamp_utc, event_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ev["event_id"],
                        ev["source"],
                        ev["url"],
                        ev["title"],
                        ev["raw_text"],
                        ev["timestamp_utc"],
                        ev["event_hash"],
                    ),
                )
                if cur.rowcount > 0:
                    saved += 1
            except Exception as e:
                logger.error(f"[TG] DB保存失敗 event_id={ev.get('event_id')}: {e}")
        conn.commit()

    return saved


# ---- エントリポイント -----------------------------------------------------

def run_telegram_collector() -> int:
    """
    Telegram 公開チャンネルからメッセージを収集して events テーブルに保存する。
    TELEGRAM_API_ID が未設定の場合は即スキップ。
    戻り値: 新規保存件数。
    """
    if not API_ID or not API_HASH:
        logger.info("[TG] TELEGRAM_API_ID / TELEGRAM_API_HASH 未設定のためスキップ")
        return 0

    try:
        events = asyncio.run(_collect_async())
    except Exception as e:
        logger.exception(f"[TG] 収集失敗: {e}")
        return 0

    saved = _save_events(events)
    logger.info(f"[TG] 完了: 収集 {len(events)} 件 / 新規保存 {saved} 件")
    return saved


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_telegram_collector()
