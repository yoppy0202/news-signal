"""
scripts/telegram_auth.py — Telegram セッション文字列を生成する初回認証スクリプト

【手順】
  1. https://my.telegram.org にアクセスしてアプリを作成
     → API_ID（整数）と API_HASH（文字列）を取得
  2. .env に以下を追記（または実行時に対話入力）：
       TELEGRAM_API_ID=12345678
       TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890
       TELEGRAM_PHONE=+819012345678
  3. このスクリプトを実行：
       python scripts/telegram_auth.py
  4. SMS または Telegram アプリに届いた認証コードを入力
  5. 出力された TELEGRAM_SESSION 文字列を GitHub Secrets に登録

【注意】
  - このスクリプトはローカルでのみ実行（GitHub Actions では不要）
  - セッション文字列はアカウントへのフルアクセス権を持つため厳重管理
  - .env に保存する場合: TELEGRAM_SESSION=<セッション文字列>
"""

import asyncio
import os
import sys
from pathlib import Path


def _load_env():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    try:
        from dotenv import load_dotenv
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass


async def main():
    _load_env()

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        print("エラー: telethon がインストールされていません。")
        print("  pip install telethon")
        sys.exit(1)

    # 環境変数 → 対話入力 の優先順
    api_id_str = os.environ.get("TELEGRAM_API_ID") or input("TELEGRAM_API_ID: ").strip()
    api_hash   = os.environ.get("TELEGRAM_API_HASH") or input("TELEGRAM_API_HASH: ").strip()
    phone      = os.environ.get("TELEGRAM_PHONE") or input("電話番号 (例: +819012345678): ").strip()

    try:
        api_id = int(api_id_str)
    except (ValueError, TypeError):
        print(f"エラー: TELEGRAM_API_ID は整数である必要があります: {api_id_str!r}")
        sys.exit(1)

    print("\nTelegram に接続して認証を開始します...")
    print("SMS または Telegram アプリに認証コードが届きます。")

    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        await client.start(phone=phone)
        session_string = client.session.save()

    print("\n" + "=" * 60)
    print("認証成功！以下のセッション文字列を GitHub Secrets に登録してください。")
    print("")
    print("  Secret 名: TELEGRAM_SESSION")
    print("  値（以下をそのまま貼り付け）:")
    print("=" * 60)
    print(session_string)
    print("=" * 60)
    print("")
    print(".env に保存する場合は以下を追記：")
    print(f"  TELEGRAM_SESSION={session_string}")
    print("")
    print("注意: このセッション文字列はアカウントへのアクセス権を持ちます。")
    print("      絶対に公開リポジトリにコミットしないでください。")


if __name__ == "__main__":
    asyncio.run(main())
