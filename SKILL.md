# SKILL.md — news-signal 地図・判断基準

## プロジェクト概要
ニュース（RSS / X 等）と価格スナップショットを紐付けて収集し、
「イベント → 価格反応」の観測データを貯めることを目的とする。
将来的にシグナル化・Telegram通知へ拡張する。

## Phase 1 のゴール（完了）
- VADER 感情分析 + キーワードルールベースで `event_type` を分類
- `events` テーブルに `sentiment / sentiment_label / event_type` カラム追加（ALTER TABLE）
- RSS フィード拡充（rekt.news / Solana 公式）
- `main.py` の処理順序を rss → sentiment → snapshot に更新
- Jupiter への無駄リトライ排除（Solana CA がある場合のみ使用）

## Phase 0 のゴール（完了）
- RSS フィードをポーリングして `events` テーブルに保存
- 本文から銘柄（シンボル / CA）を抽出し、Jupiter → Binance → DexScreener の順で価格取得
- `price_snapshots` テーブルに保存
- GitHub Actions で15分ごとに `main.py` を走らせる
- ローカル動作確認まで（Render 等のデプロイはまだ）

## ディレクトリ構成
```
news-signal/
├── main.py                     # エントリポイント（RSS→感情分析→価格スナップショット）
├── requirements.txt
├── .env.example
├── .gitignore
├── .github/workflows/collector.yml  # 15分おきに main.py 実行
│
├── shared/                     # boatrace-signal から流用
│   ├── fetch_utils.py          # requests ラッパー + fetch_json
│   └── telegram_utils.py       # get_env / send_message
│
├── storage/
│   └── db.py                   # SQLite 初期化 + マイグレーション
│
├── collectors/
│   └── rss_collector.py        # feedparser による RSS ポーリング（7媒体）
│
├── processors/                 # Phase 1 追加
│   └── sentiment.py            # VADER感情分析 + キーワードevent_type分類
│
├── price/
│   └── snapshot.py             # シンボル/CA抽出 + 価格取得
│
├── SKILL.md
├── GOTCHAS.md
└── MASTER_LOG.md
```

## SKILL 設計パターン（該当フェーズ）
- **Pipeline**: `main.py` が `rss_collector → snapshot` を順に実行
- **Tool Wrapper**: `shared/fetch_utils.py` が HTTP 取得規約を注入
- **Generator**: `price/snapshot.py` が構造化行（symbol/ca/chain/price）を生成

## 判断基準
- 新しい「外部データ源」追加 → `collectors/<name>.py`
- 新しい「価格ソース」追加 → `price/snapshot.py` の fetch_price にフォールバック追加
- スキーマ変更は `storage/db.py` の `SCHEMA` を直接更新（マイグレーションは Phase 1 以降）
- **無料枠・読み取りのみ**の API を優先。鍵が要るものは `.env` 経由で渡す
- `events.event_hash` は `source|url|title` の SHA256。重複排除はこの列で一意化

## 将来の拡張ポイント（メモ）
- X(Twitter) collector の追加
- LLM による銘柄抽出精度向上（現状は正規表現＋主要ティッカーホワイトリスト）
- n分後 / 1h / 24h の価格追跡 → リターン計算
- Telegram 通知（`shared/telegram_utils.py` を再利用）
- Render へのバックグラウンドワーカー化
