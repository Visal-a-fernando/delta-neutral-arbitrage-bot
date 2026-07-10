"""profit_calculator.py — live edge calculator.

Reads bot.db and reports:
  • today's net P&L (regular + orphan + startup-orphan trades)
  • per-pair breakdown (incl. orphan losses isolated)
  • last-24h, last-7d, all-time
  • daily/monthly extrapolation
  • execution-cost estimate so you can see if your edge > friction

Usage:
    python profit_calculator.py
    python profit_calculator.py --days 7
    python profit_calculator.py --start-balance 1146   # show vs starting capital

No exchange calls — purely DB-driven.
For live wallet truth use check_positions.py.
"""

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "bot.db"
W = 72


def open_db():
    if not DB_PATH.exists():
        # Fall back to other common locations
        for p in [Path("data/bot.db"), Path("bot.db"), Path("../data/bot.db")]:
            if p.exists():
                return sqlite3.connect(p)
        print(f"❌ bot.db not found at {DB_PATH}")
        raise SystemExit(1)
    return sqlite3.connect(DB_PATH)


def fmt_money(v):
    sign = "+" if v >= 0 else "−"
    return f"{sign}${abs(v):.2f}"


def section(title):
    print()
    print("─" * W)
    print(f"  {title}")
    print("─" * W)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1,
                    help="Reporting window in days (default 1 = today)")
    ap.add_argument("--start-balance", type=float, default=None,
                    help="Original starting capital — shows projected ROI")
    args = ap.parse_args()

    con = open_db()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # Window cutoff
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    cutoff_iso = cutoff.isoformat()

    rows = cur.execute("""
        SELECT pair, notional_usd, spread_pct, gross_profit, fees, net_profit,
               status, notes, timestamp
        FROM trades
        WHERE status='closed' AND timestamp >= ?
        ORDER BY timestamp
    """, (cutoff_iso,)).fetchall()

    if not rows:
        print(f"No closed trades in last {args.days} day(s).")
        return

    # Classify
    regular   = [r for r in rows if not (r["notes"] or "").startswith("orphan")
                 and not (r["notes"] or "").startswith("startup_orphan")
                 and not (r["notes"] or "").startswith("slippage_kill")]
    orphan    = [r for r in rows if (r["notes"] or "").startswith("orphan_close")]
    startup   = [r for r in rows if (r["notes"] or "").startswith("startup_orphan")]
    slipkill  = [r for r in rows if (r["notes"] or "").startswith("slippage_kill")]

    def stats(group):
        n = len(group)
        if n == 0:
            return {"n": 0, "net": 0, "fees": 0, "wins": 0, "wr": 0, "avg": 0,
                    "best": 0, "worst": 0, "notional": 0}
        nets = [r["net_profit"] or 0 for r in group]
        return {
            "n": n,
            "net":      sum(nets),
            "fees":     sum(r["fees"] or 0 for r in group),
            "wins":     sum(1 for x in nets if x > 0),
            "wr":       sum(1 for x in nets if x > 0) / n * 100,
            "avg":      sum(nets) / n,
            "best":     max(nets),
            "worst":    min(nets),
            "notional": sum(r["notional_usd"] or 0 for r in group),
        }

    s_reg  = stats(regular)
    s_orph = stats(orphan)
    s_strt = stats(startup)
    s_slip = stats(slipkill)
    total_net = s_reg["net"] + s_orph["net"] + s_strt["net"] + s_slip["net"]

    # ── HEADER ────────────────────────────────────────────────────────────────
    print()
    print("═" * W)
    print(f"  LIVE PROFIT CALCULATOR  —  last {args.days} day(s)")
    print("═" * W)
    print(f"  Window: {cutoff.strftime('%Y-%m-%d %H:%M UTC')} → now")

    # ── REGULAR TRADES ────────────────────────────────────────────────────────
    section("REGULAR TRADES (strategy entries)")
    print(f"  Count:        {s_reg['n']}")
    print(f"  Win rate:     {s_reg['wr']:.0f}% ({s_reg['wins']}W / {s_reg['n']-s_reg['wins']}L)")
    print(f"  Net P&L:      {fmt_money(s_reg['net'])}")
    print(f"  Avg / trade:  {fmt_money(s_reg['avg'])}")
    print(f"  Best:         {fmt_money(s_reg['best'])}")
    print(f"  Worst:        {fmt_money(s_reg['worst'])}")
    print(f"  Fees paid:    ${s_reg['fees']:.2f}")
    print(f"  Notional:     ${s_reg['notional']:,.0f}")

    # ── ORPHAN COSTS (the invisible bleed) ────────────────────────────────────
    section("ORPHAN CLOSURES (cancel-fill races, imbalanced fills)")
    if s_orph["n"] == 0:
        print("  ✅ No orphans recorded in window. Patches working.")
    else:
        print(f"  Count:        {s_orph['n']}")
        print(f"  Net P&L:      {fmt_money(s_orph['net'])}    ⚠️ should approach $0")
        print(f"  Avg / orphan: {fmt_money(s_orph['avg'])}")
        print(f"  Worst:        {fmt_money(s_orph['worst'])}")

    # ── STARTUP ORPHANS ───────────────────────────────────────────────────────
    if s_strt["n"] > 0:
        section("STARTUP ORPHANS (positions left from previous sessions)")
        print(f"  Count:        {s_strt['n']}")
        print(f"  Net P&L:      {fmt_money(s_strt['net'])}")
        print(f"  ⚠ These are leftovers from mid-trade shutdowns. "
              f"With patch (g) deployed, count should = 0 going forward.")

    # ── SLIPPAGE KILLS ────────────────────────────────────────────────────────
    if s_slip["n"] > 0:
        section("SLIPPAGE KILLS (latency ate edge before fill)")
        print(f"  Count:        {s_slip['n']}")
        print(f"  Friction cost:{fmt_money(s_slip['net'])}")
        print(f"  Avg / kill:   {fmt_money(s_slip['avg'])}")
        print(f"  ⚠ Each kill = round-trip friction with no held P&L. "
              f"If count high, raise min_spread_entry or check pair-specific "
              f"latency.")

    # ── PER-PAIR BREAKDOWN ────────────────────────────────────────────────────
    section("PER-PAIR (regular trades only)")
    by_pair: dict[str, list[float]] = {}
    for r in regular:
        by_pair.setdefault(r["pair"], []).append(r["net_profit"] or 0)
    pair_rows = []
    for pair, nets in by_pair.items():
        n = len(nets)
        net = sum(nets)
        wins = sum(1 for x in nets if x > 0)
        pair_rows.append((pair, n, net, wins / n * 100 if n else 0, net / n if n else 0))
    pair_rows.sort(key=lambda x: -x[2])
    print(f"  {'Pair':<32} {'N':>4} {'Net':>10} {'WR':>5} {'Avg':>8}")
    for p, n, net, wr, avg in pair_rows[:20]:
        sym = p.replace("/USDT:USDT", "").replace("_", "/")
        print(f"  {sym[:32]:<32} {n:>4} {fmt_money(net):>10} {wr:>4.0f}% {fmt_money(avg):>8}")

    # ── TOTALS ────────────────────────────────────────────────────────────────
    section("TOTALS")
    print(f"  Regular net:    {fmt_money(s_reg['net'])}")
    if s_orph["n"]:
        print(f"  Orphan cost:    {fmt_money(s_orph['net'])}")
    if s_strt["n"]:
        print(f"  Startup cost:   {fmt_money(s_strt['net'])}")
    if s_slip["n"]:
        print(f"  Slippage-kill:  {fmt_money(s_slip['net'])}  ({s_slip['n']} kills)")
    print(f"  ─────────────────────────")
    print(f"  TOTAL:          {fmt_money(total_net)}")

    # ── EDGE PROJECTION ───────────────────────────────────────────────────────
    section("EDGE PROJECTION")
    daily   = total_net / args.days
    monthly = daily * 30
    annual  = daily * 365
    print(f"  Daily avg:    {fmt_money(daily)}")
    print(f"  Monthly:      {fmt_money(monthly)}")
    print(f"  Annual:       {fmt_money(annual)}")
    if args.start_balance:
        roi_d = daily   / args.start_balance * 100
        roi_m = monthly / args.start_balance * 100
        roi_a = annual  / args.start_balance * 100
        print(f"  ROI / day:    {roi_d:+.3f}%")
        print(f"  ROI / month:  {roi_m:+.2f}%")
        print(f"  ROI / year:   {roi_a:+.1f}%")

    # ── VERDICT ───────────────────────────────────────────────────────────────
    section("VERDICT")
    if total_net > 0 and s_orph["n"] == 0:
        print("  ✅ PROFITABLE in window, no orphan bleed. Patches working as intended.")
    elif total_net > 0 and s_orph["net"] >= -2:
        print("  ✅ PROFITABLE in window. Orphan cost negligible.")
    elif total_net > 0:
        print("  ⚠️  Profitable overall but orphans are a real cost. Watch the pattern.")
    elif total_net <= 0 and s_orph["net"] < -10:
        print("  ❌ LOSING and orphans are biting.")
        print(f"     Without orphans you'd be at {fmt_money(s_reg['net'] + s_strt['net'])}.")
        print( "     Consider entry_order_type: market in config.yaml.")
    else:
        print("  ❌ LOSING. Strategy may not have edge in current regime.")
        print( "     Compare next 24h. If still negative, consider scaling down or pivoting.")

    print("═" * W)
    print()


if __name__ == "__main__":
    main()
