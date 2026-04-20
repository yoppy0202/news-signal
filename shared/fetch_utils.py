"""
shared/fetch_utils.py — requests 共通ラッパー

【機能】
  - レート制限（REQUEST_INTERVAL 秒/リクエスト）
  - リトライ（最大 MAX_RETRIES 回）
  - User-Agent 設定
  - JSON フェッチ用のヘルパーも提供

使用法:
  from shared.fetch_utils import fetch_html, fetch_json, HEADERS
"""

import logging
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "news-signal/0.1 (research)"}
REQUEST_INTERVAL = 1   # リクエスト間隔（秒）
REQUEST_TIMEOUT  = 15  # タイムアウト（秒）
MAX_RETRIES      = 3   # リトライ回数


def fetch_html(url: str, interval: float = REQUEST_INTERVAL) -> Optional[str]:
    """指定URLのHTMLをフェッチして文字列で返す。失敗時は None。"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            enc = (resp.encoding or "").lower()
            if enc in ("shift_jis", "shift-jis", "sjis", "cp932", "x-sjis"):
                return resp.content.decode("cp932", errors="replace")
            return resp.text
        except requests.RequestException as e:
            if attempt < MAX_RETRIES:
                logger.warning(f"  フェッチ失敗 (試行{attempt}/{MAX_RETRIES}): {url} → {e}")
                time.sleep(interval)
            else:
                logger.error(f"  フェッチ失敗 (全試行終了): {url} → {e}")
                return None
    return None


def fetch_json(
    url: str,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    interval: float = REQUEST_INTERVAL,
) -> Optional[Any]:
    """指定URLからJSONを取得して dict/list で返す。失敗時は None。"""
    merged_headers = dict(HEADERS)
    if headers:
        merged_headers.update(headers)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, headers=merged_headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            if attempt < MAX_RETRIES:
                logger.warning(f"  JSONフェッチ失敗 (試行{attempt}/{MAX_RETRIES}): {url} → {e}")
                time.sleep(interval)
            else:
                logger.error(f"  JSONフェッチ失敗 (全試行終了): {url} → {e}")
                return None
    return None
