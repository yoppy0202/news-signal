"""
main.py — news-signal エントリポイント

【処理順序】（Phase 4）
  1. RSS コレクタ      → events テーブルに保存
  2. 感情分析          → events.sentiment / sentiment_label / event_type を UPDATE
  3. 価格スナップショット → price_snapshots に保存
  4. アラート通知      → 高インパクトイベントを Telegram に通知

【実行】
  - ローカル: python main.py
  - GitHub Actions: .github/workflows/collector.yml から15分おき
  - Render: バックグラウンドワーカー想定（Phase 1 以降）
"""

import logging
import sys
import time

from collectors.rss_collector import run_rss_collector
from notifier.alert import run as run_alert
from price.snapshot import run_price_snapshot
from processors.sentiment import run_sentiment
from storage.db import DB_PATH, get_conn, init_db


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def main() -> int:
    setup_logging()
    log = logging.getLogger("main")
    log.info(f"news-signal collector 起動 (DB: {DB_PATH})")

    t0 = time.time()

    # 0. DB 初期化（冪等 + マイグレーション）
    with get_conn() as conn:
        init_db(conn)

    # 1. RSS 収集
    try:
        new_events = run_rss_collector()
    except Exception as e:
        log.exception(f"RSS collector 失敗: {e}")
        new_events = 0

    # 2. 感情分析
    try:
        analyzed = run_sentiment()
    except Exception as e:
        log.exception(f"sentiment 失敗: {e}")
        analyzed = 0

    # 3. 価格スナップショット
    try:
        saved_snapshots = run_price_snapshot()
    except Exception as e:
        log.exception(f"price snapshot 失敗: {e}")
        saved_snapshots = 0

    # 4. アラート通知
    try:
        alert_result = run_alert()
    except Exception as e:
        log.exception(f"alert 失敗: {e}")
        alert_result = {"candidates": 0, "sent": 0, "dry_run": True}

    elapsed = time.time() - t0
    log.info(
        f"完了: 新規イベント {new_events} 件 / 感情分析 {analyzed} 件 / "
        f"スナップショット {saved_snapshots} 件 / "
        f"アラート候補 {alert_result['candidates']} 件 (送信 {alert_result['sent']} 件) / "
        f"経過 {elapsed:.1f}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
