# news-signal プロジェクト固有の Claude 設定

## Permission Mode
- デフォルト: autoモード（classifier判定による自動承認）
- 破壊的操作（rm -rf /, sudo, dd, force push）はdeny済み
- 通常のファイル操作・git・Python実行はauto-approve

## コスト管理（news-signal固有）
- Surf APIはバッチのみ。リアルタイムトリガー禁止
- Jupiter/Binance/DexScreener APIはキーレスのため自動実行OK
- Google Sheets書き込みは1時間に1回までに制限

## セキュリティ（news-signal固有）
- .envファイルは絶対にgit管理しない（.gitignore確認済み）
- APIキーを含むコードは実行前に必ず表示して確認を取る
- Helius/Nansen連携は将来追加時に確認ステップを必須とする
