"""
main.py — news-signal Phase 0 エントリポイント

【処理】
  1. RSS コレクタを実行し events テーブルに保存
  2. 価格スナップショット処理で銘柄抽出→価格取得→price_snapshots に保存

【実行】
  - ローカル: python main.py
  - GitHub Actions: .github/workflows/collector.yml から15分おき
  - Render: バックグラウンドワーカー想定（Phase 1 以降）
"""

import logging
import sys
import time

from collectors.rss_collector import run_rss_collector
from price.snapshot import run_price_snapshot
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

    # 0. DB 初期化（冪等）
    with get_conn() as conn:
        init_db(conn)

    # 1. RSS 収集
    try:
        new_events = run_rss_collector()
    except Exception as e:
        log.exception(f"RSS collector 失敗: {e}")
        new_events = 0

    # 2. 価格スナップショット
    try:
        saved_snapshots = run_price_snapshot()
    except Exception as e:
        log.exception(f"price snapshot 失敗: {e}")
        saved_snapshots = 0

    elapsed = time.time() - t0
    log.info(
        f"完了: 新規イベント {new_events} 件 / スナップショット {saved_snapshots} 件 "
        f"/ 経過 {elapsed:.1f}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
