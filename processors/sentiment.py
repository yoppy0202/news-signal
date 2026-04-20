"""
processors/sentiment.py — VADER 感情分析 + キーワードルールベース分類

【処理】
  1. events テーブルから未分析レコードを取得
  2. VADER で sentiment スコア（-1.0〜1.0）を計算
  3. キーワード辞書で event_type を分類
  4. events テーブルに sentiment / sentiment_label / event_type を UPDATE

使用法:
  from processors.sentiment import run_sentiment
  n = run_sentiment()
"""

import logging
from typing import Optional

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from storage.db import get_conn, init_db

logger = logging.getLogger(__name__)

# ---- キーワード辞書 --------------------------------------------------------

POSITIVE_KEYWORDS = ["listing", "partnership", "launch", "upgrade", "bullish"]
NEGATIVE_KEYWORDS = ["hack", "exploit", "rug", "scam", "bearish", "crash"]

EVENT_TYPE_KEYWORDS = {
    "listing":    ["listed", "listing", "now on", "available on"],
    "hack":       ["hack", "exploit", "drained", "stolen", "attack"],
    "whale_move": ["whale", "large transfer", "smart money"],
    "macro":      ["fed", "cpi", "etf", "sec", "regulation"],
    "narrative":  ["ai", "rwa", "meme", "defi", "layer2"],
}

# VADER の compound スコアしきい値
POSITIVE_THRESHOLD = 0.05
NEGATIVE_THRESHOLD = -0.05

_analyzer: Optional[SentimentIntensityAnalyzer] = None


def _get_analyzer() -> SentimentIntensityAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = SentimentIntensityAnalyzer()
    return _analyzer


def classify_sentiment(compound: float) -> str:
    """compound スコアから sentiment_label を返す。"""
    if compound >= POSITIVE_THRESHOLD:
        return "positive"
    if compound <= NEGATIVE_THRESHOLD:
        return "negative"
    return "neutral"


def classify_event_type(text: str) -> Optional[str]:
    """テキストをキーワードで照合し event_type を返す。複数マッチは先勝ち。"""
    lower = text.lower()
    for etype, keywords in EVENT_TYPE_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return etype
    return None


def analyze_text(text: str) -> dict:
    """1テキストを解析して {sentiment, sentiment_label, event_type} を返す。"""
    analyzer = _get_analyzer()
    scores = analyzer.polarity_scores(text)
    compound = scores["compound"]
    label = classify_sentiment(compound)
    etype = classify_event_type(text)
    return {
        "sentiment":       compound,
        "sentiment_label": label,
        "event_type":      etype,
    }


def run_sentiment(limit: int = 200) -> int:
    """
    未分析イベント（sentiment IS NULL）を処理してDBに書き込む。
    戻り値: 更新件数。
    """
    updated = 0
    with get_conn() as conn:
        init_db(conn)
        cur = conn.cursor()

        cur.execute(
            """
            SELECT event_id, raw_text
              FROM events
             WHERE sentiment IS NULL
             ORDER BY timestamp_utc DESC
             LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        logger.info(f"[SENTIMENT] 対象イベント {len(rows)} 件")

        for row in rows:
            result = analyze_text(row["raw_text"] or "")
            cur.execute(
                """
                UPDATE events
                   SET sentiment       = ?,
                       sentiment_label = ?,
                       event_type      = ?
                 WHERE event_id = ?
                """,
                (
                    result["sentiment"],
                    result["sentiment_label"],
                    result["event_type"],
                    row["event_id"],
                ),
            )
            updated += 1

        conn.commit()

    logger.info(f"[SENTIMENT] 更新 {updated} 件")
    return updated


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_sentiment()
