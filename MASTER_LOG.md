# MASTER_LOG.md — news-signal セッションログ

> 新しいタスクは P-XX で採番、セッション終了前に必ず更新。

## タスク一覧
| ID   | 状態         | 優先度 | 概要                                                                 |
|------|--------------|--------|----------------------------------------------------------------------|
| P-01 | Done         | High   | Phase 0 雛形作成（shared/storage/collectors/price/main.py + Actions）|
| P-02 | Open         | High   | ローカルで `python main.py` が成功することを確認（初回実行）          |
| P-03 | Open         | Mid    | RSS_FEEDS を YAML/JSON 外出しして追加容易に                           |
| P-04 | Open         | Mid    | X (Twitter) collector を追加（Nitter or 公式API）                     |
| P-05 | Open         | Mid    | 銘柄抽出を LLM / NER で高精度化                                       |
| P-06 | Open         | Low    | n 分後 / 1h / 24h のフォロー価格取得 → リターン列                     |
| P-07 | Open         | Low    | Telegram 通知（`shared/telegram_utils.py` 再利用）                    |
| P-08 | Open         | Low    | Render バックグラウンドワーカー化（cron-job.org 併用 or 常駐）        |
| P-09 | Open         | Low    | DB を外部ストレージに移行（S3 / R2 / Supabase 等）                    |

## セッションログ

### 2026-04-20 — 初期セットアップ (P-01)
- news-signal リポジトリを新規作成
- `shared/fetch_utils.py` / `shared/telegram_utils.py` は boatrace-signal から流用
  - `fetch_utils` には `fetch_json` を追加
- `storage/db.py`：`events` / `price_snapshots` の2テーブル
- `collectors/rss_collector.py`：feedparser で5媒体をポーリング、event_hash で重複排除
- `price/snapshot.py`：$SYMBOL / 主要ティッカー / EVM CA / Solana CA を抽出し、
  Jupiter → Binance → DexScreener の順でフォールバック
- `main.py`：RSS → スナップショットの順に実行
- `.github/workflows/collector.yml`：15分おきに実行、DB は actions/cache で一時保持
- 3ファイル（SKILL/GOTCHAS/MASTER_LOG）初期化

### 次セッションの開始手順
1. `MASTER_LOG.md` の Open タスクを確認
2. 通常は P-02（ローカル初回実行）から。`python main.py` でエラーが出たら GOTCHAS.md に追記
3. RSS_FEEDS の拡張を検討するなら P-03
