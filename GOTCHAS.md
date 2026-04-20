# GOTCHAS.md — news-signal 実体験ベースの罠集

> エラー解決直後にここへ追記する。一般論ではなく、このプロジェクトで踏んだ地雷のみ記録。

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
