"""
BOB/USDT P2P collector – Phase 0, week 1.

Every INTERVAL seconds it snapshots the Binance P2P BOB/USDT order book
(both sides), stores every ad in SQLite and computes a depth-weighted
reference rate for a fixed trade size Q (USDT).

Run:
    pip install httpx
    python p2p_collector.py              # loop forever, every 60 s
    python p2p_collector.py --once       # one snapshot (to test)
    python p2p_collector.py --q 500 --interval 120 --db data/p2p.sqlite

Side convention (Binance's tradeType is from the TAKER's view):
    tradeType=BUY  -> ads of people SELLING USDT -> our ASK side
    tradeType=SELL -> ads of people BUYING USDT  -> our BID side
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

URL = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (research collector; BOB/USD reference rate)",
}
ROWS_PER_PAGE = 20
MAX_PAGES = 5            # up to 100 ads per side is plenty for BOB
PAGE_PAUSE_S = 1.0       # be polite: one request per second

log = logging.getLogger("p2p")


@dataclass
class Ad:
    side: str            # "ask" or "bid"
    adv_no: str
    price: float         # BOB per USDT
    qty_usdt: float      # tradable USDT left in the ad
    min_bob: float       # min order (BOB)
    max_bob: float       # max order (BOB)
    pay_methods: str
    merchant: str
    user_no: str
    month_orders: int
    finish_rate: float
    user_type: str


# --------------------------------------------------------------------------- fetch
def fetch_side(client: httpx.Client, trade_type: str) -> list[Ad]:
    side = "ask" if trade_type == "BUY" else "bid"
    ads: list[Ad] = []
    for page in range(1, MAX_PAGES + 1):
        body = {
            "asset": "USDT", "fiat": "BOB", "tradeType": trade_type,
            "page": page, "rows": ROWS_PER_PAGE,
            "payTypes": [], "publisherType": None,
        }
        r = client.post(URL, json=body, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json().get("data") or []
        ads.extend(parse_ads(data, side))
        if len(data) < ROWS_PER_PAGE:
            break
        time.sleep(PAGE_PAUSE_S)
    return ads


def parse_ads(data: list[dict], side: str) -> list[Ad]:
    out = []
    for item in data:
        adv, who = item.get("adv", {}), item.get("advertiser", {})
        try:
            out.append(Ad(
                side=side,
                adv_no=str(adv.get("advNo")),
                price=float(adv["price"]),
                qty_usdt=float(adv.get("surplusAmount") or adv.get("tradableQuantity") or 0),
                min_bob=float(adv.get("minSingleTransAmount") or 0),
                max_bob=float(adv.get("maxSingleTransAmount") or 0),
                pay_methods=",".join(m.get("identifier", "") for m in adv.get("tradeMethods", [])),
                merchant=str(who.get("nickName", "")),
                user_no=str(who.get("userNo", "")),
                month_orders=int(who.get("monthOrderCount") or 0),
                finish_rate=float(who.get("monthFinishRate") or 0),
                user_type=str(who.get("userType", "")),
            ))
        except (KeyError, TypeError, ValueError) as e:
            log.warning("skipping malformed ad: %s", e)
    return out


# --------------------------------------------------------------------------- rate
def vwap_for_size(ads: list[Ad], q_usdt: float, best_first_desc: bool) -> tuple[float | None, float]:
    """Average price to fill q_usdt against the ads, best price first.

    An ad can be used only if a trade of the needed size fits its min/max
    order limits (in BOB). Returns (vwap or None if not enough depth, depth_used).
    """
    book = sorted(ads, key=lambda a: a.price, reverse=best_first_desc)
    remaining, cost = q_usdt, 0.0
    for a in book:
        if remaining <= 1e-9:
            break
        cap_usdt = min(a.qty_usdt, a.max_bob / a.price if a.max_bob else a.qty_usdt)
        take = min(remaining, cap_usdt)
        if take * a.price < a.min_bob:      # below this ad's minimum order
            continue
        cost += take * a.price
        remaining -= take
    filled = q_usdt - remaining
    return (cost / filled if remaining <= 1e-9 else None), filled


def reference_rate(asks: list[Ad], bids: list[Ad], q_usdt: float) -> dict:
    ask_vwap, _ = vwap_for_size(asks, q_usdt, best_first_desc=False)  # cheapest sellers first
    bid_vwap, _ = vwap_for_size(bids, q_usdt, best_first_desc=True)   # highest buyers first
    mid = (ask_vwap + bid_vwap) / 2 if ask_vwap and bid_vwap else None
    best_ask = min((a.price for a in asks), default=None)
    best_bid = max((b.price for b in bids), default=None)
    return {
        "q_usdt": q_usdt,
        "best_ask": best_ask, "best_bid": best_bid,
        "ask_vwap": ask_vwap, "bid_vwap": bid_vwap, "mid": mid,
        "spread_pct": (ask_vwap - bid_vwap) / mid * 100 if mid else None,
        "depth_ask_usdt": sum(a.qty_usdt for a in asks),
        "depth_bid_usdt": sum(b.qty_usdt for b in bids),
        "n_ask": len(asks), "n_bid": len(bids),
    }


# --------------------------------------------------------------------------- storage
SCHEMA = """
CREATE TABLE IF NOT EXISTS ads (
    ts TEXT, side TEXT, adv_no TEXT, price REAL, qty_usdt REAL,
    min_bob REAL, max_bob REAL, pay_methods TEXT, merchant TEXT,
    user_no TEXT, month_orders INTEGER, finish_rate REAL, user_type TEXT
);
CREATE INDEX IF NOT EXISTS ads_ts ON ads(ts);
CREATE TABLE IF NOT EXISTS rates (
    ts TEXT PRIMARY KEY, q_usdt REAL, best_ask REAL, best_bid REAL,
    ask_vwap REAL, bid_vwap REAL, mid REAL, spread_pct REAL,
    depth_ask_usdt REAL, depth_bid_usdt REAL, n_ask INTEGER, n_bid INTEGER
);
"""


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def save(con: sqlite3.Connection, ts: str, ads: list[Ad], rate: dict) -> None:
    con.executemany(
        "INSERT INTO ads VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(ts, a.side, a.adv_no, a.price, a.qty_usdt, a.min_bob, a.max_bob, a.pay_methods,
          a.merchant, a.user_no, a.month_orders, a.finish_rate, a.user_type) for a in ads],
    )
    con.execute(
        "INSERT OR REPLACE INTO rates VALUES (:ts,:q_usdt,:best_ask,:best_bid,:ask_vwap,:bid_vwap,"
        ":mid,:spread_pct,:depth_ask_usdt,:depth_bid_usdt,:n_ask,:n_bid)",
        {"ts": ts, **rate},
    )
    con.commit()


# --------------------------------------------------------------------------- main loop
def snapshot(client: httpx.Client, con: sqlite3.Connection, q_usdt: float) -> dict:
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    asks = fetch_side(client, "BUY")
    time.sleep(PAGE_PAUSE_S)
    bids = fetch_side(client, "SELL")
    rate = reference_rate(asks, bids, q_usdt)
    save(con, ts, asks + bids, rate)
    return {"ts": ts, **rate}


def main() -> None:
    p = argparse.ArgumentParser(description="Binance P2P BOB/USDT collector")
    p.add_argument("--db", default="data/p2p_bob_usdt.sqlite")
    p.add_argument("--interval", type=int, default=60, help="seconds between snapshots")
    p.add_argument("--q", type=float, default=1000.0, help="reference size in USDT")
    p.add_argument("--once", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    con = open_db(Path(args.db))
    backoff = args.interval
    with httpx.Client() as client:
        while True:
            start = time.time()
            try:
                r = snapshot(client, con, args.q)
                log.info("mid=%s spread=%s%% asks=%d bids=%d",
                         f"{r['mid']:.4f}" if r["mid"] else "NA",
                         f"{r['spread_pct']:.3f}" if r["spread_pct"] else "NA",
                         r["n_ask"], r["n_bid"])
                backoff = args.interval
            except (httpx.HTTPError, json.JSONDecodeError) as e:
                backoff = min(backoff * 2, 900)       # back off up to 15 min on errors
                log.error("fetch failed (%s); retrying in %ds", e, backoff)
            if args.once:
                break
            time.sleep(max(0, backoff - (time.time() - start)))


if __name__ == "__main__":
    main()
