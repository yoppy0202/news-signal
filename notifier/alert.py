"""
notifier/alert.py — 高インパクトイベントの判定と Telegram 通知

【判定条件（いずれか満たせば通知）】
  1. sentiment_label == "negative" AND event_type == "hack"
  2. event_type == "listing" AND pct_change_1h > 10.0
  3. abs(pct_change_1h) > 15.0（急騰・急落）

【notified_events 永続化バックエンド】
  - SHEETS_ID が設定されている場合: Google Sheets の ns_notified タブ
    → GitHub Actions でDBがリセットされても通知済みIDが保持される
    → 起動時に全件読み込み、実行後に一括追記（APIコール最小化）
  - SHEETS_ID 未設定の場合: SQLite（ローカル開発用フォールバック）

【初回実行時の動作】
  ns_notified（またはnotified_events）が空の場合、既存イベントを全件登録してから判定。
  → 過去イベントへの遡及通知を防ぐ。

【通知先】
  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
  TELEGRAM_TOPIC_NEWS_IMPACT（オプション：設定時は指定トピックへ送信）

【重複防止】
  通知済み・評価済みのevent_idはバックエンドに記録し、再評価しない。

使用法:
  from notifier.alert import run
  run()
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from storage.db import get_conn, init_db
from storage.sheets_sync import append_notified_ids, load_notified_ids, open_spreadsheet

logger = logging.getLogger(__name__)

# ---- 閾値 ----------------------------------------------------------------

HACK_NEGATIVE_ALERT = True     # sentiment_label=negative & event_type=hack → 通知
LISTING_PCT_THRESHOLD = 10.0   # listing で +1h 変化率がこれを超えたら通知
EXTREME_PCT_THRESHOLD = 15.0   # |pct_change_1h| > この値 → 通知

# ---- 環境変数 -------------------------------------------------------------

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
try:
    from dotenv import load_dotenv
    if _ENV_FILE.exists():
        load_dotenv(_ENV_FILE)
except ImportError:
    pass

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
TOPIC_ID  = os.environ.get("TELEGRAM_TOPIC_NEWS_IMPACT", "")
SHEETS_ID = os.environ.get("SHEETS_ID", "")

# ---- ヘルパ ---------------------------------------------------------------

def _is_dry_run() -> bool:
    return not BOT_TOKEN or not CHAT_ID


def _format_message(ev: dict) -> str:
    """通知メッセージを組み立てる。"""
    tokens_str = ", ".join(ev.get("tokens") or []) or "—"
    pct_1h  = ev.get("pct_change_1h")
    pct_24h = ev.get("pct_change_24h")
    pct_1h_str  = f"{pct_1h:+.1f}%"  if pct_1h  is not None else "N/A"
    pct_24h_str = f"{pct_24h:+.1f}%" if pct_24h is not None else "N/A"
    snippet = (ev.get("raw_text") or "")[:100].replace("<", "&lt;").replace(">", "&gt;")

    return (
        f"[📰 NEWS] {ev.get('event_type', 'unknown')} | {tokens_str}\n"
        f"{ev.get('sentiment_label', '')} | {ev.get('source', '')}\n"
        f"{snippet}\n"
        f"+1h: {pct_1h_str} | +24h: {pct_24h_str}"
    )


def _send_alert(ev: dict) -> bool:
    """Telegram に送信。dry_run 時はログのみ。"""
    text = _format_message(ev)
    dry = _is_dry_run()

    if dry:
        logger.info(f"[ALERT][DRY-RUN] {ev['event_id']} — {ev.get('event_type')} | {ev.get('tokens')}")
        logger.info(f"  message:\n{text}")
        return True

    extra: dict = {}
    if TOPIC_ID:
        try:
            extra["message_thread_id"] = int(TOPIC_ID)
        except ValueError:
            pass

    import requests
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            **extra,
        }
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            logger.info(f"[ALERT] 送信成功 event_id={ev['event_id']}")
            return True
        logger.error(f"[ALERT] API エラー: {data}")
        return False
    except Exception as e:
        logger.error(f"[ALERT] 送信失敗: {e}")
        return False


# ---- 判定ロジック ---------------------------------------------------------

def _should_notify(ev: dict) -> bool:
    """通知条件を評価する。"""
    etype   = ev.get("event_type") or ""
    slabel  = ev.get("sentiment_label") or ""
    pct_1h  = ev.get("pct_change_1h")

    # 条件 1: hack 記事でネガティブ
    if etype == "hack" and slabel == "negative":
        return True

    # 条件 2: listing で +1h > 10%
    if etype == "listing" and pct_1h is not None and pct_1h > LISTING_PCT_THRESHOLD:
        return True

    # 条件 3: 急騰・急落
    if pct_1h is not None and abs(pct_1h) > EXTREME_PCT_THRESHOLD:
        return True

    return False


# ---- 共通クエリ -----------------------------------------------------------

_EVENTS_QUERY = """
    SELECT
        e.event_id,
        e.source,
        e.title,
        e.url,
        e.raw_text,
        e.event_type,
        e.sentiment_label,
        e.timestamp_utc,
        ps.symbol                                   AS token_symbol,
        MAX(CASE WHEN pi.window_label = 't_plus_1h'  THEN pi.pct_change END) AS pct_change_1h,
        MAX(CASE WHEN pi.window_label = 't_plus_24h' THEN pi.pct_change END) AS pct_change_24h
      FROM events e
 LEFT JOIN price_snapshots ps ON ps.event_id = e.event_id AND ps.price_usd IS NOT NULL
 LEFT JOIN price_impact     pi ON pi.event_id = e.event_id
      {where_clause}
  GROUP BY e.event_id
  ORDER BY e.timestamp_utc DESC
     LIMIT 200
"""


# ---- エントリポイント -----------------------------------------------------

def run(seed_existing: bool = True) -> dict:
    """
    高インパクトイベントを判定して Telegram 通知する。

    seed_existing: True の場合、バックエンドが空なら既存イベントを全件登録し
                   過去分の遡及通知を防ぐ。

    戻り値: {"candidates": n, "sent": n, "dry_run": bool, "notified_total": n, "backend": str}
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    candidates = 0
    sent = 0

    # ---- バックエンド選択 ----
    use_sheets = bool(SHEETS_ID)
    ss = None
    sheets_notified_ids: set = set()

    if use_sheets:
        ss = open_spreadsheet()
        if ss is None:
            logger.warning("[ALERT] Sheets 接続失敗。SQLite フォールバックに切り替えます")
            use_sheets = False
        else:
            sheets_notified_ids = load_notified_ids(ss)
            logger.info(f"[ALERT] Sheets から送信済み {len(sheets_notified_ids)} 件を読み込み")

    with get_conn() as conn:
        init_db(conn)
        cur = conn.cursor()

        # ---- 初回シード ----
        if seed_existing:
            if use_sheets:
                if not sheets_notified_ids:
                    all_ids = [r[0] for r in cur.execute("SELECT event_id FROM events").fetchall()]
                    if all_ids:
                        seed_rows = [(eid, now_iso) for eid in all_ids]
                        append_notified_ids(ss, seed_rows)
                        sheets_notified_ids = set(all_ids)
                        logger.info(f"[ALERT] Sheets 初回シード: {len(all_ids)} 件を ns_notified に登録")
            else:
                count = cur.execute("SELECT COUNT(*) FROM notified_events").fetchone()[0]
                if count == 0:
                    cur.execute(
                        """
                        INSERT OR IGNORE INTO notified_events (event_id, notified_at)
                        SELECT event_id, ? FROM events
                        """,
                        (now_iso,),
                    )
                    conn.commit()
                    seeded = cur.execute("SELECT COUNT(*) FROM notified_events").fetchone()[0]
                    logger.info(f"[ALERT] 初回シード: {seeded} 件を notified_events に登録（過去分スキップ）")

        # ---- 未通知イベントを取得 ----
        if use_sheets:
            # Sheets モード: Python 側でフィルタ
            all_rows = cur.execute(
                _EVENTS_QUERY.format(where_clause="")
            ).fetchall()
            rows = [r for r in all_rows if r["event_id"] not in sheets_notified_ids]
        else:
            # SQLite モード: SQL で除外
            rows = cur.execute(
                _EVENTS_QUERY.format(
                    where_clause="WHERE e.event_id NOT IN (SELECT event_id FROM notified_events)"
                )
            ).fetchall()

        logger.info(f"[ALERT] 未通知イベント {len(rows)} 件を評価")

        new_notified: list = []  # Sheets への一括追記バッファ

        for row in rows:
            ev = dict(row)
            ev["tokens"] = [ev["token_symbol"]] if ev.get("token_symbol") else []

            if not _should_notify(ev):
                # 通知不要でも評価済みとしてマーク（再評価しない）
                new_notified.append((ev["event_id"], now_iso))
                if not use_sheets:
                    cur.execute(
                        "INSERT OR IGNORE INTO notified_events (event_id, notified_at) VALUES (?, ?)",
                        (ev["event_id"], now_iso),
                    )
                continue

            candidates += 1
            ok = _send_alert(ev)
            if ok:
                sent += 1

            new_notified.append((ev["event_id"], now_iso))
            if not use_sheets:
                cur.execute(
                    "INSERT OR IGNORE INTO notified_events (event_id, notified_at) VALUES (?, ?)",
                    (ev["event_id"], now_iso),
                )
                conn.commit()

        if not use_sheets:
            conn.commit()

        if use_sheets:
            notified_total = len(sheets_notified_ids) + len(new_notified)
        else:
            notified_total = cur.execute("SELECT COUNT(*) FROM notified_events").fetchone()[0]

    # ---- Sheets への一括書き込み（run ループ後にまとめて） ----
    if use_sheets and new_notified:
        append_notified_ids(ss, new_notified)

    backend = "sheets" if use_sheets else "sqlite"
    result = {
        "candidates": candidates,
        "sent":       sent,
        "dry_run":    _is_dry_run(),
        "notified_total": notified_total,
        "backend":    backend,
    }
    logger.info(
        f"[ALERT] 完了: 通知対象={candidates} / 送信={sent} / "
        f"dry_run={result['dry_run']} / notified_total={notified_total} / backend={backend}"
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run()
    print(result)
