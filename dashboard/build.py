"""
dashboard/build.py — SQLite → docs/data.json 生成

【処理フロー】
  1. SQLite から events / price_impact を取得（SHEETS_ID 未設定時のフォールバック）
  2. SHEETS_ID が設定されていれば Google Sheets からも試みる（将来拡張用フック）
  3. events と price_impact を結合し docs/data.json を出力
  4. 統計（event_type 別の件数・avg_1h・avg_24h）を付与

出力先: docs/data.json（GitHub Pages 用）

使用法:
  python dashboard/build.py
"""

import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage.db import get_conn, init_db

logger = logging.getLogger(__name__)

DOCS_DIR = ROOT / "docs"
DATA_JSON = DOCS_DIR / "data.json"

WINDOW_LABELS = ["t_plus_5m", "t_plus_15m", "t_plus_1h", "t_plus_4h", "t_plus_24h"]


# ---- データ取得 -----------------------------------------------------------

def _load_from_sqlite() -> tuple:
    """SQLite から (events_rows, impact_rows) を返す。"""
    with get_conn() as conn:
        init_db(conn)

        events = conn.execute(
            """
            SELECT
                e.event_id, e.source, e.title, e.url,
                e.timestamp_utc, e.event_type, e.sentiment_label,
                e.raw_text
              FROM events e
             ORDER BY e.timestamp_utc DESC
             LIMIT 500
            """
        ).fetchall()

        impacts = conn.execute(
            """
            SELECT event_id, token, window_label, pct_change
              FROM price_impact
             WHERE pct_change IS NOT NULL
            """
        ).fetchall()

    return events, impacts


# ---- データ変換 -----------------------------------------------------------

def _build_impact_map(impact_rows) -> dict:
    """event_id → {token → {window_label: pct_change}} のネストマップを返す。"""
    m: dict = defaultdict(lambda: defaultdict(dict))
    for r in impact_rows:
        m[r["event_id"]][r["token"]][r["window_label"]] = (
            round(r["pct_change"], 4) if r["pct_change"] is not None else None
        )
    return m


def _build_events_list(event_rows, impact_map: dict) -> list:
    """events リストを組み立てる。"""
    result = []
    for e in event_rows:
        event_id = e["event_id"]
        token_impacts = impact_map.get(event_id, {})

        # トークン一覧（impact がある場合はそこから、なければ空）
        tokens = list(token_impacts.keys())

        # 全トークンを束ねた impact（複数トークンある場合は最初のものを採用）
        impact: dict = {}
        if token_impacts:
            first_token_data = next(iter(token_impacts.values()))
            for wl in WINDOW_LABELS:
                v = first_token_data.get(wl)
                if v is not None:
                    impact[wl] = v

        result.append({
            "event_id":       event_id,
            "source":         e["source"] or "",
            "title":          e["title"] or "",
            "url":            e["url"] or "",
            "event_type":     e["event_type"] or "unknown",
            "sentiment_label": e["sentiment_label"] or "neutral",
            "tokens":         tokens,
            "timestamp_utc":  e["timestamp_utc"] or "",
            "raw_text":       (e["raw_text"] or "")[:300],  # 300文字に丸める
            "impact":         impact,
        })
    return result


def _build_stats(events_list: list) -> dict:
    """event_type 別の統計を計算する。"""
    buckets: dict = defaultdict(lambda: {
        "count": 0,
        "pct_1h": [],
        "pct_24h": [],
    })

    for ev in events_list:
        etype = ev["event_type"] or "unknown"
        buckets[etype]["count"] += 1
        imp = ev.get("impact", {})
        if "t_plus_1h" in imp and imp["t_plus_1h"] is not None:
            buckets[etype]["pct_1h"].append(imp["t_plus_1h"])
        if "t_plus_24h" in imp and imp["t_plus_24h"] is not None:
            buckets[etype]["pct_24h"].append(imp["t_plus_24h"])

    by_event_type: dict = {}
    for etype, data in buckets.items():
        pct_1h_list = data["pct_1h"]
        pct_24h_list = data["pct_24h"]
        avg_1h = round(sum(pct_1h_list) / len(pct_1h_list), 2) if pct_1h_list else None
        avg_24h = round(sum(pct_24h_list) / len(pct_24h_list), 2) if pct_24h_list else None
        win_rate = (
            round(sum(1 for v in pct_1h_list if v > 5) / len(pct_1h_list) * 100, 1)
            if pct_1h_list else None
        )
        by_event_type[etype] = {
            "count":    data["count"],
            "avg_1h":   avg_1h,
            "avg_24h":  avg_24h,
            "win_rate_1h_gt5pct": win_rate,
        }

    return {"by_event_type": by_event_type}


# ---- エントリポイント -----------------------------------------------------

def build() -> dict:
    """
    data.json を生成して docs/ に書き出す。
    戻り値: 生成データのサマリ dict。
    """
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("[BUILD] SQLite からデータを取得中...")
    event_rows, impact_rows = _load_from_sqlite()

    impact_map = _build_impact_map(impact_rows)
    events_list = _build_events_list(event_rows, impact_map)
    stats = _build_stats(events_list)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events":       events_list,
        "stats":        stats,
    }

    DATA_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"[BUILD] {DATA_JSON} 生成完了 ({len(events_list)} events)")

    return {
        "events_count":  len(events_list),
        "stats":         stats,
        "data_json_path": str(DATA_JSON),
        "data_json_size": DATA_JSON.stat().st_size,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = build()
    print(f"\n--- 生成完了 ---")
    print(f"events 件数   : {result['events_count']}")
    print(f"data.json サイズ: {result['data_json_size']:,} bytes")
    print(f"\nstats.by_event_type:")
    for etype, s in sorted(result["stats"]["by_event_type"].items()):
        avg1h  = f"{s['avg_1h']:+.2f}%" if s['avg_1h'] is not None else "N/A"
        avg24h = f"{s['avg_24h']:+.2f}%" if s['avg_24h'] is not None else "N/A"
        wr     = f"{s['win_rate_1h_gt5pct']}%" if s['win_rate_1h_gt5pct'] is not None else "N/A"
        print(f"  {etype:12s}: count={s['count']:3d}  +1h avg={avg1h:10s}  +24h avg={avg24h:10s}  win_rate={wr}")
