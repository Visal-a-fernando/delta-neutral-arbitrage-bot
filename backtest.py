"""
backtest.py -- Historical spread-capture backtest using CCXT OHLCV data.

Fetches historical candles from both Binance Futures and Bybit, aligns
timestamps, then replays the same entry/exit logic as the live bot.

NOTE ON METHODOLOGY:
  Bybit's REST API caps at ~1,999 candles regardless of the requested window.
  At 5m resolution that covers ~7 days. Use --tf 1h for up to ~200 days.
  Close-price comparison understates intra-candle convergence — paper mode
  (real prices every 20s) remains the most accurate forward test.

Usage:
    python backtest.py                        # 90-day, all pairs, 1h candles
    python backtest.py --days 30 --tf 15m     # 30-day, 15m candles
    python backtest.py --pair DOGE/USDT       # single pair
    python backtest.py --csv                  # export trades to data/backtest_trades.csv
"""

import argparse
import csv
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import ccxt
import yaml

# ── Constants ─────────────────────────────────────────────────────────────────
CONFIG_PATH     = "config/config.yaml"
MONTHLY_TARGET  = 300.0
MAX_HOLD_MIN    = 1440         # force-close any position open longer than 24h
W               = 64           # display width

# Fee model (maker both sides — limit orders)
FEE_BINANCE_MAKER = 0.0002     # 0.02%
FEE_BYBIT_MAKER   = 0.0001     # 0.01%
FEE_ROUND_TRIP    = (FEE_BINANCE_MAKER + FEE_BYBIT_MAKER) * 2  # 0.06%


# ── Config ────────────────────────────────────────────────────────────────────
def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── Exchange (public — no API keys needed) ────────────────────────────────────
def make_exchange(exchange_id):
    cls = getattr(ccxt, exchange_id)
    default_type = "swap" if exchange_id in ("okx", "myokx") else "future"
    ex = cls({"enableRateLimit": True, "options": {"defaultType": default_type}})
    ex.load_markets()
    return ex


# ── OHLCV fetching with pagination ────────────────────────────────────────────
def fetch_ohlcv_full(exchange, symbol, timeframe, since_ms, show_progress=True):
    """Fetch all candles from since_ms to now, paginating automatically."""
    all_candles = []
    limit = 1000
    current_since = since_ms

    while True:
        try:
            batch = exchange.fetch_ohlcv(symbol, timeframe, since=current_since, limit=limit)
        except ccxt.BadSymbol:
            return None
        except Exception as e:
            print(f"    fetch error: {e}")
            break

        if not batch:
            break

        all_candles.extend(batch)
        last_ts = batch[-1][0]

        if show_progress:
            dt = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            print(f"    {len(all_candles):>6,} candles ... {dt}", end="\r")

        # Stop if the last batch didn't fill the limit (we've reached present)
        if len(batch) < limit:
            break

        current_since = last_ts + 1
        time.sleep(exchange.rateLimit / 1000)

    if show_progress:
        print()  # newline after \r progress

    return all_candles


# ── Align two OHLCV series by timestamp ──────────────────────────────────────
def align_series(candles_a, candles_b):
    """Return list of (ts_ms, close_a, close_b) for common timestamps."""
    map_a = {c[0]: c[4] for c in candles_a}
    map_b = {c[0]: c[4] for c in candles_b}
    common = sorted(set(map_a) & set(map_b))
    return [(ts, map_a[ts], map_b[ts]) for ts in common]


# ── Capital sizing ────────────────────────────────────────────────────────────
def calc_margin_per_pair(cfg):
    cap      = cfg["capital"]
    binance_avail = cap["total_usdt"] * cap["binance_allocation"] - cap["stablecoin_reserve"]
    bybit_avail   = cap["total_usdt"] * cap["bybit_allocation"]
    deployable    = min(binance_avail, bybit_avail) * (1 - cap["free_margin_buffer"])
    max_pos       = cfg["strategy"]["max_simultaneous_positions"]
    return deployable / max_pos


# ── Per-pair simulation ───────────────────────────────────────────────────────
def simulate_pair(symbol, aligned, cfg, margin_per_pair):
    """
    Replay spread strategy on aligned price series.
    Returns list of trade dicts.
    """
    s_cfg  = cfg["strategy"]
    ps_cfg = cfg["position_sizing"]
    lev    = cfg["leverage"]

    min_entry = s_cfg["min_spread_entry"]   # 0.08%
    min_exit  = s_cfg["min_spread_exit"]    # 0.03%

    trades   = []
    position = None

    for ts, price_a, price_b in aligned:
        mid = (price_a + price_b) / 2.0
        if mid == 0:
            continue

        spread = (price_a - price_b) / mid   # signed; >0 = Binance higher
        abs_sp = abs(spread)

        # ── Exit logic ──────────────────────────────────────────────────────
        if position is not None:
            hold_min = (ts - position["entry_ts"]) / 60_000

            direction  = position["direction"]   # +1 or -1
            gross      = direction * (position["entry_spread"] - spread) * position["notional"]
            fees       = FEE_ROUND_TRIP * position["notional"]
            net        = gross - fees

            spread_converged = abs_sp <= min_exit
            spread_inverted  = (spread * position["entry_spread"]) < 0
            max_hold_hit     = hold_min >= MAX_HOLD_MIN

            if spread_converged or spread_inverted or max_hold_hit:
                trades.append({
                    "symbol":       symbol,
                    "entry_ts":     datetime.fromtimestamp(position["entry_ts"] / 1000, tz=timezone.utc),
                    "exit_ts":      datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                    "entry_spread": position["entry_spread"] * 100,   # in %
                    "exit_spread":  spread * 100,
                    "notional":     round(position["notional"], 2),
                    "gross":        round(gross, 4),
                    "fees":         round(fees, 4),
                    "net":          round(net, 4),
                    "duration_min": round(hold_min, 1),
                    "exit_reason":  (
                        "converged"  if spread_converged else
                        "inverted"   if spread_inverted  else
                        "max_hold"
                    ),
                })
                position = None

        # ── Entry logic ─────────────────────────────────────────────────────
        if position is None and abs_sp >= min_entry:
            # Dynamic sizing
            if abs_sp >= ps_cfg["high_spread_threshold"]:
                deploy = ps_cfg["high_spread_deploy"]
                effective_lev = lev["maximum"]
            elif abs_sp >= ps_cfg["medium_spread_min"]:
                deploy = ps_cfg["medium_spread_deploy"]
                effective_lev = lev["default"]
            else:
                continue

            notional = margin_per_pair * effective_lev * deploy

            position = {
                "entry_ts":     ts,
                "entry_spread": spread,
                "direction":    1 if spread > 0 else -1,
                "notional":     notional,
            }

    # Force-close any still-open position at last available price
    if position and aligned:
        last_ts, last_a, last_b = aligned[-1]
        last_mid = (last_a + last_b) / 2.0
        last_spread = (last_a - last_b) / last_mid if last_mid else 0
        direction  = position["direction"]
        gross      = direction * (position["entry_spread"] - last_spread) * position["notional"]
        fees       = FEE_ROUND_TRIP * position["notional"]
        hold_min   = (last_ts - position["entry_ts"]) / 60_000
        trades.append({
            "symbol":       symbol,
            "entry_ts":     datetime.fromtimestamp(position["entry_ts"] / 1000, tz=timezone.utc),
            "exit_ts":      datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc),
            "entry_spread": position["entry_spread"] * 100,
            "exit_spread":  last_spread * 100,
            "notional":     round(position["notional"], 2),
            "gross":        round(gross, 4),
            "fees":         round(fees, 4),
            "net":          round(gross - fees, 4),
            "duration_min": round(hold_min, 1),
            "exit_reason":  "eod_force_close",
        })

    return trades


# ── Report ────────────────────────────────────────────────────────────────────
def _bar(value, target, width=26):
    pct = min(value / target, 1.0) if target > 0 else 0
    filled = int(pct * width)
    return f"[{'#' * filled + '.' * (width - filled)}] {pct*100:.0f}%"


def _sep(label=""):
    side = "---"
    if label:
        print(f"{side} {label} {'-' * (W - len(label) - 5)}")
    else:
        print("-" * W)


def print_report(all_trades, days, margin_per_pair, cfg):
    if not all_trades:
        print()
        print("No trades generated.")
        print(f"  Entry threshold : {cfg['strategy']['min_spread_entry']*100:.3f}%")
        print("  Spreads between the two exchanges never exceeded this threshold")
        print("  in the historical data. Try --days 90 or a different --pair.")
        return

    nets        = [t["net"] for t in all_trades]
    total_net   = sum(nets)
    avg_net     = total_net / len(nets)
    wins        = [n for n in nets if n > 0]
    losses      = [n for n in nets if n <= 0]
    win_pct     = len(wins) / len(nets) * 100
    avg_win     = sum(wins) / len(wins) if wins else 0
    avg_loss    = sum(losses) / len(losses) if losses else 0

    avg_entry_sp  = sum(abs(t["entry_spread"]) for t in all_trades) / len(all_trades)
    avg_duration  = sum(t["duration_min"] for t in all_trades) / len(all_trades)
    trades_pd     = len(all_trades) / days
    proj_monthly  = total_net / days * 30

    # Max drawdown on cumulative equity curve
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for t in sorted(all_trades, key=lambda x: x["exit_ts"]):
        equity += t["net"]
        peak    = max(peak, equity)
        max_dd  = max(max_dd, peak - equity)

    # Per-pair summary
    pair_stats = {}
    for t in all_trades:
        s = t["symbol"]
        if s not in pair_stats:
            pair_stats[s] = {"count": 0, "net": 0.0, "spreads": []}
        pair_stats[s]["count"] += 1
        pair_stats[s]["net"]   += t["net"]
        pair_stats[s]["spreads"].append(abs(t["entry_spread"]))

    # Exit reason breakdown
    reason_counts = {}
    for t in all_trades:
        r = t["exit_reason"]
        reason_counts[r] = reason_counts.get(r, 0) + 1

    # Detect Bybit data cap warning
    max_hold_pct = reason_counts.get("max_hold", 0) / len(all_trades) * 100 if all_trades else 0

    print()
    print("=" * W)
    print(f"  BACKTEST RESULTS  --  {days}-DAY WINDOW")
    print("=" * W)
    print(f"  Entry threshold : {cfg['strategy']['min_spread_entry']*100:.3f}%")
    print(f"  Exit threshold  : {cfg['strategy']['min_spread_exit']*100:.3f}%")
    print(f"  Leverage        : {cfg['leverage']['default']}x default / {cfg['leverage']['maximum']}x max")
    print(f"  Margin/pair     : ${margin_per_pair:.0f}  |  Max notional/pair: "
          f"${margin_per_pair * cfg['leverage']['maximum']:.0f}")
    if max_hold_pct > 50:
        print()
        print(f"  ** WARNING: {max_hold_pct:.0f}% of trades hit max_hold. Likely cause:")
        print(f"     Bybit REST API caps at ~1,999 candles — data window is")
        print(f"     shorter than requested. Re-run with --tf 1h for more history.")
        print(f"     Paper mode (20s live polling) remains more accurate.")
    print()

    _sep("TRADE SUMMARY")
    print(f"  Total trades    : {len(all_trades)}")
    print(f"  Trades / day    : {trades_pd:.1f}")
    print(f"  Win rate        : {win_pct:.1f}%  "
          f"({len(wins)} wins / {len(losses)} losses)")
    print(f"  Avg entry spread: {avg_entry_sp:.4f}%")
    print(f"  Avg hold time   : {avg_duration:.0f} min  "
          f"({avg_duration/60:.1f}h)")
    print()

    _sep("P&L")
    print(f"  Avg net / trade : ${avg_net:.3f}")
    print(f"  Avg win         : ${avg_win:.3f}")
    print(f"  Avg loss        : ${avg_loss:.3f}")
    print(f"  Best trade      : ${max(nets):.2f}")
    print(f"  Worst trade     : ${min(nets):.2f}")
    print(f"  Total net       : ${total_net:.2f}  ({days} days)")
    print(f"  Max drawdown    : ${max_dd:.2f}")
    print()

    _sep("MONTHLY PROJECTION")
    print(f"  Projected/month : ${proj_monthly:>8.0f}  {_bar(proj_monthly, MONTHLY_TARGET)}")
    print(f"  Target          : ${MONTHLY_TARGET:>8.0f}")
    gap = MONTHLY_TARGET - proj_monthly
    if proj_monthly >= MONTHLY_TARGET:
        print(f"  Status          : ON TRACK  (${proj_monthly-MONTHLY_TARGET:.0f} above target)")
    else:
        pct_of_target = proj_monthly / MONTHLY_TARGET * 100
        print(f"  Status          : ${gap:.0f} SHORT of target  ({pct_of_target:.0f}% of goal)")
    print()

    _sep("EXIT REASONS")
    for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
        pct = count / len(all_trades) * 100
        print(f"  {reason:<20} {count:>4}  ({pct:.0f}%)")
    print()

    _sep("BY PAIR")
    for sym, s in sorted(pair_stats.items(), key=lambda x: -x[1]["net"]):
        avg_sp = sum(s["spreads"]) / len(s["spreads"])
        print(f"  {sym:<24} trades={s['count']:>4}  "
              f"net=${s['net']:>7.2f}  avg_spread={avg_sp:.4f}%")
    print()

    _sep("SCENARIOS (what-if at current trade frequency)")
    freq = trades_pd
    for spread_label, spread_val in [("0.08% (current)", 0.0008),
                                      ("0.10%", 0.001),
                                      ("0.15%", 0.0015),
                                      ("0.20%", 0.002)]:
        # notional at default leverage, medium deploy
        notional = margin_per_pair * cfg["leverage"]["default"] * 0.60
        gross    = spread_val * notional
        net_t    = gross - FEE_ROUND_TRIP * notional
        monthly  = net_t * freq * 30
        print(f"  Spread {spread_label:<18} ${net_t:.3f}/trade  "
              f"${monthly:>6.0f}/mo")
    print()

    print("=" * W)
    print()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Delta-neutral backtest")
    ap.add_argument("--days",  type=int,   default=90,   help="Lookback window in days (default 90)")
    ap.add_argument("--tf",    type=str,   default="1h", help="Candle timeframe (default 1h; use 15m for more detail)")
    ap.add_argument("--pair",  type=str,   default=None, help="Filter to one symbol, e.g. DOGE/USDT")
    ap.add_argument("--csv",   action="store_true",      help="Export trades to data/backtest_trades.csv")
    args = ap.parse_args()

    cfg = load_config()
    margin_per_pair = calc_margin_per_pair(cfg)

    since_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp() * 1000
    )

    # Filter pairs
    pairs_cfg = [p for p in cfg["pairs"] if p.get("active", False)]
    if args.pair:
        pairs_cfg = [p for p in pairs_cfg
                     if args.pair.upper() in p["leg_a_symbol"].upper()]
    if not pairs_cfg:
        print(f"No active pairs match '{args.pair}'. Check config/config.yaml.")
        sys.exit(1)

    print()
    print(f"Backtest: {args.days} days | timeframe {args.tf} | "
          f"{len(pairs_cfg)} pair(s) | threshold {cfg['strategy']['min_spread_entry']*100:.3f}%")
    print("Using public endpoints — no API keys required.")
    print()

    # Init exchanges once (load_markets takes a moment)
    print("Connecting to exchanges...")
    try:
        binance = make_exchange("binanceusdm")
        bybit   = make_exchange("bybit")
    except Exception as e:
        print(f"Failed to connect: {e}")
        sys.exit(1)
    print("Connected.\n")

    all_trades = []

    for pair in pairs_cfg:
        sym = pair["leg_a_symbol"]   # e.g. "ETH/USDT:USDT"
        print(f"[{sym}]")
        print(f"  Fetching Binance ({args.tf}, {args.days}d)...")
        candles_a = fetch_ohlcv_full(binance, sym, args.tf, since_ms)

        print(f"  Fetching Bybit   ({args.tf}, {args.days}d)...")
        candles_b = fetch_ohlcv_full(bybit, sym, args.tf, since_ms)

        if not candles_a or not candles_b:
            print(f"  Skipping — no data returned.\n")
            continue

        aligned = align_series(candles_a, candles_b)
        if not aligned:
            print(f"  Skipping — no overlapping timestamps.\n")
            continue

        print(f"  {len(candles_a):,} Binance  |  {len(candles_b):,} Bybit  |  "
              f"{len(aligned):,} aligned")

        trades = simulate_pair(sym, aligned, cfg, margin_per_pair)
        all_trades.extend(trades)

        pair_net = sum(t["net"] for t in trades)
        print(f"  Trades: {len(trades)}  |  Net: ${pair_net:.2f}")
        print()

    all_trades.sort(key=lambda t: t["entry_ts"])
    print_report(all_trades, args.days, margin_per_pair, cfg)

    if args.csv and all_trades:
        out = Path("data/backtest_trades.csv")
        out.parent.mkdir(exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_trades[0].keys())
            writer.writeheader()
            writer.writerows(all_trades)
        print(f"Trades exported to {out}\n")


if __name__ == "__main__":
    main()
