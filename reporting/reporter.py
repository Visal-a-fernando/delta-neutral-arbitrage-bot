"""reporting/reporter.py — Full performance reporting for the delta-neutral bot."""

import logging
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Optional

import yaml

from utils.database import (
    _conn,
    get_high_water_mark,
    get_daily_net_profit,
    get_monthly_net_profit,
    get_weekly_net_profit,
    get_reports,
    save_daily_report,
)

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"
W = 72   # report width


# ── Helpers ───────────────────────────────────────────────────────────────────

def _divider(char: str = "─", label: str = "") -> str:
    if label:
        pad = W - len(label) - 4
        return f"  ── {label} {'─' * pad}"
    return "  " + char * (W - 2)


def _section(title: str) -> str:
    return _divider("─", title)


# ── Public API ────────────────────────────────────────────────────────────────

def compound_capital(cfg: dict, paper_mode: bool = False):
    """
    Add this month's reinvest allocation (40%) to total_usdt in config.yaml.
    Called on the last day of each month by bot_engine.
    """
    monthly_net = get_monthly_net_profit()
    if monthly_net <= 0:
        logger.info("Compound capital: no profit this month — skipping")
        return

    reinvest_pct = cfg["profit_extraction"]["monthly_reinvest_pct"]
    reinvest_amt = monthly_net * reinvest_pct
    old_capital  = cfg["capital"]["total_usdt"]
    new_capital  = round(old_capital + reinvest_amt, 2)

    cfg["capital"]["total_usdt"] = new_capital

    try:
        with open(_CONFIG_PATH) as f:
            raw = yaml.safe_load(f)
        raw["capital"]["total_usdt"] = new_capital
        with open(_CONFIG_PATH, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        logger.info(
            f"COMPOUND: total_usdt {old_capital} -> {new_capital} "
            f"(+${reinvest_amt:.2f} = {reinvest_pct*100:.0f}% of ${monthly_net:.2f} monthly profit)"
        )
    except Exception as e:
        logger.error(f"Could not persist compounded capital to config: {e}")


def save_todays_report(exchanges: dict, paper_mode: bool = False):
    """Compute today's stats and persist to daily_reports table."""
    today = datetime.now(timezone.utc).date().isoformat()

    with _conn() as con:
        row = con.execute("""
            SELECT
                COUNT(*)                        AS trades_count,
                COALESCE(SUM(gross_profit), 0)  AS gross_profit,
                COALESCE(SUM(fees), 0)          AS fees,
                COALESCE(SUM(net_profit), 0)    AS net_profit,
                COALESCE(SUM(funding_income), 0) AS funding_income,
                AVG(spread_pct)                 AS avg_spread_pct
            FROM trades
            WHERE DATE(timestamp) = ? AND status = 'closed'
        """, (today,)).fetchone()

    total_balance = 0.0
    total_used    = 0.0
    total_total   = 0.0
    for ex in exchanges.values():
        try:
            bal = ex.fetch_balance()
            total_balance += ex.get_total_equity()
            total_used    += float(bal.get("used",  {}).get("USDT", 0) or 0)
            total_total   += float(bal.get("total", {}).get("USDT", 1) or 1)
        except Exception:
            pass

    margin_usage_pct = (total_used / total_total) if total_total > 0 else 0

    report = {
        "report_date":      today,
        "trades_count":     row["trades_count"],
        "gross_profit":     round(row["gross_profit"], 4),
        "fees":             round(row["fees"], 4),
        "net_profit":       round(row["net_profit"], 4),
        "funding_income":   round(row["funding_income"], 4),
        "avg_spread_pct":   round(row["avg_spread_pct"] or 0, 6),
        "account_balance":  round(total_balance, 2),
        "margin_usage_pct": round(margin_usage_pct, 4),
        "paper_mode":       int(paper_mode),
        "created_at":       datetime.now(timezone.utc).isoformat(),
    }
    save_daily_report(report)
    logger.info(f"Daily report saved: {today} | net=${report['net_profit']:.2f}")
    return report


def generate_daily_report(
    print_to_console: bool = True,
    strategy=None,
    risk_manager=None,
    paper_mode: bool = False,
) -> str:
    """
    Generate the full performance report.

    Sections:
      1. Header + account summary
      2. All-time summary
      3. This week / this month
      4. Dynamic daily loss limit status
      5. Daily breakdown (last 30 days from trades table)
      6. Per-pair breakdown (all time)
      7. Adaptive threshold status (if strategy provided)
      8. Open positions
      9. Last 20 closed trades
     10. Recent risk events
    """
    lines = []

    def add(s: str = ""):
        lines.append(s)

    now = datetime.now(timezone.utc)
    hwm = get_high_water_mark()

    # ── Header ────────────────────────────────────────────────────────────────
    add()
    add("=" * W)
    add("  DELTA-NEUTRAL BOT  —  FULL PERFORMANCE REPORT")
    add("=" * W)
    add(f"  Generated  : {now.strftime('%Y-%m-%d %H:%M UTC')}")
    add(f"  Mode       : {'PAPER' if paper_mode else 'LIVE'}")
    add(f"  HWM        : ${hwm:,.2f}")
    add()

    # ── All-time summary ──────────────────────────────────────────────────────
    add(_section("ALL-TIME SUMMARY"))
    with _conn() as con:
        at = con.execute("""
            SELECT
                COUNT(*)                        AS total,
                SUM(CASE WHEN status='closed' THEN 1 END) AS closed,
                SUM(CASE WHEN status='open'   THEN 1 END) AS open_t,
                COALESCE(SUM(CASE WHEN status='closed' THEN net_profit END), 0)   AS net,
                COALESCE(SUM(CASE WHEN status='closed' THEN gross_profit END), 0) AS gross,
                COALESCE(SUM(CASE WHEN status='closed' THEN fees END), 0)         AS fees,
                COALESCE(SUM(CASE WHEN status='closed' THEN funding_income END), 0) AS funding,
                SUM(CASE WHEN status='closed' AND net_profit > 0 THEN 1 ELSE 0 END) AS wins
            FROM trades
        """).fetchone()

    closed     = at["closed"] or 1
    win_rate   = (at["wins"] or 0) / closed * 100
    avg_trade  = (at["net"] or 0) / closed
    fee_pct    = (at["fees"] / at["gross"] * 100) if at["gross"] else 0

    add(f"  Total trades   : {at['total']}   (closed: {at['closed']}  |  open: {at['open_t'] or 0})")
    add(f"  Net P&L        : ${at['net']:,.2f}")
    add(f"  Gross P&L      : ${at['gross']:,.2f}")
    add(f"  Total fees     : ${at['fees']:,.2f}  ({fee_pct:.1f}% of gross)")
    add(f"  Funding income : ${at['funding']:,.4f}")
    add(f"  Win rate       : {win_rate:.1f}%  ({at['wins']} / {closed})")
    add(f"  Avg per trade  : ${avg_trade:.2f}")
    add()

    # ── This week / month ─────────────────────────────────────────────────────
    add(_section("PERIOD SUMMARY"))
    week_net  = get_weekly_net_profit()
    month_net = get_monthly_net_profit()
    today_net = get_daily_net_profit()
    add(f"  Today    : ${today_net:,.2f}")
    add(f"  This week: ${week_net:,.2f}")
    add(f"  This month: ${month_net:,.2f}")
    add()

    # ── Dynamic daily loss limit ───────────────────────────────────────────────
    add(_section("DAILY LOSS LIMIT  (dynamic)"))
    if risk_manager is not None:
        effective_limit = risk_manager.get_effective_daily_loss_limit()
        base    = abs(risk_manager.risk_cfg.get("max_daily_loss", 200))
        p_step  = risk_manager.risk_cfg.get("daily_loss_profit_step", 300)
        s_amt   = risk_manager.risk_cfg.get("daily_loss_step_amount", 50)
        profit  = max(today_net, 0)
        steps   = int(profit / p_step)
        next_at = (steps + 1) * p_step
    else:
        # Read from config if risk_manager not available
        try:
            cfg = yaml.safe_load(open(_CONFIG_PATH))
            base    = abs(cfg["risk"].get("max_daily_loss", 200))
            p_step  = cfg["risk"].get("daily_loss_profit_step", 300)
            s_amt   = cfg["risk"].get("daily_loss_step_amount", 50)
        except Exception:
            base, p_step, s_amt = 200, 300, 50
        profit  = max(today_net, 0)
        steps   = int(profit / p_step)
        next_at = (steps + 1) * p_step
        effective_limit = base + steps * s_amt

    add(f"  Base limit       : ${base:.0f}")
    add(f"  Today's profit   : ${today_net:.2f}  (positive portion used: ${profit:.2f})")
    add(f"  Steps unlocked   : {steps}  × ${s_amt:.0f} = +${steps * s_amt:.0f}")
    add(f"  Effective limit  : -${effective_limit:.0f}")
    if today_net < effective_limit:
        add(f"  Next unlock at   : ${next_at:.0f} daily profit  (+${s_amt:.0f} more room)")
    add()

    # ── Daily breakdown (from trades, last 30 days) ───────────────────────────
    add(_section("DAILY BREAKDOWN  (last 30 days)"))
    add(f"  {'Date':<12} {'Trades':>6} {'Gross':>10} {'Fees':>9} {'Net':>10} {'WR%':>6}")
    add("  " + "─" * 54)

    with _conn() as con:
        daily_rows = con.execute("""
            SELECT
                DATE(timestamp)                  AS d,
                COUNT(*)                         AS t,
                COALESCE(SUM(gross_profit), 0)   AS gross,
                COALESCE(SUM(fees), 0)           AS fees,
                COALESCE(SUM(net_profit), 0)     AS net,
                SUM(CASE WHEN net_profit > 0 THEN 1 ELSE 0 END) AS wins
            FROM trades
            WHERE status = 'closed'
              AND DATE(timestamp) >= DATE('now', '-30 days')
            GROUP BY DATE(timestamp)
            ORDER BY d DESC
        """).fetchall()

    d_net = d_gross = d_fees = d_trades = 0
    for r in daily_rows:
        wr = (r["wins"] / r["t"] * 100) if r["t"] else 0
        sign = "+" if r["net"] >= 0 else ""
        add(f"  {r['d']:<12} {r['t']:>6} ${r['gross']:>9,.2f} ${r['fees']:>8,.2f} "
            f"{sign}${r['net']:>8,.2f} {wr:>5.0f}%")
        d_net    += r["net"]
        d_gross  += r["gross"]
        d_fees   += r["fees"]
        d_trades += r["t"]

    add("  " + "─" * 54)
    sign = "+" if d_net >= 0 else ""
    add(f"  {'TOTAL':<12} {d_trades:>6} ${d_gross:>9,.2f} ${d_fees:>8,.2f} "
        f"{sign}${d_net:>8,.2f}")
    add()

    # ── Per-pair breakdown ─────────────────────────────────────────────────────
    add(_section("PER-PAIR BREAKDOWN  (all time)"))
    add(f"  {'Pair':<42} {'T':>4} {'Net':>9} {'Avg':>7} {'WR%':>5}  Status")
    add("  " + "─" * 70)

    with _conn() as con:
        pair_rows = con.execute("""
            SELECT
                pair,
                COUNT(*)                         AS t,
                COALESCE(SUM(net_profit), 0)     AS net,
                COALESCE(AVG(net_profit), 0)     AS avg,
                SUM(CASE WHEN net_profit > 0 THEN 1 ELSE 0 END) AS wins
            FROM trades
            WHERE status = 'closed'
            GROUP BY pair
            ORDER BY net DESC
        """).fetchall()

        # Active pair names from config
        try:
            cfg_data    = yaml.safe_load(open(_CONFIG_PATH))
            active_set  = {
                f"{p['leg_a_symbol']}_{p['leg_b_symbol']}"
                for p in cfg_data.get("pairs", []) if p.get("active")
            }
            disabled_set = {
                f"{p['leg_a_symbol']}_{p['leg_b_symbol']}"
                for p in cfg_data.get("pairs", []) if not p.get("active")
            }
        except Exception:
            active_set  = set()
            disabled_set = set()

    for r in pair_rows:
        wr     = (r["wins"] / r["t"] * 100) if r["t"] else 0
        sign   = "+" if r["net"] >= 0 else ""
        status = ("DISABLED" if r["pair"] in disabled_set
                  else "Active"  if r["pair"] in active_set
                  else "Unknown")

        # Mark throttled pairs (threshold elevated) if strategy available
        if strategy is not None and r["pair"] in strategy._pair_thresholds:
            floor_t = strategy.s_cfg.get("adaptive_spread", {}).get("floor", 0.0011)
            if strategy._pair_thresholds[r["pair"]] > floor_t * 1.01:
                status += " [throttled]"

        add(f"  {r['pair']:<42} {r['t']:>4} {sign}${r['net']:>8,.2f} "
            f"{sign}${r['avg']:>5,.2f} {wr:>4.0f}%  {status}")
    add()

    # ── Adaptive thresholds ────────────────────────────────────────────────────
    add(_section("ADAPTIVE THRESHOLDS"))
    if strategy is not None:
        floor_t   = strategy.s_cfg.get("adaptive_spread", {}).get("floor", 0.0011)
        ceiling_t = strategy.s_cfg.get("adaptive_spread", {}).get("ceiling", 0.0025)

        add(f"  {'Pair':<42} {'Current':>9} {'Floor':>7} {'State'}")
        add("  " + "─" * 68)

        # All active pairs — show floor if not yet in thresholds dict
        try:
            all_pairs = [
                f"{p['leg_a_symbol']}_{p['leg_b_symbol']}"
                for p in yaml.safe_load(open(_CONFIG_PATH)).get("pairs", [])
                if p.get("active")
            ]
        except Exception:
            all_pairs = list(strategy._pair_thresholds.keys())

        for pair in all_pairs:
            t = strategy._pair_thresholds.get(pair, floor_t)
            if t >= ceiling_t * 0.999:
                state = "THROTTLED (at ceiling)"
            elif t > floor_t * 1.01:
                state = f"Elevated (+{(t - floor_t)*100:.3f}%)"
            else:
                state = "At floor"
            add(f"  {pair:<42} {t*100:>8.3f}%  {floor_t*100:>6.3f}%  {state}")
        add()
    else:
        add("  (strategy object not available — restart bot to see live thresholds)")
        add()

    # ── Open positions ─────────────────────────────────────────────────────────
    add(_section("OPEN POSITIONS"))
    with _conn() as con:
        opens = con.execute(
            "SELECT * FROM trades WHERE status='open' ORDER BY timestamp"
        ).fetchall()

    if not opens:
        add("  None")
    else:
        add(f"  {'ID':>4}  {'Pair':<42} {'Notional':>10} {'Spread':>7} {'Direction':<16} Since")
        add("  " + "─" * 70)
        for o in opens:
            sp   = (o["spread_pct"] or 0) * 100
            drxn = o["direction"] or "buy_a_sell_b"
            since = str(o["timestamp"])[:16]
            add(f"  {o['id']:>4}  {o['pair']:<42} ${o['notional_usd']:>9,.0f} "
                f"{sp:>6.3f}%  {drxn:<16} {since}")
    add()

    # ── Last 20 closed trades ─────────────────────────────────────────────────
    add(_section("LAST 20 CLOSED TRADES"))
    add(f"  {'ID':>4}  {'Pair':<38} {'Net':>8} {'Entry%':>7} {'Reason':<20} Timestamp")
    add("  " + "─" * 72)

    with _conn() as con:
        last20 = con.execute("""
            SELECT id, pair, net_profit, spread_pct, notes, timestamp
            FROM trades WHERE status='closed'
            ORDER BY id DESC LIMIT 20
        """).fetchall()

    for t in last20:
        net  = t["net_profit"] or 0
        sp   = (t["spread_pct"] or 0) * 100
        sign = "+" if net >= 0 else ""
        # Extract reason from notes field
        reason = ""
        if t["notes"]:
            import re
            m = re.search(r"closed:(\S+)", t["notes"])
            if m:
                reason = m.group(1)
        ts = str(t["timestamp"])[:16]
        add(f"  {t['id']:>4}  {t['pair']:<38} {sign}${net:>7,.2f} {sp:>6.3f}%  "
            f"{reason:<20} {ts}")
    add()

    # ── Recent risk events ────────────────────────────────────────────────────
    add(_section("RECENT RISK EVENTS  (last 10)"))
    with _conn() as con:
        risk_events = con.execute("""
            SELECT timestamp, event_type, description, account_balance
            FROM risk_events
            ORDER BY id DESC LIMIT 10
        """).fetchall()

    if not risk_events:
        add("  None")
    else:
        for e in risk_events:
            ts  = str(e["timestamp"])[:16]
            bal = f"  bal=${e['account_balance']:,.2f}" if e["account_balance"] else ""
            add(f"  {ts}  [{e['event_type']}]{bal}")
            if e["description"]:
                add(f"            {e['description'][:65]}")
    add()

    # ── Profit extraction summary ──────────────────────────────────────────────
    add(_section("PROFIT EXTRACTION"))
    with _conn() as con:
        withdrawals = con.execute("""
            SELECT withdrawal_type, SUM(amount_usd) AS total, COUNT(*) AS cnt
            FROM withdrawals
            GROUP BY withdrawal_type
            ORDER BY total DESC
        """).fetchall()

    if not withdrawals:
        add("  No withdrawals recorded yet")
    else:
        add(f"  {'Type':<25} {'Count':>5} {'Total USD':>12}")
        add("  " + "─" * 45)
        for w in withdrawals:
            add(f"  {w['withdrawal_type']:<25} {w['cnt']:>5} ${w['total']:>11,.2f}")
    add()

    # ── Footer ────────────────────────────────────────────────────────────────
    add("=" * W)
    add(f"  End of report  —  {now.strftime('%Y-%m-%d %H:%M UTC')}")
    add("=" * W)
    add()

    output = "\n".join(lines)
    if print_to_console:
        print(output)

    logger.info(
        f"Full report generated: "
        f"{at['closed']} trades | net=${at['net']:.2f} | "
        f"today=${today_net:.2f}"
    )
    return output
