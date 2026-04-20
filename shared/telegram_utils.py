"""
shared/telegram_utils.py — Telegram 送信共通関数（boatrace-signal から流用）

【機能】
  - get_env(): 環境変数を取得（未設定時に明確なエラー）
    ローカル実行時はプロジェクトルートの .env を自動読み込み
  - send_message(): Telegram Bot API へメッセージ送信

使用法:
  from shared.telegram_utils import get_env, send_message
"""

import logging
import os
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

TG_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MESSAGE_CHARS = 4096

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
try:
    from dotenv import load_dotenv
    if _ENV_FILE.exists():
        load_dotenv(_ENV_FILE)
        logger.debug(f"[dotenv] .env 読み込み: {_ENV_FILE}")
except ImportError:
    pass


def get_env(key: str, default: str = None) -> str:
    """環境変数を取得。未設定なら明確なエラーを送出（default 指定時は default を返す）。"""
    val = os.environ.get(key)
    if not val:
        if default is not None:
            return default
        raise EnvironmentError(
            f"環境変数 {key} が設定されていません。\n"
            f"ローカル: プロジェクトルートに .env ファイルを作成してください。\n"
            f"  {key}=your_value\n"
            f"GitHub Actions: リポジトリ Settings → Secrets に登録済みか確認してください。"
        )
    return val


def send_message(
    token: str,
    chat_id: str,
    text: str,
    dry_run: bool = False,
    parse_mode: str = "HTML",
) -> bool:
    """Telegram にメッセージを送信する。dry_run=True ならログ出力のみ。"""
    if dry_run:
        logger.info("[DRY-RUN] 送信内容:\n" + "─" * 40 + "\n" + text + "\n" + "─" * 40)
        return True

    url = TG_API_BASE.format(token=token)
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            logger.info(f"  Telegram 送信成功 (message_id={data['result']['message_id']})")
            return True
        logger.error(f"  Telegram API エラー: {data}")
        return False
    except requests.RequestException as e:
        logger.error(f"  Telegram 送信失敗: {e}")
        return False
