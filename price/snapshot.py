"""
price/snapshot.py — イベントから銘柄を抽出し、価格を取得して保存する

【処理】
  1. events テーブルの中で、まだ price_snapshots が無い行をピック
  2. raw_text から「シンボル（例: $SOL, BTC）」「CA（EVM: 0x..., Solana: base58）」を抽出
  3. Jupiter Price API → Binance REST → DexScreener の順で価格取得
  4. price_snapshots テーブルへ INSERT

使用法:
  from price.snapshot import run_price_snapshot
  n = run_price_snapshot()
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from shared.fetch_utils import fetch_json
from storage.db import get_conn, init_db

logger = logging.getLogger(__name__)


# --- 銘柄抽出 -------------------------------------------------------------

# $SYMBOL パターン（2-10文字の大文字英数字）
RE_DOLLAR_SYMBOL = re.compile(r"\$([A-Z][A-Z0-9]{1,9})\b")

# 裸の大文字ティッカー（BTC/ETH/SOL 等の主要銘柄のみホワイトリストで拾う）
MAJOR_TICKERS = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOGE",
    "TRX", "DOT", "MATIC", "LINK", "LTC", "TON", "SUI", "APT",
    "ARB", "OP", "PEPE", "SHIB", "BONK", "WIF", "JUP", "PYTH",
}
RE_BARE_TICKER = re.compile(r"\b([A-Z]{2,6})\b")

# EVM コントラクト: 0x + 40 hex
RE_EVM_CA = re.compile(r"\b0x[a-fA-F0-9]{40}\b")

# Solana CA: base58 32-44chars (簡易: 数字も英字も混在)
RE_SOL_CA = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")


def extract_tokens(text: str) -> List[Dict[str, str]]:
    """text から候補トークンのリストを返す。要素: {symbol, contract_addr, chain}"""
    if not text:
        return []

    results: List[Dict[str, str]] = []
    seen: set = set()

    # 1. $SYMBOL
    for m in RE_DOLLAR_SYMBOL.finditer(text):
        sym = m.group(1).upper()
        key = ("sym", sym)
        if key not in seen:
            seen.add(key)
            results.append({"symbol": sym, "contract_addr": "", "chain": "unknown"})

    # 2. 主要銘柄の裸ティッカー
    for m in RE_BARE_TICKER.finditer(text):
        sym = m.group(1).upper()
        if sym in MAJOR_TICKERS:
            key = ("sym", sym)
            if key not in seen:
                seen.add(key)
                results.append({"symbol": sym, "contract_addr": "", "chain": "unknown"})

    # 3. EVM CA
    for m in RE_EVM_CA.finditer(text):
        ca = m.group(0)
        key = ("ca", ca.lower())
        if key not in seen:
            seen.add(key)
            results.append({"symbol": "", "contract_addr": ca, "chain": "evm"})

    # 4. Solana CA（EVM CA にマッチしたものは除外済み）
    for m in RE_SOL_CA.finditer(text):
        ca = m.group(0)
        # EVM CA は 0x始まりで base58 的にも一致するが、上で拾われているのでスキップ
        if ca.startswith("0x"):
            continue
        # よく見る誤検出を抑止（大文字のみ/小文字のみ/短すぎは除外）
        if ca.isupper() or ca.islower():
            continue
        key = ("ca", ca)
        if key not in seen:
            seen.add(key)
            results.append({"symbol": "", "contract_addr": ca, "chain": "solana"})

    return results


# --- 価格取得 -------------------------------------------------------------

def _jupiter_price(symbol_or_ca: str) -> Optional[Tuple[float, dict]]:
    """Jupiter Price API v2 で価格取得。ids にはシンボル or mint。"""
    if not symbol_or_ca:
        return None
    url = "https://api.jup.ag/price/v2"
    data = fetch_json(url, params={"ids": symbol_or_ca})
    if not data:
        return None
    node = (data.get("data") or {}).get(symbol_or_ca) or {}
    price = node.get("price")
    if price is None:
        return None
    try:
        return float(price), data
    except (TypeError, ValueError):
        return None


def _binance_price(symbol: str) -> Optional[Tuple[float, dict]]:
    """Binance REST で USDT ペア価格取得。"""
    if not symbol:
        return None
    pair = f"{symbol.upper()}USDT"
    url = "https://api.binance.com/api/v3/ticker/price"
    data = fetch_json(url, params={"symbol": pair})
    if not data or "price" not in data:
        return None
    try:
        return float(data["price"]), data
    except (TypeError, ValueError):
        return None


def _dexscreener_price(ca: str) -> Optional[Tuple[float, dict]]:
    """DexScreener で CA からペアを検索し、priceUsd を取得。"""
    if not ca:
        return None
    url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
    data = fetch_json(url)
    if not data:
        return None
    pairs = data.get("pairs") or []
    if not pairs:
        return None
    # 最も流動性が高いペアを採用
    pairs_sorted = sorted(
        pairs,
        key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0),
        reverse=True,
    )
    top = pairs_sorted[0]
    price = top.get("priceUsd")
    if price is None:
        return None
    try:
        return float(price), top
    except (TypeError, ValueError):
        return None


def fetch_price(token: Dict[str, str]) -> Optional[Dict]:
    """Jupiter → Binance → DexScreener の順にフォールバック。
    戻り値: {price_usd, source, raw_response} or None

    【最適化】
    - symbol のみ（CA なし）の主要 CEX トークンは Jupiter をスキップ
      → Jupiter Price API v2 はシンボル文字列に 404 を返すため無駄なリトライを防ぐ
    - CA が Solana mint（非 EVM）の場合のみ Jupiter を試す
    """
    symbol = token.get("symbol") or ""
    ca = token.get("contract_addr") or ""
    chain = token.get("chain") or "unknown"

    # 1. Jupiter (Solana mint CA がある場合のみ)
    if ca and chain == "solana":
        r = _jupiter_price(ca)
        if r:
            price, raw = r
            return {"price_usd": price, "source": "jupiter", "raw_response": raw}

    # 2. Binance (symbol)
    if symbol:
        r = _binance_price(symbol)
        if r:
            price, raw = r
            return {"price_usd": price, "source": "binance", "raw_response": raw}

    # 3. DexScreener (CA がある場合)
    if ca:
        r = _dexscreener_price(ca)
        if r:
            price, raw = r
            return {"price_usd": price, "source": "dexscreener", "raw_response": raw}

    return None


# --- エントリポイント -----------------------------------------------------

def run_price_snapshot(limit: int = 50) -> int:
    """未取得イベントに対し銘柄抽出→価格取得→保存。戻り値: 保存件数。"""
    saved = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        init_db(conn)
        cur = conn.cursor()

        # price_snapshots がまだ無いイベントを取得
        cur.execute(
            """
            SELECT e.event_id, e.raw_text
              FROM events e
         LEFT JOIN price_snapshots p ON p.event_id = e.event_id
             WHERE p.snapshot_id IS NULL
          ORDER BY e.timestamp_utc DESC
             LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        logger.info(f"[SNAPSHOT] 対象イベント {len(rows)} 件")

        for row in rows:
            event_id = row["event_id"]
            tokens = extract_tokens(row["raw_text"] or "")
            if not tokens:
                # 銘柄抽出できない場合もダミー行を入れて「処理済み」マーク
                cur.execute(
                    """
                    INSERT INTO price_snapshots
                        (event_id, symbol, contract_addr, chain, price_usd, source, fetched_at_utc, raw_response)
                    VALUES (?, NULL, NULL, 'unknown', NULL, 'none', ?, NULL)
                    """,
                    (event_id, now_iso),
                )
                conn.commit()
                continue

            for tok in tokens:
                result = fetch_price(tok)
                price_usd = result["price_usd"] if result else None
                source = result["source"] if result else "none"
                raw_resp = json.dumps(result["raw_response"], ensure_ascii=False)[:8000] if result else None

                cur.execute(
                    """
                    INSERT INTO price_snapshots
                        (event_id, symbol, contract_addr, chain, price_usd, source, fetched_at_utc, raw_response)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        tok.get("symbol") or None,
                        tok.get("contract_addr") or None,
                        tok.get("chain") or "unknown",
                        price_usd,
                        source,
                        now_iso,
                        raw_resp,
                    ),
                )
                saved += 1
            conn.commit()

    logger.info(f"[SNAPSHOT] 保存 {saved} 件")
    return saved


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_price_snapshot()
