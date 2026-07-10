"""core/bot_engine.py — Main trading loop, scheduler, and orchestration."""

import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from utils.exchange import build_exchange_clients
from utils.ws_feeds import WSPriceCache
from strategies.spread_capture import SpreadCaptureStrategy
from strategies.basis_capture import BasisCaptureStrategy
from risk.risk_manager import RiskManager
from reporting.reporter import generate_daily_report, save_todays_report, compound_capital
from reporting.profit_extractor import ProfitExtractor
from alerts.alert_manager import AlertManager
from alerts.telegram_commands import TelegramCommandHandler

logger = logging.getLogger(__name__)


class BotEngine:
    """Orchestrates all bot components."""

    def __init__(self, cfg: dict, paper_mode: bool = False):
        self.cfg = cfg
        self.paper_mode = paper_mode
        self.running = False

        logger.info("Initialising bot components...")

        # Build exchange clients
        self.exchanges = build_exchange_clients(cfg, paper_mode)

        # WebSocket price cache (optional — falls back to REST if ccxt.pro absent)
        self.ws_cache = WSPriceCache()
        self._register_ws_symbols()
        ws_started = self.ws_cache.start()
        if ws_started:
            for client in self.exchanges.values():
                client.set_ws_cache(self.ws_cache)

        # Core components
        self.alert_manager  = AlertManager(cfg)
        self.risk_manager   = RiskManager(cfg, self.exchanges, self.alert_manager)
        self.strategy       = SpreadCaptureStrategy(cfg, self.exchanges, self.risk_manager,
                                                     self.alert_manager, paper_mode)
        self.basis_strategy = BasisCaptureStrategy(cfg, self.exchanges, self.risk_manager,
                                                    self.alert_manager, paper_mode)
        self.profit_extractor = ProfitExtractor(cfg, self.exchanges, self.alert_manager)

        # Snapshot the original configured capital so paper-mode sync
        # always adds profits on top of the true starting amount.
        self._initial_capital = float(cfg["capital"]["total_usdt"])

        # Scheduler — must exist before TelegramCommandHandler is created
        self.scheduler = BackgroundScheduler(timezone="Australia/Perth")
        self._register_jobs()

        # Telegram command handler (needs scheduler reference)
        self.telegram_commands = TelegramCommandHandler(self)

        # Sync capital immediately on startup so today's sizing reflects
        # any profits already accumulated from previous sessions.
        self._sync_capital_from_balances(startup=True)

        logger.info("Bot engine initialised")

    # ── Scheduler setup ───────────────────────────────────────────────────────

    def _register_jobs(self):
        interval_s = self.cfg["strategy"]["position_check_interval"]

        # Main strategy tick
        self.scheduler.add_job(
            self._strategy_tick,
            IntervalTrigger(seconds=interval_s),
            id="strategy_tick",
            max_instances=1,
            coalesce=True,
        )

        # Risk check every 60 seconds
        self.scheduler.add_job(
            self._risk_tick,
            IntervalTrigger(seconds=60),
            id="risk_tick",
            max_instances=1,
            coalesce=True,
        )

        # Daily report at 23:55 AWST
        self.scheduler.add_job(
            self._daily_report_job,
            CronTrigger(hour=23, minute=55),
            id="daily_report",
        )

        # ── Withdrawals DISABLED ──────────────────────────────────────────────
        # All profits stay on exchange as compounding capital.
        # weekly_withdrawal, monthly_tasks, and compound_capital jobs removed.
        # To re-enable: uncomment the three scheduler.add_job blocks below.
        #
        # self.scheduler.add_job(
        #     self.profit_extractor.weekly_withdrawal,
        #     CronTrigger(day_of_week="fri", hour=18, minute=0),
        #     id="weekly_withdrawal",
        # )
        # self.scheduler.add_job(
        #     self.profit_extractor.monthly_tasks,
        #     CronTrigger(day="last", hour=22, minute=0),
        #     id="monthly_tasks",
        # )
        # self.scheduler.add_job(
        #     self._compound_job,
        #     CronTrigger(day="last", hour=22, minute=5),
        #     id="compound_capital",
        # )

        # Daily database backup — 00:00 AWST (5 min before capital sync)
        # Snapshots bot.db so trade history & accumulated profits are recoverable
        # if the live DB is corrupted, deleted, or the disk fails.
        # Keeps the most recent 30 backups; older ones are auto-pruned.
        self.scheduler.add_job(
            self._backup_database,
            CronTrigger(hour=0, minute=0),
            id="db_backup",
        )

        # Daily capital sync — 00:05 AWST every day
        # Reads actual USDT equity from both exchanges and updates position sizing.
        # In paper mode uses starting capital + cumulative paper profits.
        self.scheduler.add_job(
            self._sync_capital_from_balances,
            CronTrigger(hour=0, minute=5),
            id="capital_sync",
        )

        # Hourly Telegram progress report
        self.scheduler.add_job(
            self._hourly_telegram_report,
            IntervalTrigger(hours=1),
            id="hourly_telegram",
            max_instances=1,
            coalesce=True,
        )

        # Daily detailed pair + leverage report - fires at 23:55 UTC
        self.scheduler.add_job(
            self._daily_pair_report,
            CronTrigger(hour=23, minute=55, timezone="UTC"),
            id="daily_pair_report",
            max_instances=1,
            coalesce=True,
        )

        # Orphan detector — runs every 60s. Reconciles bot's tracked open trades
        # vs actual exchange positions. Auto-closes any unknown positions.
        # This is the safety net for any partial fills, race conditions, or bugs.
        self.scheduler.add_job(
            self._detect_and_close_orphans,
            IntervalTrigger(seconds=60),
            id="orphan_scan",
            max_instances=1,
            coalesce=True,
        )

        # Funding window boost — 30 min BEFORE each settlement (UTC)
        # Binance & Bybit settle at 00:00, 08:00, 16:00 UTC
        for h, m in [(23, 30), (7, 30), (15, 30)]:
            self.scheduler.add_job(
                self._enter_funding_window,
                CronTrigger(hour=h, minute=m, timezone="UTC"),
                id=f"funding_window_start_{h:02d}{m:02d}",
            )

        # Restore normal rate 15 min AFTER each settlement
        for h, m in [(0, 15), (8, 15), (16, 15)]:
            self.scheduler.add_job(
                self._exit_funding_window,
                CronTrigger(hour=h, minute=m, timezone="UTC"),
                id=f"funding_window_end_{h:02d}{m:02d}",
            )

        logger.info("Scheduled jobs registered")

    # ── Tick handlers ─────────────────────────────────────────────────────────

    def _strategy_tick(self):
        try:
            if not self.risk_manager.is_trading_allowed():
                return
            self.strategy.tick()
            self.basis_strategy.tick()
        except Exception as e:
            logger.error(f"Strategy tick error: {e}", exc_info=True)

    def _risk_tick(self):
        try:
            self.risk_manager.tick()
        except Exception as e:
            logger.error(f"Risk tick error: {e}", exc_info=True)

    def _detect_and_close_orphans(self):
        """
        Safety reconciler — runs every 60s.

        Compares bot's tracked open trades (DB) against ACTUAL exchange positions.
        Any position the exchange shows but the bot doesn't know about → ORPHAN.
        Closes orphans automatically at market and alerts via Telegram.

        Catches all failure modes:
        - Half-filled trades the bot lost track of
        - Race conditions in cancel-replace cycles
        - Manual positions opened on the same account (don't do this!)
        - Bugs in close logic that report success but don't actually close
        """
        if self.paper_mode:
            return  # Paper mode has no real exchange positions

        try:
            from utils.database import get_open_trades
            # Build the set of (exchange, symbol) keys the bot knows are open
            tracked = set()

            # 1. Trades persisted to DB
            for trade in get_open_trades():
                tracked.add((trade.get("leg_a_exchange"), trade.get("leg_a_symbol")))
                tracked.add((trade.get("leg_b_exchange"), trade.get("leg_b_symbol")))

            # 2. In-flight entries (pre-claim before DB write — race protection)
            # The strategy stores pre-claims in _open_positions WITH leg/exchange info,
            # so orphan detector can recognize positions that are about to be logged.
            try:
                for pair_key, info in dict(self.strategy._open_positions).items():
                    if info and isinstance(info, dict):
                        if info.get("leg_a_exchange") and info.get("leg_a_symbol"):
                            tracked.add((info["leg_a_exchange"], info["leg_a_symbol"]))
                        if info.get("leg_b_exchange") and info.get("leg_b_symbol"):
                            tracked.add((info["leg_b_exchange"], info["leg_b_symbol"]))
            except Exception as e:
                logger.debug(f"orphan-scan: in-flight check failed: {e}")

            # Scan each exchange for actual positions
            for ex_name, ex in self.exchanges.items():
                try:
                    positions = ex.fetch_positions()
                except Exception as e:
                    logger.debug(f"orphan-scan: fetch_positions failed on {ex_name}: {e}")
                    continue

                for pos in positions:
                    contracts = float(pos.get("contracts") or 0)
                    if abs(contracts) <= 0:
                        continue

                    sym = pos.get("symbol")
                    if not sym:
                        continue

                    # Is this position tracked by the bot?
                    if (ex_name, sym) in tracked:
                        continue   # known, expected, OK

                    # ORPHAN — exchange has position bot doesn't know about
                    side = pos.get("side", "unknown")
                    entry = pos.get("entryPrice", 0)
                    mark  = pos.get("markPrice", 0)
                    pnl   = pos.get("unrealizedPnl", 0)

                    logger.critical(
                        f"ORPHAN DETECTED on {ex_name}: {sym} {side} {contracts} contracts "
                        f"(entry={entry}, mark={mark}, unrealized PnL=${pnl}) — closing at market"
                    )
                    self.alert_manager.send_telegram(
                        f"<b>🚨 ORPHAN DETECTED</b>\n"
                        f"Exchange: <code>{ex_name}</code>\n"
                        f"Symbol: <code>{sym}</code>\n"
                        f"Side: {side}\n"
                        f"Size: {contracts}\n"
                        f"Unrealized PnL: ${pnl}\n"
                        f"Auto-closing at market..."
                    )

                    # Close at market
                    try:
                        ex.close_position(sym, side, abs(contracts), order_type="market")
                        logger.critical(f"ORPHAN CLOSED: {ex_name} {sym} {contracts}")
                        self.alert_manager.send_telegram(
                            f"<b>✅ Orphan closed</b>\n"
                            f"<code>{ex_name} {sym}</code> ({contracts} contracts)"
                        )
                        # Record orphan as a synthetic closed trade so /snapshot
                        # and reporting reflect the wallet truth. We use unrealized
                        # PnL at detection time as the realized P&L approximation
                        # (market-close slippage will be small extra cost beyond this).
                        try:
                            from utils.database import log_trade
                            from datetime import datetime, timezone
                            try:    pnl_f = float(pnl or 0)
                            except: pnl_f = 0.0
                            try:    entry_f = float(entry or 0)
                            except: entry_f = 0.0
                            try:    notional = abs(float(contracts)) * float(mark or 0)
                            except: notional = 0.0
                            # Subtract round-trip taker fees (entry already paid, close about to pay)
                            taker_fees = {"binance": 0.0004, "bybit": 0.00055}
                            fee_rate = taker_fees.get(ex_name, 0.0005)
                            fees_est = notional * fee_rate * 2
                            net_after_fees = pnl_f - fees_est
                            log_trade({
                                "timestamp":      datetime.now(timezone.utc).isoformat(),
                                "pair":           f"ORPHAN_{ex_name}_{sym}",
                                "leg_a_exchange": ex_name,
                                "leg_b_exchange": None,
                                "leg_a_symbol":   sym,
                                "leg_b_symbol":   None,
                                "notional_usd":   notional,
                                "spread_pct":     0.0,
                                "gross_profit":   pnl_f,
                                "fees":           fees_est,
                                "net_profit":     net_after_fees,
                                "funding_income": 0.0,
                                "status":         "closed",
                                "paper_mode":     0 if not self.paper_mode else 1,
                                "amount_a":       abs(float(contracts)),
                                "amount_b":       0.0,
                                "entry_price_a":  entry_f,
                                "entry_price_b":  0.0,
                                "direction":      side,
                                "notes":          "orphan_close",
                            })
                        except Exception as le:
                            logger.warning(f"Failed to log synthetic orphan trade: {le}")
                    except Exception as e:
                        logger.critical(
                            f"ORPHAN CLOSE FAILED on {ex_name} {sym}: {e} — "
                            f"MANUAL ACTION REQUIRED"
                        )
                        self.alert_manager.send_telegram(
                            f"<b>🚨🚨 ORPHAN CLOSE FAILED — MANUAL ACTION REQUIRED 🚨🚨</b>\n"
                            f"Exchange: <code>{ex_name}</code>\n"
                            f"Symbol: <code>{sym}</code>\n"
                            f"Size: {contracts}\n"
                            f"Error: <code>{str(e)[:200]}</code>\n\n"
                            f"<b>Log into {ex_name} app and close manually NOW!</b>"
                        )
        except Exception as e:
            logger.error(f"_detect_and_close_orphans error: {e}", exc_info=True)

    def _daily_report_job(self):
        try:
            save_todays_report(self.exchanges, paper_mode=self.paper_mode)
            generate_daily_report(
                print_to_console=False,
                strategy=self.strategy,
                risk_manager=self.risk_manager,
                paper_mode=self.paper_mode,
            )
        except Exception as e:
            logger.error(f"Daily report error: {e}", exc_info=True)

    def _hourly_telegram_report(self):
        """Send a concise progress snapshot to Telegram every hour."""
        try:
            import sqlite3
            from datetime import date, timedelta
            from pathlib import Path

            db_path = str(Path(__file__).resolve().parent.parent / "data" / "bot.db")
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row

            today = date.today().isoformat()

            # Today's stats
            today_rows = con.execute(
                "SELECT net_profit FROM trades WHERE DATE(timestamp,'localtime')=? AND status='closed'",
                (today,)
            ).fetchall()
            today_net    = sum(r["net_profit"] or 0 for r in today_rows)
            today_trades = len(today_rows)
            today_wins   = sum(1 for r in today_rows if (r["net_profit"] or 0) > 0)
            today_wr     = (today_wins / today_trades * 100) if today_trades else 0

            # All-time
            all_rows = con.execute(
                "SELECT net_profit FROM trades WHERE status='closed'"
            ).fetchall()
            total_net    = sum(r["net_profit"] or 0 for r in all_rows)
            total_trades = len(all_rows)

            # Open positions
            opens = con.execute(
                "SELECT pair FROM trades WHERE status='open'"
            ).fetchall()
            open_count = len(opens)

            # Best pair today
            pair_rows = con.execute("""
                SELECT leg_a_symbol,
                       SUM(net_profit) as net,
                       COUNT(*) as cnt,
                       SUM(CASE WHEN net_profit>0 THEN 1 ELSE 0 END) as wins
                FROM trades
                WHERE DATE(timestamp,'localtime')=? AND status='closed'
                GROUP BY leg_a_symbol ORDER BY net DESC LIMIT 1
            """, (today,)).fetchone()

            # Worst pair today
            worst_row = con.execute("""
                SELECT leg_a_symbol, SUM(net_profit) as net, COUNT(*) as cnt
                FROM trades
                WHERE DATE(timestamp,'localtime')=? AND status='closed'
                GROUP BY leg_a_symbol ORDER BY net ASC LIMIT 1
            """, (today,)).fetchone()

            # Monthly projection
            days_this_month = max(date.today().day, 1)
            month_start = date.today().replace(day=1).isoformat()
            month_rows = con.execute(
                "SELECT net_profit FROM trades WHERE DATE(timestamp,'localtime')>=? AND status='closed'",
                (month_start,)
            ).fetchall()
            month_net   = sum(r["net_profit"] or 0 for r in month_rows)
            proj_monthly = (month_net / days_this_month) * 30

            con.close()

            # Dynamic daily loss limit
            limit = self.risk_manager.get_effective_daily_loss_limit()
            mode  = "PAPER" if self.paper_mode else "LIVE"

            # Progress bar (20 chars)
            monthly_target = self.cfg.get("reporting", {}).get("monthly_target", 600)
            bar_fill  = min(int((month_net / monthly_target) * 20), 20) if monthly_target else 0
            bar       = "#" * bar_fill + "." * (20 - bar_fill)
            bar_pct   = (month_net / monthly_target * 100) if monthly_target else 0

            sign_today = "+" if today_net >= 0 else ""
            sign_total = "+" if total_net >= 0 else ""

            best_line  = ""
            if pair_rows and pair_rows["net"] > 0:
                sym = pair_rows["leg_a_symbol"].replace("/USDT:USDT", "")
                bwr = pair_rows["wins"] / pair_rows["cnt"] * 100 if pair_rows["cnt"] else 0
                best_line = f"\nBest pair : {sym} ({pair_rows['cnt']}t, {sign_today}${pair_rows['net']:.2f}, {bwr:.0f}% WR)"

            worst_line = ""
            if worst_row and (worst_row["net"] or 0) < 0:
                sym = worst_row["leg_a_symbol"].replace("/USDT:USDT", "")
                worst_line = f"\nWatch     : {sym} ({worst_row['cnt']}t, ${worst_row['net']:.2f})"

            msg = (
                f"<b>Delta-Neutral Bot [{mode}]</b>\n"
                f"<code>-----------------------------</code>\n"
                f"<b>TODAY ({today})</b>\n"
                f"Net P&amp;L    : <b>{sign_today}${today_net:.2f}</b>\n"
                f"Trades     : {today_trades}  |  WR: {today_wr:.0f}%"
                f"{best_line}"
                f"{worst_line}\n"
                f"<code>-----------------------------</code>\n"
                f"<b>MONTH SO FAR</b>\n"
                f"Month net  : {'+' if month_net >= 0 else ''}${month_net:.2f}\n"
                f"Projected  : ${proj_monthly:.0f}/mo  [{bar}] {bar_pct:.0f}%\n"
                f"<code>-----------------------------</code>\n"
                f"All-time   : {sign_total}${total_net:.2f}  ({total_trades} trades)\n"
                f"Open pos   : {open_count}\n"
                f"Loss limit : -${limit:.0f} (dynamic)\n"
            )

            self.alert_manager.send_telegram(msg)
            logger.info("Hourly Telegram progress report sent")

        except Exception as e:
            logger.error(f"Hourly Telegram report error: {e}", exc_info=True)

    def _daily_pair_report(self):
        """End-of-day breakdown: per-pair P&L, win rate, and per-leverage-tier summary."""
        try:
            import sqlite3
            from datetime import date
            from pathlib import Path

            db_path = str(Path(__file__).resolve().parent.parent / "data" / "bot.db")
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            today = date.today().isoformat()

            rows = con.execute("""
                SELECT pair, leg_a_symbol, net_profit, gross_profit, fees, spread_pct
                FROM trades
                WHERE DATE(timestamp,'localtime')=? AND status='closed'
            """, (today,)).fetchall()
            con.close()

            if not rows:
                self.alert_manager.send_telegram(
                    f"<b>Daily Report</b> <code>{today}</code>\nNo closed trades today."
                )
                return

            per_pair = {}
            for r in rows:
                sym = (r["leg_a_symbol"] or r["pair"]).replace("/USDT:USDT", "").replace(":USDT", "")
                d = per_pair.setdefault(sym, {"trades": 0, "net": 0.0, "fees": 0.0, "wins": 0})
                d["trades"] += 1
                d["net"]    += r["net_profit"]   or 0
                d["fees"]   += r["fees"]         or 0
                if (r["net_profit"] or 0) > 0:
                    d["wins"] += 1

            def _tier(spr):
                if spr is None:        return "??"
                if spr < 0.0020:       return "7x"
                if spr < 0.0030:       return "8x"
                return "10x"

            per_tier = {"7x": [0, 0.0, 0], "8x": [0, 0.0, 0], "10x": [0, 0.0, 0]}
            for r in rows:
                t = _tier(r["spread_pct"])
                if t in per_tier:
                    per_tier[t][0] += 1
                    per_tier[t][1] += r["net_profit"] or 0
                    if (r["net_profit"] or 0) > 0:
                        per_tier[t][2] += 1

            losers  = sorted([(s,d) for s,d in per_pair.items() if d["net"] < 0], key=lambda x: x[1]["net"])
            winners = sorted([(s,d) for s,d in per_pair.items() if d["net"] > 0], key=lambda x: -x[1]["net"])

            total_net    = sum(d["net"]    for d in per_pair.values())
            total_trades = sum(d["trades"] for d in per_pair.values())
            total_wins   = sum(d["wins"]   for d in per_pair.values())
            total_wr     = (total_wins / total_trades * 100) if total_trades else 0
            sign         = "+" if total_net >= 0 else ""

            mode = "PAPER" if self.paper_mode else "LIVE"
            lines = [
                f"<b>Daily Pair Report [{mode}]</b>  <code>{today}</code>",
                f"Net: <b>{sign}${total_net:.2f}</b> | {total_trades} trades | WR {total_wr:.0f}%",
                ""
            ]

            def fmt_row(sym, d):
                wr = (d["wins"] / d["trades"] * 100) if d["trades"] else 0
                s  = "+" if d["net"] >= 0 else ""
                return f"{sym:<7s} {d['trades']:>3d}t  {s}${d['net']:>7.2f}  WR{wr:>3.0f}%"

            lines.append("<b>Losing pairs</b>")
            if losers:
                lines.append("<pre>" + "\n".join(fmt_row(s,d) for s,d in losers[:8]) + "</pre>")
            else:
                lines.append("<i>none today</i>")

            lines.append("")
            lines.append("<b>Top winners</b>")
            if winners:
                lines.append("<pre>" + "\n".join(fmt_row(s,d) for s,d in winners[:8]) + "</pre>")
            else:
                lines.append("<i>none today</i>")

            lines.append("")
            lines.append("<b>By leverage tier</b>")
            tier_lines = []
            for t in ["7x", "8x", "10x"]:
                n, net, wins = per_tier[t]
                if n == 0:
                    tier_lines.append(f"{t:>3s}    0t       --      --")
                    continue
                avg = net / n
                wr  = wins / n * 100
                s   = "+" if net >= 0 else ""
                tier_lines.append(f"{t:>3s} {n:>4d}t  {s}${net:>7.2f}  avg{s}${avg:>5.2f}  WR{wr:>3.0f}%")
            lines.append("<pre>" + "\n".join(tier_lines) + "</pre>")

            self.alert_manager.send_telegram("\n".join(lines))
            logger.info("Daily Telegram pair report sent")

        except Exception as e:
            logger.error(f"Daily Telegram pair report error: {e}", exc_info=True)

    def _compound_job(self):
        try:
            compound_capital(self.cfg, paper_mode=self.paper_mode)
        except Exception as e:
            logger.error(f"Compound capital error: {e}", exc_info=True)

    def _backup_database(self, max_backups: int = 30):
        """
        Snapshot bot.db to data/bot_backup_YYYYMMDD_HHMMSS.db.

        Runs daily at 00:00 AWST (right before the capital sync) so each backup
        captures the previous day's final state. The most recent `max_backups`
        snapshots are kept; older ones are pruned to stop the disk filling up.

        Uses shutil.copy2 — atomic enough for SQLite when the bot is idle, which
        00:00 AWST always is (no funding window, no strategy tick mid-flight on
        a 5-second tick interval). For extra safety we could switch to the
        sqlite3 backup API, but copy2 is simpler and sufficient here.
        """
        try:
            data_dir = Path(__file__).resolve().parent.parent / "data"
            src = data_dir / "bot.db"
            if not src.exists():
                logger.warning(f"DB backup skipped: {src} not found")
                return

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dst = data_dir / f"bot_backup_{ts}.db"
            shutil.copy2(src, dst)
            logger.info(f"DB backup created: {dst.name} ({dst.stat().st_size / 1024:.1f} KB)")

            # Prune old backups — keep newest `max_backups`
            backups = sorted(
                data_dir.glob("bot_backup_*.db"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for old in backups[max_backups:]:
                try:
                    old.unlink()
                    logger.info(f"DB backup pruned: {old.name}")
                except Exception as e:
                    logger.warning(f"Could not prune {old.name}: {e}")
        except Exception as e:
            logger.error(f"DB backup error: {e}", exc_info=True)

    def _sync_capital_from_balances(self, startup: bool = False):
        """
        Update total_usdt in the live config from real exchange balances.

        Live mode  — reads USDT equity (free + unrealised PnL) from Binance
                     and Bybit, sums them, updates cfg["capital"]["total_usdt"].
        Paper mode — uses initial capital + all cumulative paper net profit so
                     position sizing grows as virtual profits accumulate.

        Runs automatically at 00:05 AWST every day, and once at startup.
        Withdrawals are DISABLED — all profits stay on-exchange as capital.
        """
        try:
            old_capital = self.cfg["capital"]["total_usdt"]

            if self.paper_mode:
                # ── Paper: virtual balance ─────────────────────────────────
                import sqlite3
                from pathlib import Path
                db_path = str(Path(__file__).resolve().parent.parent / "data" / "bot.db")
                con = sqlite3.connect(db_path)
                row = con.execute(
                    "SELECT SUM(net_profit) FROM trades WHERE status='closed'"
                ).fetchone()
                con.close()
                cumulative_profit = float(row[0] or 0)
                new_capital = round(self._initial_capital + cumulative_profit, 2)
                source = "paper virtual balance"
            else:
                # ── Live: read real equity from both exchanges ─────────────
                binance_equity = 0.0
                bybit_equity   = 0.0
                if "binance" in self.exchanges:
                    binance_equity = self.exchanges["binance"].get_total_equity()
                if "bybit" in self.exchanges:
                    bybit_equity = self.exchanges["bybit"].get_total_equity()
                new_capital = round(binance_equity + bybit_equity, 2)
                source = f"Binance ${binance_equity:,.2f} + Bybit ${bybit_equity:,.2f}"

            # Sanity checks — reject obviously wrong values.
            # 1. Absolute floor: < $100 is almost certainly an API failure
            if new_capital < 100:
                logger.warning(f"Capital sync skipped - suspiciously low value ${new_capital:.2f}")
                return
            # 2. Ratio guard: if new capital is less than 50% of current, one exchange
            #    likely returned 0 (API down / auth failure). Log and skip rather than
            #    halving position sizing unexpectedly.
            if old_capital > 0:
                ratio = new_capital / old_capital
                if ratio < 0.50:
                    logger.warning(
                        f"Capital sync skipped - new ${new_capital:,.2f} is "
                        f"{(1-ratio)*100:.0f}% below current ${old_capital:,.2f}. "
                        f"Possible exchange API failure. Check balances manually."
                    )
                    self.alert_manager.send_telegram(
                        f"<b>⚠️ Capital Sync SKIPPED</b>\n"
                        f"Reported balance ${new_capital:,.2f} is {(1-ratio)*100:.0f}% "
                        f"below current ${old_capital:,.2f}.\n"
                        f"Possible exchange API failure - sizing unchanged.\n"
                        f"Use /balance to check manually."
                    )
                    return

            self.cfg["capital"]["total_usdt"] = new_capital
            change   = new_capital - old_capital
            sign     = "+" if change >= 0 else ""
            tag      = "STARTUP" if startup else "DAILY SYNC"
            mode_tag = "PAPER" if self.paper_mode else "LIVE"

            # ── Auto-scale risk/sizing params with capital ─────────────────
            scaling = self._auto_scale_params(new_capital)

            logger.info(
                f"[{tag}] Capital updated: ${old_capital:,.2f} -> ${new_capital:,.2f} "
                f"({sign}${change:,.2f})  [{source}]"
            )
            logger.info(
                f"[{tag}] Auto-scaled: reserve=${scaling['reserve']:.0f} "
                f"daily_loss=${scaling['daily_loss']:.0f} "
                f"notional_cap=${scaling['notional_cap']:,.0f}"
            )

            # ── Telegram notification ──────────────────────────────────────
            # Skip startup notification when change is near-zero (first boot
            # with no prior profits yet, or same-day restart).
            if not startup or abs(change) > 1.0:
                msg = (
                    f"<b>💰 Capital Sync [{mode_tag}] — {tag}</b>\n"
                    f"<code>-----------------------------</code>\n"
                    f"Previous capital : <b>${old_capital:,.2f}</b>\n"
                    f"New capital      : <b>${new_capital:,.2f}</b>\n"
                    f"Change           : <b>{sign}${change:,.2f}</b>\n"
                    f"<code>-----------------------------</code>\n"
                    f"Source           : {source}\n"
                    f"<code>-----------------------------</code>\n"
                    f"<b>Risk params auto-scaled:</b>\n"
                    f"Reserve          : ${scaling['reserve']:,.0f}\n"
                    f"Daily loss limit : ${scaling['daily_loss']:,.0f} base\n"
                    f"Notional cap     : ${scaling['notional_cap']:,.0f}/trade\n"
                    f"Notional/trade   : ~${scaling['est_notional']:,.0f} (est)\n"
                    f"<code>-----------------------------</code>\n"
                    f"All profits stay on-exchange (withdrawals off)."
                )
                self.alert_manager.send_telegram(msg)

        except Exception as e:
            logger.error(f"Capital sync error: {e}", exc_info=True)

    def _auto_scale_params(self, capital: float) -> dict:
        """
        Automatically adjust reserve, daily-loss limit, and notional cap
        whenever capital changes (called from _sync_capital_from_balances).

        All three values live in cfg so the strategy picks them up immediately
        on the next tick — no restart required.

        Scaling rules
        -------------
        Reserve          : 10% of capital, min $100
                           Acts as a virtual buffer in the sizing formula —
                           NOT a physical transfer.  Keep all funds in the
                           Binance Futures / Bybit Perpetuals wallets.

        Daily loss limit : 5% of capital, min $50
        Profit step      : 10% of capital (every X profit → unlock more loss room)
        Step amount      : 2.5% of capital per step

        Notional cap (per trade)
          < $5,000    →  $2,500
          $5k–$15k    →  $5,000
          $15k–$50k   →  $10,000
          $50k–$100k  →  $15,000
          > $100k     →  $25,000
        """
        # ── Reserve ───────────────────────────────────────────────────────
        new_reserve = max(round(capital * 0.10), 100)
        self.cfg["capital"]["stablecoin_reserve"] = new_reserve

        # ── Daily loss limit (dynamic base) ───────────────────────────────
        new_daily_loss   = max(round(capital * 0.05), 50)
        new_profit_step  = max(round(capital * 0.10), 100)
        new_step_amount  = max(round(capital * 0.025), 25)
        self.cfg["risk"]["max_daily_loss"]          = new_daily_loss
        self.cfg["risk"]["daily_loss_profit_step"]  = new_profit_step
        self.cfg["risk"]["daily_loss_step_amount"]  = new_step_amount

        # ── Notional cap per trade ─────────────────────────────────────────
        # Tier-based: each jump roughly doubles the cap as capital doubles.
        # Below $5k the formula naturally stays under $2,500 anyway.
        if capital < 5_000:
            new_notional_cap = 2_500
        elif capital < 15_000:
            new_notional_cap = 5_000
        elif capital < 50_000:
            new_notional_cap = 10_000
        elif capital < 100_000:
            new_notional_cap = 15_000
        else:
            new_notional_cap = 25_000
        self.cfg["strategy"]["max_notional_per_trade"] = new_notional_cap

        # ── Estimated notional per trade (for Telegram display) ───────────
        tradeable    = (capital - new_reserve) * (1 - self.cfg["capital"]["free_margin_buffer"])
        leverage     = self.cfg["leverage"]["default"]
        deploy_pct   = self.cfg["position_sizing"]["medium_spread_deploy"]
        concurrent   = 4
        est_notional = min(tradeable * leverage * deploy_pct / concurrent, new_notional_cap)

        return {
            "reserve":      new_reserve,
            "daily_loss":   new_daily_loss,
            "notional_cap": new_notional_cap,
            "est_notional": est_notional,
        }

    def _register_ws_symbols(self):
        """Register all active pair symbols with the WS price cache."""
        for pair in self.cfg.get("pairs", []):
            if pair.get("active"):
                self.ws_cache.register(pair["leg_a_exchange"], pair["leg_a_symbol"])
                self.ws_cache.register(pair["leg_b_exchange"], pair["leg_b_symbol"])

    def _enter_funding_window(self):
        """Boost strategy scan to fast interval during funding settlement window."""
        fast = self.cfg["strategy"].get("funding_window_fast_interval", 5)
        logger.info(f"FUNDING WINDOW: boosting scan rate to {fast}s")
        try:
            self.scheduler.reschedule_job(
                "strategy_tick", trigger=IntervalTrigger(seconds=fast)
            )
        except Exception as e:
            logger.warning(f"Could not reschedule strategy tick: {e}")

    def _exit_funding_window(self):
        """Restore normal scan interval after funding window."""
        normal = self.cfg["strategy"]["position_check_interval"]
        logger.info(f"FUNDING WINDOW ended: restoring scan to {normal}s")
        try:
            self.scheduler.reschedule_job(
                "strategy_tick", trigger=IntervalTrigger(seconds=normal)
            )
        except Exception as e:
            logger.warning(f"Could not reschedule strategy tick: {e}")

    # ── Startup reconciliation ────────────────────────────────────────────────

    def _startup_position_reconciliation(self):
        """Close any pre-existing exchange positions that don't match a DB-tracked
        open trade. Runs ONCE at startup, BEFORE the scheduler starts.

        Without this, naked positions left from a previous mid-trade shutdown
        survive across restarts. The 60s orphan detector compares only by
        (exchange, symbol) existence — when the bot enters a NEW trade on the
        same symbol, the old position becomes invisible (hidden under the
        legitimate one) and bleeds money silently.
        """
        try:
            from utils.database import log_trade, get_open_trades
            from datetime import datetime, timezone

            # Anything the DB currently considers open is "expected".
            tracked = set()
            for trade in get_open_trades():
                if trade.get("leg_a_exchange") and trade.get("leg_a_symbol"):
                    tracked.add((trade["leg_a_exchange"], trade["leg_a_symbol"]))
                if trade.get("leg_b_exchange") and trade.get("leg_b_symbol"):
                    tracked.add((trade["leg_b_exchange"], trade["leg_b_symbol"]))

            taker_fees = {"binance": 0.0004, "bybit": 0.00055}
            closed_count = 0
            for ex_name, ex in self.exchanges.items():
                try:
                    positions = ex.fetch_positions()
                except Exception as e:
                    logger.warning(f"Startup reconcile: fetch_positions failed on {ex_name}: {e}")
                    continue

                for pos in positions:
                    contracts = float(pos.get("contracts") or 0)
                    if abs(contracts) <= 0:
                        continue
                    sym = pos.get("symbol")
                    if not sym:
                        continue
                    if (ex_name, sym) in tracked:
                        # Tracked — leave alone, the strategy/exit logic will handle.
                        logger.info(f"Startup reconcile: {ex_name} {sym} matches DB-tracked open trade, kept.")
                        continue

                    side = pos.get("side", "unknown")
                    entry = float(pos.get("entryPrice") or 0)
                    mark  = float(pos.get("markPrice") or 0)
                    pnl   = float(pos.get("unrealizedPnl") or 0)

                    logger.critical(
                        f"STARTUP ORPHAN: {ex_name} {sym} {side} {contracts} contracts "
                        f"(entry={entry}, mark={mark}, pnl=${pnl:.2f}) — closing at market"
                    )
                    self.alert_manager.send_telegram(
                        f"<b>🚨 STARTUP ORPHAN DETECTED</b>\n"
                        f"Exchange: <code>{ex_name}</code>\n"
                        f"Symbol: <code>{sym}</code>\n"
                        f"Side: {side} | Size: {contracts}\n"
                        f"Unrealized PnL: ${pnl:.2f}\n"
                        f"Auto-closing at market before scheduler starts..."
                    )
                    try:
                        ex.close_position(sym, side, abs(contracts), order_type="market")
                        logger.critical(f"STARTUP ORPHAN CLOSED: {ex_name} {sym} {contracts}")
                        closed_count += 1
                        # Synthetic trade row so /snapshot reflects the loss.
                        try:
                            notional = abs(contracts) * mark
                            fee_rate = taker_fees.get(ex_name, 0.0005)
                            fees_est = notional * fee_rate * 2
                            log_trade({
                                "timestamp":      datetime.now(timezone.utc).isoformat(),
                                "pair":           f"STARTUP_ORPHAN_{ex_name}_{sym}",
                                "leg_a_exchange": ex_name,
                                "leg_b_exchange": None,
                                "leg_a_symbol":   sym,
                                "leg_b_symbol":   None,
                                "notional_usd":   notional,
                                "spread_pct":     0.0,
                                "gross_profit":   pnl,
                                "fees":           fees_est,
                                "net_profit":     pnl - fees_est,
                                "funding_income": 0.0,
                                "status":         "closed",
                                "paper_mode":     0 if not self.paper_mode else 1,
                                "amount_a":       abs(contracts),
                                "amount_b":       0.0,
                                "entry_price_a":  entry,
                                "entry_price_b":  0.0,
                                "direction":      str(side).lower(),
                                "notes":          "startup_orphan_close",
                            })
                        except Exception as le:
                            logger.warning(f"Failed to log startup orphan trade: {le}")
                    except Exception as e:
                        logger.critical(
                            f"STARTUP ORPHAN CLOSE FAILED on {ex_name} {sym}: {e} — "
                            f"REFUSING TO START. Close manually then restart."
                        )
                        self.alert_manager.send_telegram(
                            f"<b>🚨🚨 STARTUP ORPHAN CLOSE FAILED — BOT REFUSING TO START 🚨🚨</b>\n"
                            f"Exchange: <code>{ex_name}</code>\n"
                            f"Symbol: <code>{sym}</code> | Size: {contracts}\n"
                            f"Error: <code>{str(e)[:200]}</code>\n"
                            f"Close on the exchange UI then restart the bot."
                        )
                        raise SystemExit(1)
            if closed_count == 0:
                logger.info("Startup reconcile: no orphan positions found.")
            else:
                logger.critical(f"Startup reconcile: closed {closed_count} orphan position(s).")
        except SystemExit:
            raise
        except Exception as e:
            logger.error(f"Startup reconcile error: {e}", exc_info=True)
            # Do not block startup on unexpected reconciler bugs — but flag loudly.

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def run(self):
        # CRITICAL: reconcile pre-existing positions BEFORE the scheduler starts.
        # Without this, a previous mid-trade shutdown leaves naked positions on
        # exchanges; the strategy may then enter NEW trades that get merged with
        # the old ones, hiding the orphans from the (existence-only) detector.
        self._startup_position_reconciliation()

        self.running = True
        self.scheduler.start()
        self.telegram_commands.start()   # start inbound command polling
        logger.info("Bot engine running - press Ctrl+C to stop")

        # Start Flask dashboard in a separate thread if enabled
        if self.cfg.get("dashboard", {}).get("enabled"):
            self._start_dashboard()

        try:
            while self.running:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            self.shutdown()

    def shutdown(self):
        logger.info("Shutting down bot engine...")
        self.running = False
        self.ws_cache.stop()
        self.telegram_commands.stop()
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        logger.info("Bot engine stopped")

    def _start_dashboard(self):
        import threading
        from dashboard.app import create_app
        dash_cfg = self.cfg["dashboard"]
        app = create_app()
        t = threading.Thread(
            target=lambda: app.run(
                host=dash_cfg["host"],
                port=dash_cfg["port"],
                debug=False,
                use_reloader=False
            ),
            daemon=True
        )
        t.start()
        logger.info(f"Dashboard running at http://{dash_cfg['host']}:{dash_cfg['port']}")
