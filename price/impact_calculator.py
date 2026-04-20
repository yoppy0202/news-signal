"""
price/impact_calculator.py — イベント T0 価格を基準にした価格変化率計算

【処理】
  1. price_snapshots に T0 価格がある (event_id, symbol/ca) を取得
  2. イベント timestamp_utc を基準に T+5m/15m/1h/4h/24h の価格を取得
  3. pct_change = (price_tx - price_t0) / price_t0 * 100 を計算
  4. price_impact テーブルに INSERT OR IGNORE（UNIQUE 制約で二重計算防止）

【価格取得優先順】
  Binance klines（CEX シンボル）→ Jupiter v6（Solana CA）→ DexScreener

エントリポイント:
  from price.impact_calculator import run
  run()
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from shared.fetch_utils import fetch_json
from storage.db import get_conn, init_db

logger = logging.getLogger(__name__)

# 計算対象ウィンドウ
WINDOWS: List[Tuple[str, int]] = [
    ("t_plus_5m",  5),
    ("t_plus_15m", 15),
    ("t_plus_1h",  60),
    ("t_plus_4h",  240),
    ("t_plus_24h", 1440),
]

# Binance で USDT ペアが存在するメジャーシンボル
CEX_SYMBOLS = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOGE",
    "TRX", "DOT", "MATIC", "LINK", "LTC", "TON", "SUI", "APT",
    "ARB", "OP", "PEPE", "SHIB", "BONK", "WIF", "JUP", "PYTH",
}


# ---- 価格取得関数 -----------------------------------------------------------

def _binance_kline_price(symbol: str, target_dt: datetime) -> Optional[float]:
    """Binance klines API で target_dt 時点の終値を返す（1m 足の最初の足）。"""
    if symbol.upper() not in CEX_SYMBOLS:
        return None
    pair = f"{symbol.upper()}USDT"
    start_ms = int(target_dt.timestamp() * 1000)
    data = fetch_json(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": pair, "interval": "1m", "startTime": start_ms, "limit": 1},
        interval=0.2,
    )
    if not data or not isinstance(data, list) or not data[0]:
        return None
    try:
        return float(data[0][4])  # close price
    except (IndexError, TypeError, ValueError):
        return None


def _jupiter_v6_price(ca: str) -> Optional[float]:
    """Jupiter Price API v6 で CA の現在価格を返す。"""
    if not ca:
        return None
    data = fetch_json(f"https://price.jup.ag/v6/price", params={"ids": ca}, interval=0.2)
    if not data:
        return None
    node = (data.get("data") or {}).get(ca) or {}
    price = node.get("price")
    try:
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None


def _dexscreener_price(ca: str) -> Optional[float]:
    """DexScreener で CA の価格を返す（流動性最大ペア）。"""
    if not ca:
        return None
    data = fetch_json(
        f"https://api.dexscreener.com/latest/dex/tokens/{ca}", interval=0.5
    )
    if not data:
        return None
    pairs = data.get("pairs") or []
    if not pairs:
        return None
    top = max(
        pairs,
        key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0),
    )
    try:
        return float(top.get("priceUsd") or 0) or None
    except (TypeError, ValueError):
        return None


def fetch_price_at(
    symbol: str,
    ca: str,
    chain: str,
    target_dt: datetime,
) -> Optional[float]:
    """
    Binance klines → Jupiter v6 → DexScreener の優先順で price_usd を返す。
    過去の特定時刻を取れるのは Binance klines のみ。
    Jupiter/DexScreener は「今の価格」を返すため、
    target_dt が過去 5 分以内の場合のみ使用する（それ以外はスキップ）。
    """
    now = datetime.now(timezone.utc)
    age_minutes = (now - target_dt).total_seconds() / 60

    # 1. Binance klines（過去・現在どちらも対応）
    if symbol and symbol.upper() in CEX_SYMBOLS:
        p = _binance_kline_price(symbol, target_dt)
        if p:
            return p

    # 2. Jupiter v6 / DexScreener は直近 5 分以内のウィンドウのみ
    if age_minutes <= 5:
        if ca and chain == "solana":
            p = _jupiter_v6_price(ca)
            if p:
                return p
        if ca:
            p = _dexscreener_price(ca)
            if p:
                return p

    return None


# ---- メイン処理 ------------------------------------------------------------

def run(limit: int = 100) -> int:
    """
    T0 価格のあるイベントに対し、各ウィンドウの pct_change を計算して保存する。
    戻り値: 保存した price_impact 行数。
    """
    saved = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        init_db(conn)
        cur = conn.cursor()

        # T0 価格がある (event_id, symbol, ca, chain, price_t0, event_timestamp) を取得
        cur.execute(
            """
            SELECT
                ps.event_id,
                ps.symbol,
                ps.contract_addr  AS ca,
                ps.chain,
                ps.price_usd      AS price_t0,
                e.timestamp_utc
              FROM price_snapshots ps
              JOIN events e ON e.event_id = ps.event_id
             WHERE ps.price_usd IS NOT NULL
          ORDER BY e.timestamp_utc DESC
             LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        logger.info(f"[IMPACT] 対象 {len(rows)} 件")

        for row in rows:
            event_id   = row["event_id"]
            symbol     = row["symbol"] or ""
            ca         = row["ca"] or ""
            chain      = row["chain"] or "unknown"
            price_t0   = row["price_t0"]
            token      = symbol or ca

            try:
                event_dt = datetime.fromisoformat(row["timestamp_utc"])
                if event_dt.tzinfo is None:
                    event_dt = event_dt.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                logger.warning(f"[IMPACT] timestamp 解析失敗 event_id={event_id}")
                continue

            for window_label, minutes in WINDOWS:
                target_dt = event_dt + timedelta(minutes=minutes)
                # 未来のウィンドウはスキップ
                if target_dt > datetime.now(timezone.utc):
                    continue

                price_tx = fetch_price_at(symbol, ca, chain, target_dt)
                if price_tx is None:
                    pct_change = None
                else:
                    pct_change = (price_tx - price_t0) / price_t0 * 100 if price_t0 else None

                try:
                    cur.execute(
                        """
                        INSERT OR IGNORE INTO price_impact
                            (event_id, token, window_label, price, pct_change, calculated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (event_id, token, window_label, price_tx, pct_change, now_iso),
                    )
                    if cur.rowcount > 0:
                        saved += 1
                except Exception as e:
                    logger.error(f"[IMPACT] insert error: {e}")
                    continue

            conn.commit()
            # Binance レート制限への配慮
            time.sleep(0.1)

    logger.info(f"[IMPACT] 保存 {saved} 件")
    return saved


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
