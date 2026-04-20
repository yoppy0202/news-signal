# MASTER_LOG.md — news-signal セッションログ

> 新しいタスクは P-XX で採番、セッション終了前に必ず更新。

## タスク一覧
| ID   | 状態         | 優先度 | 概要                                                                             |
|------|--------------|--------|----------------------------------------------------------------------------------|
| P-01 | Done         | High   | Phase 0 雛形作成（shared/storage/collectors/price/main.py + Actions）            |
| P-02 | Done         | High   | Phase 1 実装（sentiment/DB拡張/RSSフィード追加/main.py更新）                     |
| P-03 | Done         | High   | Phase 2 実装（price_impact / sheets_sync / impact_calc.yml）                     |
| P-04 | Done         | High   | Phase 3 実装（dashboard/build.py / docs/index.html / dashboard_build.yml）       |
| P-05 | Open         | Mid    | rekt.news の代替取得方法（RSS が HTML を返すため feedparser 不可）               |
| P-05 | Open         | Mid    | RSS_FEEDS を YAML/JSON 外出しして追加容易に                                       |
| P-06 | Open         | Mid    | X (Twitter) collector を追加（Nitter or 公式API）                                |
| P-07 | Open         | Mid    | 銘柄抽出を LLM / NER で高精度化                                                   |
| P-08 | Open         | Low    | Telegram 通知（`shared/telegram_utils.py` 再利用）                                |
| P-09 | Open         | Low    | Render バックグラウンドワーカー化（cron-job.org 併用 or 常駐）                    |
| P-10 | Open         | Low    | DB を外部ストレージに移行（S3 / R2 / Supabase 等）                                |
| P-11 | Open         | Low    | gspread v6 対応（oauth2client → google-auth への移行）                            |

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

### 2026-04-20 — CLAUDE.md 作成・設定確認 (P-01b)
- `~/.claude/settings.json` / `~/.claude/CLAUDE.md` の内容を確認
- `news-signal/CLAUDE.md` が存在しなかったため新規作成
- Permission Mode / コスト管理 / セキュリティ の3セクションを追記
- ポイント: Surf API はバッチのみ、.env は gitignore 済み確認

### 2026-04-20 — Phase 1 実装 (P-02)
- `processors/sentiment.py` 新規作成
  - VADER で compound スコア（-1.0〜1.0）計算
  - キーワードルールで `event_type` 分類（listing/hack/whale_move/macro/narrative）
- `storage/db.py` 拡張: `_run_migrations` 関数で既存DB に sentiment/sentiment_label/event_type カラムを ALTER TABLE 追加
- `collectors/rss_collector.py` に rekt.news / solana_official フィードを追加
  - rekt.news は RSS が HTML を返すため feedparser で取得不可（GOTCHAS 追記）
- `main.py` を rss → sentiment → snapshot の順に更新
- `requirements.txt` に vaderSentiment>=3.3.2 追加
- `price/snapshot.py` 最適化: Solana CA がない場合は Jupiter をスキップ
  - Jupiter Price API v2 はシンボル文字列で 404 → 1シンボルあたり 6 秒の無駄を排除
  - 実行時間 62秒 → 26秒 に短縮

**動作確認結果（172件）**
| 指標              | 結果                                           |
|------------------|------------------------------------------------|
| 取得イベント数    | 172 件（前回からの差分 28 件追加）             |
| sentiment_label  | positive: 79 / negative: 56 / neutral: 37      |
| event_type       | narrative: 47 / macro: 38 / hack: 35 / 分類なし: 46 / whale_move: 4 / listing: 2 |
| price_snapshots  | 160 件（binance: 33 / none: 127）              |
| 価格取得成功例   | BTC $75,277 / SOL $84.84 / XRP $1.41          |

### 2026-04-20 — Phase 2 実装 (P-03)
- `price/impact_calculator.py` 新規作成
  - T+5m/15m/1h/4h/24h の価格変化率を計算
  - Binance klines（過去時刻対応）→ Jupiter v6（直近 5 分以内のみ）→ DexScreener
  - `price_impact` テーブルへ INSERT OR IGNORE
- `storage/db.py` に `price_impact` テーブル追加
- `storage/sheets_sync.py` 新規作成
  - `ns_events` / `ns_price_impact` タブを自動作成して差分追記
  - `sheets_sync_state` テーブルで最終同期 rowid/id を管理
  - GOOGLE_CREDENTIALS（JSON環境変数）または credentials.json で認証
- `.github/workflows/impact_calc.yml` 新規作成（毎時 :05 分に実行）
- `requirements.txt` に gspread / oauth2client 追加

**動作確認結果**
| 指標 | 結果 |
|---|---|
| 計算 window 数 | 96 件（5 window × 20 events ≒ 100 - 未来ウィンドウ除外）|
| pct_change 成功例 | DOGE +7.91%（t_plus_1h）/ ETH +6.17%（t_plus_4h）/ SOL -5.89%（t_plus_1h）|
| Sheets sync | events=172 / price_impact=96 → Google Sheets 書き込み成功 ✓ |
| Sheets sync（未設定時） | SHEETS_ID 未設定 → 正常スキップ（ok=False）|

**GitHub Actions Secrets に追加が必要**
- `SHEETS_ID`: スプレッドシート ID
- `GOOGLE_CREDENTIALS`: サービスアカウント credentials.json の1行JSON

### 2026-04-20 — Phase 3 実装 (P-04)
- `dashboard/build.py` 新規作成
  - SQLite から events / price_impact を取得して docs/data.json を生成
  - SHEETS_ID 未設定時は SQLite 直接読み取り（フォールバック）
  - event_type 別の統計（count / avg_1h / avg_24h / win_rate）を計算
- `docs/index.html` 新規作成（ダークテーマ静的ダッシュボード）
  - Chart.js（CDN）で event_type × +1h 平均変化率を棒グラフ表示
  - イベントフィード（タイムライン形式、最新200件）
  - sentiment / event_type をカラーバッジで表示
  - プラス変化率=緑 / マイナス=赤 / モバイル対応
- `.github/workflows/dashboard_build.yml` 新規作成（毎時:10分実行）
  - docs/ に data.json を生成後、[skip ci] タグ付きで自動コミット & push

**動作確認結果**
| 指標 | 結果 |
|---|---|
| events 件数 | 172 件 |
| data.json サイズ | 134,618 bytes |
| stats.by_event_type (全タイプ) | 下表参照 |

| event_type | 件数 | +1h 平均 | +24h 平均 | 勝率(+5%超) |
|---|---|---|---|---|
| hack       | 35 | +3.00% | +0.81% | 0.0% |
| listing    | 2  | +5.49% | +0.91% | 100.0% |
| macro      | 38 | +1.43% | +0.49% | 0.0% |
| narrative  | 47 | +2.97% | +1.15% | 0.0% |
| unknown    | 46 | +1.44% | +1.17% | 16.7% |
| whale_move | 4  | N/A    | N/A    | N/A |

**GitHub Pages 設定手順**
1. リポジトリ Settings → Pages → Source: **GitHub Actions** を選択
2. `dashboard_build.yml` が master に push されると自動デプロイ

### 次セッションの開始手順
1. `MASTER_LOG.md` の Open タスクを確認
2. 通常は P-02（ローカル初回実行）から。`python main.py` でエラーが出たら GOTCHAS.md に追記
3. RSS_FEEDS の拡張を検討するなら P-03
