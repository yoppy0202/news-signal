"""
storage/sheets_sync.py — SQLite データを Google Sheets にフラッシュ

【対象シート】
  - ns_events      : events テーブルの内容
  - ns_price_impact: price_impact テーブルの内容

【差分更新】
  - 前回フラッシュ以降の行のみ追記（sheets_sync_state テーブルで最終行IDを管理）
  - 1時間に1回実行を想定（impact_calc.yml で制御）

【認証】
  - GOOGLE_CREDENTIALS 環境変数（JSON文字列）優先
  - フォールバック: credentials.json（ローカル開発用）
  - SHEETS_ID 環境変数でスプレッドシートを指定

使用法:
  from storage.sheets_sync import run_sheets_sync
  run_sheets_sync()
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from storage.db import get_conn, init_db

logger = logging.getLogger(__name__)

# ---- 設定 ------------------------------------------------------------------

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
try:
    from dotenv import load_dotenv
    if _ENV_FILE.exists():
        load_dotenv(_ENV_FILE)
except ImportError:
    pass

SHEETS_ID = os.environ.get("SHEETS_ID", "")

# ---- Sheets ヘッダー定義 ---------------------------------------------------

NS_EVENTS_HEADERS = [
    "event_id", "source", "url", "title",
    "timestamp_utc", "event_hash",
    "sentiment", "sentiment_label", "event_type", "created_at",
]

NS_IMPACT_HEADERS = [
    "id", "event_id", "token", "window_label",
    "price", "pct_change", "calculated_at",
]

# ---- gspread クライアント --------------------------------------------------

def _get_gspread_client():
    """GOOGLE_CREDENTIALS 環境変数 → credentials.json の順で認証。"""
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        creds_dict = json.loads(creds_json)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        creds_path = Path(__file__).resolve().parent.parent / "credentials.json"
        if not creds_path.exists():
            raise FileNotFoundError(
                "credentials.json が見つかりません。"
                "GOOGLE_CREDENTIALS 環境変数か credentials.json を用意してください。"
            )
        creds = ServiceAccountCredentials.from_json_keyfile_name(str(creds_path), scope)
    return gspread.authorize(creds)


def _get_or_create_worksheet(ss, tab_name: str, headers: List[str]):
    """タブが存在しなければ作成してヘッダー行を書き込む。"""
    import gspread
    try:
        ws = ss.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=tab_name, rows=5000, cols=len(headers))
        ws.append_row(headers, value_input_option="RAW")
        logger.info(f"[SHEETS] タブ作成: {tab_name}")
    return ws


# ---- 状態管理（最終フラッシュIDをSQLiteで管理） ---------------------------

def _get_last_synced_id(conn, table_key: str) -> int:
    """sheets_sync_state テーブルから最終同期 ID を取得（なければ 0）。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sheets_sync_state (
            table_key TEXT PRIMARY KEY,
            last_id   INTEGER NOT NULL DEFAULT 0,
            synced_at TEXT
        )
        """
    )
    conn.commit()
    row = conn.execute(
        "SELECT last_id FROM sheets_sync_state WHERE table_key = ?", (table_key,)
    ).fetchone()
    return row["last_id"] if row else 0


def _set_last_synced_id(conn, table_key: str, last_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO sheets_sync_state (table_key, last_id, synced_at)
        VALUES (?, ?, ?)
        ON CONFLICT(table_key) DO UPDATE SET last_id=excluded.last_id, synced_at=excluded.synced_at
        """,
        (table_key, last_id, now),
    )
    conn.commit()


# ---- 同期処理 --------------------------------------------------------------

def _sync_events(conn, ss) -> int:
    """events テーブルの差分を ns_events タブに追記。戻り値: 追記行数。"""
    ws = _get_or_create_worksheet(ss, "ns_events", NS_EVENTS_HEADERS)
    last_rowid = _get_last_synced_id(conn, "events")

    rows = conn.execute(
        f"""
        SELECT rowid, {', '.join(NS_EVENTS_HEADERS)}
          FROM events
         WHERE rowid > ?
         ORDER BY rowid ASC
         LIMIT 500
        """,
        (last_rowid,),
    ).fetchall()

    if not rows:
        return 0

    data = [[str(r[col] or "") for col in NS_EVENTS_HEADERS] for r in rows]
    ws.append_rows(data, value_input_option="RAW")
    max_rowid = max(r["rowid"] for r in rows)
    _set_last_synced_id(conn, "events", max_rowid)
    logger.info(f"[SHEETS] ns_events に {len(rows)} 行追記")
    return len(rows)


def _sync_price_impact(conn, ss) -> int:
    """price_impact テーブルの差分を ns_price_impact タブに追記。"""
    ws = _get_or_create_worksheet(ss, "ns_price_impact", NS_IMPACT_HEADERS)
    last_id = _get_last_synced_id(conn, "price_impact")

    rows = conn.execute(
        f"""
        SELECT {', '.join(NS_IMPACT_HEADERS)}
          FROM price_impact
         WHERE id > ?
         ORDER BY id ASC
         LIMIT 500
        """,
        (last_id,),
    ).fetchall()

    if not rows:
        return 0

    data = [[str(r[col] or "") for col in NS_IMPACT_HEADERS] for r in rows]
    ws.append_rows(data, value_input_option="RAW")
    max_id = max(r["id"] for r in rows)
    _set_last_synced_id(conn, "price_impact", max_id)
    logger.info(f"[SHEETS] ns_price_impact に {len(rows)} 行追記")
    return len(rows)


# ---- エントリポイント -------------------------------------------------------

def run_sheets_sync() -> dict:
    """
    Google Sheets に差分フラッシュ。
    戻り値: {"events": n, "price_impact": n, "ok": bool}
    """
    if not SHEETS_ID:
        logger.warning("[SHEETS] SHEETS_ID が未設定のためスキップ")
        return {"events": 0, "price_impact": 0, "ok": False}

    try:
        client = _get_gspread_client()
        ss = client.open_by_key(SHEETS_ID)
    except Exception as e:
        logger.error(f"[SHEETS] 認証/接続失敗: {e}")
        return {"events": 0, "price_impact": 0, "ok": False}

    with get_conn() as conn:
        init_db(conn)
        n_events = _sync_events(conn, ss)
        n_impact = _sync_price_impact(conn, ss)

    logger.info(f"[SHEETS] 完了: events={n_events} price_impact={n_impact}")
    return {"events": n_events, "price_impact": n_impact, "ok": True}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_sheets_sync()
    print(result)
