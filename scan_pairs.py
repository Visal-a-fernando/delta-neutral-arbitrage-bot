"""
scan_pairs.py -- Multi-exchange pair scanner.

Tests candidate symbols across all exchange combinations using 90 days
of 1h OHLCV data. Ranked output helps decide which pairs to add to config.

Usage:
    python scan_pairs.py              # test all candidates
    python scan_pairs.py --days 30    # shorter window
"""

import argparse
import sys
import time
from datetime import datetime, timezone, timedelta

import ccxt
import yaml

CONFIG_PATH = "config/config.yaml"
W = 72

CANDIDATES = [
    # Proven (already trading or recent)
    "WIF/USDT:USDT",
    "PORTAL/USDT:USDT",
    # Meme / high-vol (profile like WIF)
    "1000PEPE/USDT:USDT",
    "1000BONK/USDT:USDT",
    "1000SHIB/USDT:USDT",
    "1000FLOKI/USDT:USDT",
    "DOGE/USDT:USDT",
    "POPCAT/USDT:USDT",
    "MEW/USDT:USDT",
    # Mid-cap volatile L1/L2
    "SUI/USDT:USDT",
    "SEI/USDT:USDT",
    "TIA/USDT:USDT",
    "APT/USDT:USDT",
    "NEAR/USDT:USDT",
    "ARB/USDT:USDT",
    "OP/USDT:USDT",
    # DeFi / hot narratives
    "JUP/USDT:USDT",
    "JTO/USDT:USDT",
    "PYTH/USDT:USDT",
    "ENA/USDT:USDT",
    "WLD/USDT:USDT",
    "ORDI/USDT:USDT",
    "ONDO/USDT:USDT",
    # Liquid majors (control group)
    "XRP/USDT:USDT",
    "LINK/USDT:USDT",
    "AVAX/USDT:USDT",
    "DOT/USDT:USDT",
    "ADA/USDT:USDT",
    "LTC/USDT:USDT",
]

EXCHANGE_PAIRS = [
    ("binanceusdm", "bybit",   "Binance<>Bybit "),
]

FEE_RT = 0.0006   # 0.06% round trip (maker both sides)


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def make_exchange(exchange_id):
    cls = getattr(ccxt, exchange_id)
    # Gate.io, Bitget, OKX perpetuals are "swap"; Binance/Bybit use "future"
    swap_exchanges = ("okx", "myokx", "gate", "bitget")
    default_type = "swap" if exchange_id in swap_exchanges else "future"
    ex = cls({"enableRateLimit": True, "options": {"defaultType": default_type}})
    try:
        ex.load_markets()
    except Exception:
        pass
    return ex


def fetch_ohlcv(exchange, symbol, since_ms, limit=1000):
    all_c = []
    cur = since_ms
    while True:
        try:
            batch = exchange.fetch_ohlcv(symbol, "1h", since=cur, limit=limit)
        except ccxt.BadSymbol:
            return None
        except Exception:
            break
        if not batch:
            break
        all_c.extend(batch)
        if len(batch) < limit:
            break
        cur = batch[-1][0] + 1
        time.sleep(exchange.rateLimit / 1000)
    return all_c or None


def align(c_a, c_b):
    ma = {c[0]: c[4] for c in c_a}
    mb = {c[0]: c[4] for c in c_b}
    ts = sorted(set(ma) & set(mb))
    return [(t, ma[t], mb[t]) for t in ts]


def simulate(aligned, min_entry, min_exit, margin, leverage, deploy):
    trades = []
    pos = None
    for ts, pa, pb in aligned:
        mid = (pa + pb) / 2
        if mid == 0:
            continue
        spread = abs(pa - pb) / mid
        if pos is None:
            if spread >= min_entry:
                pos = {"ts": ts, "spread": spread,
                       "notional": margin * leverage * deploy}
        else:
            hold_h = (ts - pos["ts"]) / 3_600_000
            converged = spread <= min_exit
            inverted = False  # simplified for scan
            max_hold = hold_h >= 24
            if converged or inverted or max_hold:
                gross = (pos["spread"] - spread) * pos["notional"]
                fees  = FEE_RT * pos["notional"]
                net   = gross - fees
                trades.append({
                    "net": net,
                    "hold_h": hold_h,
                    "exit": "conv" if converged else "max"
                })
                pos = None
    return trades


def score(trades, days):
    if not trades:
        return None
    nets = [t["net"] for t in trades]
    wins = sum(1 for n in nets if n > 0)
    total_net = sum(nets)
    monthly = total_net / days * 30
    win_rate = wins / len(trades) * 100
    avg_hold = sum(t["hold_h"] for t in trades) / len(trades)
    max_hold_pct = sum(1 for t in trades if t["exit"] == "max") / len(trades) * 100
    return {
        "trades":       len(trades),
        "trades_pd":    len(trades) / days,
        "monthly":      monthly,
        "win_rate":     win_rate,
        "avg_hold_h":   avg_hold,
        "max_hold_pct": max_hold_pct,
        "total_net":    total_net,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()

    cfg = load_config()
    s_cfg = cfg["strategy"]
    ps_cfg = cfg["position_sizing"]
    min_entry = s_cfg["min_spread_entry"]
    min_exit  = s_cfg["min_spread_exit"]

    cap = cfg["capital"]
    avail = min(
        cap["total_usdt"] * cap["binance_allocation"] - cap["stablecoin_reserve"],
        cap["total_usdt"] * cap["bybit_allocation"]
    ) * (1 - cap["free_margin_buffer"])
    margin = avail / s_cfg["max_simultaneous_positions"]
    leverage = cfg["leverage"]["default"]
    deploy = ps_cfg["medium_spread_deploy"]

    since_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp() * 1000
    )

    print()
    print("=" * W)
    print(f"  PAIR SCANNER  --  {args.days} days | 1h candles | threshold {min_entry*100:.3f}%")
    print("=" * W)
    print()

    # Init exchanges — collect all unique exchange IDs from both legs
    all_ex_ids = set()
    for ex_a, ex_b, _ in EXCHANGE_PAIRS:
        all_ex_ids.add(ex_a)
        all_ex_ids.add(ex_b)

    exchanges = {}
    for ex_id in sorted(all_ex_ids):
        print(f"  Connecting {ex_id}...", end=" ", flush=True)
        try:
            exchanges[ex_id] = make_exchange(ex_id)
            print("OK")
        except Exception as e:
            print(f"FAILED ({e})")
            exchanges[ex_id] = None
    print()

    # Cache fetched data: {(exchange_id, symbol): candles}
    cache = {}

    def get_candles(ex_id, sym):
        key = (ex_id, sym)
        if key in cache:
            return cache[key]
        ex = exchanges.get(ex_id)
        if ex is None:
            cache[key] = None
            return None
        c = fetch_ohlcv(ex, sym, since_ms)
        cache[key] = c
        return c

    results = []  # (symbol, ex_pair_label, score_dict)

    for symbol in CANDIDATES:
        print(f"  [{symbol:<22}]")
        for ex_a_id, ex_b_id, label in EXCHANGE_PAIRS:
            print(f"    {label} ... ", end="", flush=True)
            ca = get_candles(ex_a_id, symbol)
            cb = get_candles(ex_b_id, symbol)

            if ca is None or cb is None:
                print("no data")
                continue

            aligned = align(ca, cb)
            if len(aligned) < 24:
                print("insufficient overlap")
                continue

            actual_days = (aligned[-1][0] - aligned[0][0]) / 86_400_000
            trades = simulate(aligned, min_entry, min_exit, margin, leverage, deploy)
            s = score(trades, actual_days)

            if s is None:
                print("0 trades")
                continue

            results.append((symbol, label, s))
            sign = "+" if s["monthly"] >= 0 else ""
            print(
                f"{s['trades']:>3} trades | "
                f"{sign}${s['monthly']:.0f}/mo | "
                f"WR {s['win_rate']:.0f}% | "
                f"hold {s['avg_hold_h']:.1f}h"
            )
        print()

    # Ranked summary
    results.sort(key=lambda x: -x[2]["monthly"])
    good = [r for r in results if r[2]["monthly"] > 0 and r[2]["win_rate"] > 40
            and r[2]["max_hold_pct"] < 60]

    print("=" * W)
    print(f"  RANKED RESULTS (positive monthly, WR>40%, <60% max_hold)")
    print("=" * W)
    print(f"  {'Symbol':<24} {'Exchange Pair':<16} {'$/mo':>7} "
          f"{'WR%':>6} {'t/day':>6} {'hold_h':>7}")
    print("-" * W)
    if good:
        for sym, label, s in good:
            print(f"  {sym:<24} {label:<16} "
                  f"${s['monthly']:>6.0f} "
                  f"{s['win_rate']:>5.0f}% "
                  f"{s['trades_pd']:>6.1f} "
                  f"{s['avg_hold_h']:>6.1f}h")
    else:
        print("  No pairs passed all filters at current thresholds.")
        print("  Consider lowering min_spread_entry in config.yaml.")
    print("=" * W)
    print()

    if good:
        print("  Suggested additions to config.yaml (exchange pairs above):")
        seen = set()
        for sym, label, s in good[:8]:
            if sym not in seen:
                seen.add(sym)
                ex_a = "binance" if "Binance" in label else "bybit"
                ex_b = "okx" if "OKX" in label else "bybit"
                print(f"    {sym}  ({ex_a} <-> {ex_b})  +${s['monthly']:.0f}/mo")
        print()


if __name__ == "__main__":
    main()
