"""
progress.py -- Daily progress tracker for the Delta-Neutral Bot.
Run any time to see how you're tracking toward the $300+/month target.

Usage:
    python progress.py
"""

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path as _Path

DB_PATH = str(_Path(__file__).resolve().parent / "data" / "bot.db")
MONTHLY_TARGET = 600.0
W = 58  # display width


def _conn():
    if not _Path(DB_PATH).exists():
        print("No database found. Start the bot first: python bot.py --paper")
        raise SystemExit(1)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _bar(value, target, width=28):
    pct = min(value / target, 1.0) if target > 0 else 0
    filled = int(pct * width)
    bar = "#" * filled + "." * (width - filled)
    return f"[{bar}] {pct*100:.0f}%"


def _sep(label=""):
    if label:
        side = "-" * 3
        print(f"{side} {label} {'-' * (W - len(label) - 5)}")
    else:
        print("-" * W)


def _utc_to_local_date(ts_str: str) -> str:
    """
    Convert a UTC ISO timestamp string (from DB) to the local machine date string.
    DB stores UTC. Progress.py must use local dates so 'today' matches what the
    operator sees on their clock — not UTC midnight (which is 8:00 AM AWST).
    Example: '2026-04-20T16:30:00' UTC  →  '2026-04-21' in AWST (UTC+8)
    """
    try:
        dt_utc = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        dt_local = dt_utc.astimezone()   # convert to local timezone
        return dt_local.date().isoformat()
    except Exception:
        return ts_str[:10]   # fallback: use raw prefix


def main():
    con = _conn()
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    week_start  = (today - timedelta(days=today.weekday())).isoformat()

    # Trade stats — convert UTC timestamps to local date for grouping
    all_trades = con.execute(
        "SELECT * FROM trades WHERE status='closed' ORDER BY timestamp"
    ).fetchall()

    today_trades = [t for t in all_trades if _utc_to_local_date(t["timestamp"]) == today.isoformat()]
    week_trades  = [t for t in all_trades if _utc_to_local_date(t["timestamp"]) >= week_start]
    month_trades = [t for t in all_trades if _utc_to_local_date(t["timestamp"]) >= month_start]

    today_net  = sum(t["net_profit"] or 0 for t in today_trades)
    week_net   = sum(t["net_profit"] or 0 for t in week_trades)
    month_net  = sum(t["net_profit"] or 0 for t in month_trades)
    total_net  = sum(t["net_profit"] or 0 for t in all_trades)

    avg_spread = (
        sum(t["spread_pct"] or 0 for t in all_trades) / len(all_trades) * 100
        if all_trades else 0
    )
    avg_net_per_trade = total_net / len(all_trades) if all_trades else 0

    # Days running
    first_date      = all_trades[0]["timestamp"][:10] if all_trades else today.isoformat()
    days_running    = max((today - date.fromisoformat(first_date)).days + 1, 1)
    days_this_month = max(today.day, 1)   # calendar days elapsed in current month
    projected_month = (month_net / days_this_month) * 30 if month_net != 0 else 0

    # Per-pair breakdown
    pairs = {}
    for t in all_trades:
        sym = t["leg_a_symbol"]
        if sym not in pairs:
            pairs[sym] = {"count": 0, "net": 0.0, "spreads": []}
        pairs[sym]["count"] += 1
        pairs[sym]["net"]   += t["net_profit"] or 0
        if t["spread_pct"]:
            pairs[sym]["spreads"].append(t["spread_pct"])

    # Withdrawals
    withdrawals = con.execute(
        "SELECT * FROM withdrawals ORDER BY timestamp DESC LIMIT 10"
    ).fetchall()
    total_withdrawn = con.execute(
        "SELECT COALESCE(SUM(amount_usd),0) FROM withdrawals"
    ).fetchone()[0]

    # Best / worst day — group by local date (UTC+8 for AWST)
    # SQLite datetime() shifts UTC timestamp to local equivalent using strftime
    daily = con.execute("""
        SELECT DATE(timestamp, 'localtime') as d, SUM(net_profit) as net
        FROM trades WHERE status='closed'
        GROUP BY DATE(timestamp, 'localtime') ORDER BY net DESC
    """).fetchall()
    best_day  = dict(daily[0])  if daily else None
    worst_day = dict(daily[-1]) if daily else None

    # Open positions
    open_trades = con.execute(
        "SELECT pair, timestamp FROM trades WHERE status='open'"
    ).fetchall()

    # Paper mode flag
    _pm_row  = con.execute(
        "SELECT paper_mode FROM trades ORDER BY id DESC LIMIT 1"
    ).fetchone()
    is_paper = bool(_pm_row[0]) if _pm_row else True

    con.close()

    # Display
    mode_label = "PAPER MODE" if is_paper else "LIVE MODE"
    print()
    print("=" * W)
    print(f"  DELTA-NEUTRAL BOT  --  PROGRESS  [{mode_label}]")
    print("=" * W)
    print(f"  Date        : {today}  (day {days_running} running)")
    print(f"  Target      : ${MONTHLY_TARGET:,.0f} / month")
    print()

    _sep("THIS MONTH")
    print(f"  Net profit  : ${month_net:>8.2f}  {_bar(month_net, MONTHLY_TARGET)}")
    print(f"  Projected   : ${projected_month:>8.2f}/month  "
          f"({'ON TRACK' if projected_month >= MONTHLY_TARGET else 'BEHIND'})")
    print(f"  Trades      : {len(month_trades)}")
    print()

    _sep("TODAY / THIS WEEK")
    daily_target = MONTHLY_TARGET / 30
    print(f"  Today       : ${today_net:>8.2f}  {_bar(today_net, daily_target)}"
          f"  (need ${daily_target:.2f}/day)")
    print(f"  This week   : ${week_net:>8.2f}  ({len(week_trades)} trades)")
    print()

    _sep("ALL TIME")
    print(f"  Total net   : ${total_net:>8.2f}")
    print(f"  Total trades: {len(all_trades)}")
    print(f"  Avg spread  : {avg_spread:.4f}%")
    print(f"  Avg net/trd : ${avg_net_per_trade:.3f}")
    if best_day:
        print(f"  Best day    : {best_day['d']}  ${best_day['net']:.2f}")
    if worst_day and len(daily) > 1:
        print(f"  Worst day   : {worst_day['d']}  ${worst_day['net']:.2f}")
    print()

    if pairs:
        _sep("BY PAIR")
        for sym, s in sorted(pairs.items(), key=lambda x: -x[1]["net"]):
            avg_sp = (sum(s["spreads"]) / len(s["spreads"]) * 100) if s["spreads"] else 0
            print(f"  {sym:<22} trades={s['count']:>3}  "
                  f"net=${s['net']:>7.2f}  avg_spread={avg_sp:.4f}%")
        print()

    if open_trades:
        _sep("OPEN POSITIONS")
        for t in open_trades:
            print(f"  {t['pair']:<38} since {t['timestamp'][:16]}")
        print()

    _sep("WITHDRAWALS")
    if withdrawals:
        for w in withdrawals:
            print(f"  {w['timestamp'][:10]}  {w['withdrawal_type']:<18} ${w['amount_usd']:.2f}")
        print(f"  Total secured : ${total_withdrawn:.2f}")
    else:
        print("  None yet -- first withdrawal fires Friday 18:00 AWST")
    print()

    _sep("SUMMARY")
    gap = MONTHLY_TARGET - projected_month
    if not all_trades:
        print("  No closed trades yet.")
        print("  Open position will appear above once BNB spread closes.")
    elif projected_month >= MONTHLY_TARGET:
        print(f"  ON TRACK -- projected ${projected_month:.0f}/month vs ${MONTHLY_TARGET:.0f} target")
    else:
        days_left = max(30 - days_running, 1)
        extra = (gap / avg_net_per_trade / days_left) if avg_net_per_trade > 0 else 0
        print(f"  BEHIND by ${gap:.0f}/month -- need ~{extra:.1f} more trades/day")
    print("=" * W)
    print()


if __name__ == "__main__":
    main()
