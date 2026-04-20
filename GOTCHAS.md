# GOTCHAS.md — news-signal 実体験ベースの罠集

> エラー解決直後にここへ追記する。一般論ではなく、このプロジェクトで踏んだ地雷のみ記録。

## Phase 3 — 実行時に踏んだ罠（2026-04-20）

### GitHub Actions からの docs/ 自動 push に `permissions` 設定が必要
- `dashboard_build.yml` でコミット & push するには `permissions: contents: write` が必要
- デフォルトの `GITHUB_TOKEN` は read-only なため push が 403 になる
- **対処**: workflow に `permissions: contents: write` を明示設定

### dashboard_build.yml の自動コミットで CI ループを防ぐ
- build.py が docs/data.json を更新 → push → また workflow がトリガーされる無限ループの恐れ
- **対処**: コミットメッセージに `[skip ci]` を付与（GitHub Actions はこれを見て自動スキップ）

### GitHub Pages の Source を "GitHub Actions" に設定しないと公開されない
- リポジトリ Settings → Pages → Source を **"Deploy from a branch"** のまま放置すると
  `docs/` に index.html があっても公開されない
- **対処**: Source を **"GitHub Actions"** に変更するか、
  別途 `actions/deploy-pages` を使う（現状は docs/ push 方式で運用）

---

## Phase 2 — 実行時に踏んだ罠（2026-04-20）

### Jupiter v6 は過去の時刻価格を取れない
- `https://price.jup.ag/v6/price?ids={ca}` は「現在価格」のみ返す
- T+1h 等の過去ウィンドウ計算には使えない
- **対処**: `age_minutes <= 5` の場合のみ Jupiter/DexScreener を使用し、過去データは Binance klines のみ。CEX シンボル以外の過去価格は pct_change=NULL で保存

### Binance klines の startTime は ms 単位
- `startTime=1234567890` (秒) を渡すと数十年前のデータが返ってくる
- `int(dt.timestamp() * 1000)` に変換して渡す必要がある

### gspread v6 では `gspread.authorize()` が非推奨
- 現バージョン（6.x）では `gspread.authorize(creds)` を使うと DeprecationWarning が出る
- 将来は `google.oauth2.service_account.Credentials` + `gspread.Client` への移行が必要
- Phase 2 では旧 oauth2client を継続使用（sol_signal_bot と実績のある組み合わせ）

### sheets_sync_state テーブルの ON CONFLICT 構文
- SQLite 3.24 以降で使える `INSERT ... ON CONFLICT DO UPDATE`（UPSERT）を使用
- Python 3.11 + SQLite 3.39+ なら問題ないが、古い環境では `INSERT OR REPLACE` に変更が必要

---

## Phase 1 — 実行時に踏んだ罠（2026-04-20）

### rekt.news/rss/ が XML ではなく HTML を返す
- `feedparser` が `bozo=1` を立て、entries が空になる
- ログ: `text/html; charset=utf-8 is not an XML media type`
- **対処**: 現状は bozo チェックでスキップ済み。rekt.news は別途 API/スクレイピングで対応が必要

### Jupiter Price API v2 がシンボル文字列に 404 を返す
- `https://api.jup.ag/price/v2?ids=BTC` → 404（Solana mint アドレスのみ受け付ける）
- BTC/ETH/SOL/XRP など CEX 系シンボルをそのまま Jupiter に投げると 3 回リトライして全部失敗、大幅に遅くなる（1 シンボル約 6 秒）
- **対処**: `fetch_price` を修正。`chain == 'solana'` かつ CA がある場合のみ Jupiter を使用し、その他は直接 Binance へ

### SQLite ALTER TABLE の重複実行
- 既に同名カラムがある状態で `ALTER TABLE ... ADD COLUMN` を実行すると例外が飛ぶ
- **対処**: `_run_migrations` で try/except してスキップ（`duplicate column name` は正常フロー）

---

## 初期セットアップ（Phase 0）
現時点で実運用で踏んだ罠はまだ無い。予期される注意点のみメモとして残す。

### 予期される罠 / 設計上のメモ

- **feedparser の bozo フラグ**
  RSS が XML 的に不正でも `feedparser.parse` はエラーを投げずに `bozo=1` を立てるだけ。
  `parsed.entries` が空かどうかを併用チェックしている（`collectors/rss_collector.py`）。

- **Solana CA と EVM CA の正規表現衝突**
  base58 の長さ域と hex は重なるため、`0x` 始まりは EVM として先に拾い、Solana 側ではスキップしている。
  それでも DNS名や長い base64 文字列が誤検出される可能性があるので、
  本番で誤検知が増えたら「CA はメッセージ中 URL の `?tokenAddress=` から抽出」に切り替えること。

- **Jupiter Price API v2**
  `https://api.jup.ag/price/v2?ids=<mint or symbol>` はキー不要だが、
  シンボルで叩いても解決しない場合がある（mint の方が確実）。
  フォールバックとして Binance→DexScreener を順に叩く。

- **Binance ticker/price**
  `symbol=BTCUSDT` のように連結が必要。`USDT` ペアが無い銘柄（例: 一部の日本上場のみトークン）は空振りする。

- **DexScreener**
  同じ CA に複数ペアがあるので、`liquidity.usd` で降順ソートしたトップを採用している。
  無名コインの場合 `priceUsd` が文字列で返るので必ず `float()` 変換する。

- **SQLite と OneDrive**
  DB が OneDrive 配下にあると、同期中にロックされて `database is locked` が出うる。
  その場合は `NEWS_SIGNAL_DB` を OneDrive 外（例: `C:/news-signal/data/x.sqlite3`）に逃がす。

- **GitHub Actions の DB 永続化**
  `actions/cache` はキー一致時のみ復元。run_id をキーにすると毎回ミスするので、
  現状 `restore-keys` でフォールバックしている（初回の履歴はクリーンスタート前提）。
  長期永続は Phase 1 で外部ストレージ（S3 / R2 / Supabase 等）に移すこと。
