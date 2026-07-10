"""alerts/telegram_commands.py — Telegram bot command handler (inbound messages).

Polls the Telegram getUpdates endpoint every 3 seconds in a background thread.
Only responds to the TELEGRAM_CHAT_ID configured in .env — all other senders
are silently ignored for security.

Available commands (send from your phone):
  /help       — list all commands
  /balance    — live balance on Binance + Bybit (or virtual paper balance)
  /status     — today's P&L, WR, open positions, loss limit, bot state
  /snapshot   — full per-pair breakdown for today
  /pairs      — active pairs ranked by today's net profit
  /risk       — risk manager state, HWM, margin usage
  /pause      — halt new entries (does NOT close open positions)
  /resume     — re-enable trading after a pause
  /kill       — request emergency kill (requires confirmation)
  /kill CONFIRM — execute emergency kill (closes all positions, halts bot)
"""

import json
import logging
import os
import sqlite3
import threading
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path
import ssl
import certifi

logger = logging.getLogger(__name__)


class TelegramCommandHandler:
    """Background thread that polls Telegram for commands and dispatches them."""

    POLL_INTERVAL = 3   # seconds between getUpdates calls
    API_TIMEOUT   = 5   # seconds for each HTTP request

    def __init__(self, bot_engine):
        self.bot_engine    = bot_engine
        self.alert_manager = bot_engine.alert_manager
        self.risk_manager  = bot_engine.risk_manager
        self.exchanges     = bot_engine.exchanges
        self.cfg           = bot_engine.cfg
        self.paper_mode    = bot_engine.paper_mode
        self.scheduler     = bot_engine.scheduler

        self._offset   = 0          # Telegram update offset (sliding window)
        self._running  = False
        self._thread: threading.Thread | None = None
        self._pending_kill = False  # waiting for /kill CONFIRM

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            logger.info("Telegram not configured - command handler not started")
            return
        self._running = True
        self._thread  = threading.Thread(target=self._poll_loop, daemon=True, name="tg-cmd")
        self._thread.start()
        logger.info("Telegram command handler started (polling every 3s)")

    def stop(self):
        self._running = False

    # ── Poll loop ─────────────────────────────────────────────────────────────

    def _poll_loop(self):
        while self._running:
            try:
                updates = self._get_updates()
                for upd in updates:
                    self._dispatch(upd)
            except Exception as e:
                logger.debug(f"Telegram poll error: {e}")
            time.sleep(self.POLL_INTERVAL)

    def _get_updates(self) -> list:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        url   = (f"https://api.telegram.org/bot{token}/getUpdates"
                f"?offset={self._offset}&timeout=2&allowed_updates=%5B%22message%22%5D")

        req = urllib.request.Request(url)

        # ✅ FIX: use proper SSL cert bundle
        context = ssl.create_default_context(cafile=certifi.where())

        with urllib.request.urlopen(req, timeout=self.API_TIMEOUT, context=context) as resp:
            data = json.loads(resp.read())

        if not data.get("ok"):
            return []

        updates = data.get("result", [])
        if updates:
            self._offset = updates[-1]["update_id"] + 1

        return updates

    def _dispatch(self, update: dict):
        msg     = update.get("message", {})
        text    = (msg.get("text") or "").strip()
        chat_id = str(msg.get("chat", {}).get("id", ""))

        # Security: only the configured chat can send commands
        allowed = os.environ.get("TELEGRAM_CHAT_ID", "")
        if chat_id != allowed or not text.startswith("/"):
            return

        parts = text.split()
        cmd   = parts[0].lower().rstrip("@")   # strip @botname suffix

        logger.info(f"Telegram command received: {text!r}")

        try:
            if   cmd == "/help":     self._cmd_help()
            elif cmd == "/balance":  self._cmd_balance()
            elif cmd == "/status":   self._cmd_status()
            elif cmd == "/snapshot": self._cmd_snapshot()
            elif cmd == "/wallet":   self._cmd_wallet()
            elif cmd == "/pairs":    self._cmd_pairs()
            elif cmd == "/risk":     self._cmd_risk()
            elif cmd == "/pause":    self._cmd_pause()
            elif cmd == "/resume":   self._cmd_resume()
            elif cmd == "/kill":
                if len(parts) >= 2 and parts[1].upper() == "CONFIRM":
                    self._cmd_kill_confirmed()
                else:
                    self._cmd_kill_request()
            elif cmd == "/restart": self._cmd_restart()
            else:
                self._send(f"Unknown command: <code>{cmd}</code>\nSend /help to see all commands.")
        except Exception as e:
            logger.error(f"Command handler error for {cmd}: {e}", exc_info=True)
            self._send(f"⚠ Error executing <code>{cmd}</code>: {e}")

    # ── Commands ──────────────────────────────────────────────────────────────

    def _cmd_help(self):
        mode = "PAPER" if self.paper_mode else "LIVE"
        self._send(
            f"<b>Delta-Neutral Bot [{mode}] — Commands</b>\n"
            f"<code>─────────────────────────────</code>\n"
            f"/balance   — Exchange balances (Binance + Bybit)\n"
            f"/status    — Today's P&amp;L, WR, open positions\n"
            f"/snapshot  — Full per-pair breakdown today\n"
            f"/wallet    — Live exchange wallet truth (positions + balance)\n"
            f"/pairs     — Active pairs ranked by today's profit\n"
            f"/risk      — Risk manager state &amp; limits\n"
            f"/pause     — Halt new entries (keeps open positions)\n"
            f"/resume    — Resume trading after pause\n"
            f"/restart   — Soft restart: clear state, re-sync capital\n"
            f"/kill      — ⚡ Emergency: close all &amp; halt (needs /kill CONFIRM)"
        )

    # ── /balance ──────────────────────────────────────────────────────────────

    def _cmd_balance(self):
        mode = "PAPER" if self.paper_mode else "LIVE"
        try:
            if self.paper_mode:
                con = sqlite3.connect(self._db_path())
                row = con.execute("SELECT SUM(net_profit) FROM trades WHERE status='closed'").fetchone()
                con.close()
                profit  = float(row[0] or 0)
                total   = self.cfg["capital"]["total_usdt"]
                binance = round(total * self.cfg["capital"]["binance_allocation"], 2)
                bybit   = round(total * self.cfg["capital"]["bybit_allocation"],   2)
                reserve = self.cfg["capital"]["stablecoin_reserve"]
                self._send(
                    f"<b>💰 Balance [{mode}]</b>\n"
                    f"<code>─────────────────────────</code>\n"
                    f"Binance (virtual) : <b>${binance:,.2f}</b>\n"
                    f"Bybit   (virtual) : <b>${bybit:,.2f}</b>\n"
                    f"Reserve           : ${reserve:,.2f}\n"
                    f"<code>─────────────────────────</code>\n"
                    f"Total capital     : <b>${total:,.2f}</b>\n"
                    f"Cumulative profit : +${profit:,.2f}"
                )
            else:
                binance_eq = self.exchanges["binance"].get_total_equity() if "binance" in self.exchanges else 0
                bybit_eq   = self.exchanges["bybit"].get_total_equity()   if "bybit"   in self.exchanges else 0
                total      = binance_eq + bybit_eq
                start      = self.cfg["capital"].get("_initial", 4000)
                gained     = total - start
                sign       = "+" if gained >= 0 else ""
                self._send(
                    f"<b>💰 Balance [LIVE]</b>\n"
                    f"<code>─────────────────────────</code>\n"
                    f"Binance Futures   : <b>${binance_eq:,.2f}</b>\n"
                    f"Bybit Perp        : <b>${bybit_eq:,.2f}</b>\n"
                    f"<code>─────────────────────────</code>\n"
                    f"Combined total    : <b>${total:,.2f}</b>\n"
                    f"vs starting capital: {sign}${gained:,.2f}"
                )
        except Exception as e:
            self._send(f"⚠ Could not fetch balance: {e}")

    # ── /wallet ───────────────────────────────────────────────────────────────

    def _cmd_wallet(self):
        """Live wallet truth — bypasses the trades table.

        Shows actual exchange equity + open position count per exchange,
        plus the delta vs trades-table net so the user can see the gap
        (orphan losses, fees, funding, slippage)."""
        try:
            if self.paper_mode:
                self._send("⚠ /wallet only meaningful in LIVE mode — use /balance for paper.")
                return

            lines = ["<b>👛 Wallet Truth [LIVE]</b>",
                     "<code>─────────────────────────</code>"]
            total_eq = 0.0
            total_pos = 0
            for name in ("binance", "bybit"):
                ex = self.exchanges.get(name)
                if ex is None:
                    continue
                try:
                    eq = float(ex.get_total_equity() or 0)
                except Exception as e:
                    eq = 0.0
                    lines.append(f"{name:<8} : <i>equity error: {e}</i>")
                    continue
                try:
                    positions = ex.fetch_positions()
                    n_pos = sum(1 for p in positions
                                if abs(float(p.get("contracts") or 0)) > 0)
                except Exception:
                    n_pos = 0
                total_eq  += eq
                total_pos += n_pos
                lines.append(f"<code>{name:<8}</code> : <b>${eq:,.2f}</b>  ({n_pos} pos)")

            start  = float(self.cfg["capital"].get("_initial",
                                                   self.cfg["capital"]["total_usdt"]))
            gained = total_eq - start
            sign   = "+" if gained >= 0 else ""

            # Compare to trades-table net for today (the gap = invisible costs)
            try:
                con = sqlite3.connect(self._db_path())
                today = date.today().isoformat()
                row = con.execute(
                    "SELECT COALESCE(SUM(net_profit),0) FROM trades "
                    "WHERE DATE(timestamp,'localtime')=? AND status='closed'",
                    (today,)
                ).fetchone()
                con.close()
                trades_net_today = float(row[0] or 0)
            except Exception:
                trades_net_today = 0.0

            lines.append("<code>─────────────────────────</code>")
            lines.append(f"Combined  : <b>${total_eq:,.2f}</b>  ({total_pos} open)")
            lines.append(f"vs start  : {sign}${gained:,.2f}")
            lines.append("<code>─────────────────────────</code>")
            lines.append(f"Trades-table net today : ${trades_net_today:,.2f}")
            lines.append(
                "<i>Gap = orphan closes + fees + funding + slippage "
                "(not in trades table)</i>"
            )
            self._send("\n".join(lines))
        except Exception as e:
            self._send(f"⚠ /wallet error: {e}")

    # ── /status ───────────────────────────────────────────────────────────────

    def _cmd_status(self):
        try:
            today = date.today().isoformat()
            con   = sqlite3.connect(self._db_path())
            con.row_factory = sqlite3.Row

            t_rows = con.execute(
                "SELECT net_profit FROM trades WHERE DATE(timestamp,'localtime')=? AND status='closed'",
                (today,)
            ).fetchall()
            opens = con.execute("SELECT leg_a_symbol FROM trades WHERE status='open'").fetchall()
            con.close()

            nets  = [r["net_profit"] or 0 for r in t_rows]
            n     = len(nets)
            net   = sum(nets)
            wins  = sum(1 for x in nets if x > 0)
            wr    = wins / n * 100 if n else 0
            limit = self.risk_manager.get_effective_daily_loss_limit()

            trading_ok  = self.risk_manager.is_trading_allowed()
            bot_state   = "✅ TRADING" if trading_ok else "⛔ HALTED"
            mode        = "PAPER" if self.paper_mode else "LIVE"
            sign        = "+" if net >= 0 else ""

            open_list = ""
            if opens:
                syms = [o["leg_a_symbol"].replace("/USDT:USDT", "") for o in opens[:8]]
                open_list = "\nOpen: " + ", ".join(syms)
                if len(opens) > 8:
                    open_list += f" +{len(opens)-8} more"

            self._send(
                f"<b>📊 Status [{mode}]  {datetime.now().strftime('%H:%M AWST')}</b>\n"
                f"<code>─────────────────────────</code>\n"
                f"Bot state     : {bot_state}\n"
                f"Today P&amp;L  : <b>{sign}${net:.2f}</b>  ({n} trades)\n"
                f"Win rate      : {wr:.0f}%\n"
                f"Open positions: {len(opens)}{open_list}\n"
                f"<code>─────────────────────────</code>\n"
                f"Loss limit    : -${limit:.0f}  (dynamic)\n"
                f"Remaining     : ${net + limit:.2f} before halt"
            )
        except Exception as e:
            self._send(f"⚠ Status error: {e}")

    # ── /snapshot ─────────────────────────────────────────────────────────────

    def _cmd_snapshot(self):
        try:
            today = date.today().isoformat()
            con   = sqlite3.connect(self._db_path())
            con.row_factory = sqlite3.Row

            rows = con.execute(
                "SELECT net_profit, spread_pct FROM trades "
                "WHERE DATE(timestamp,'localtime')=? AND status='closed'",
                (today,)
            ).fetchall()
            opens = con.execute("SELECT COUNT(*) as c FROM trades WHERE status='open'").fetchone()["c"]

            m_rows = con.execute(
                "SELECT net_profit FROM trades "
                "WHERE DATE(timestamp,'localtime')>=? AND status='closed'",
                (date.today().replace(day=1).isoformat(),)
            ).fetchall()
            con.close()

            nets    = [r["net_profit"] or 0 for r in rows]
            n       = len(nets)
            net     = sum(nets)
            wins    = sum(1 for x in nets if x > 0)
            wr      = wins / n * 100 if n else 0
            avg     = net / n if n else 0
            avg_sp  = sum(r["spread_pct"] or 0 for r in rows) / n * 100 if n else 0
            m_net   = sum(r["net_profit"] or 0 for r in m_rows)
            days    = max(date.today().day, 1)
            proj    = m_net / days * 30
            mode    = "PAPER" if self.paper_mode else "LIVE"
            sign    = "+" if net >= 0 else ""

            # Tier breakdown (by spread)
            t7  = [r["net_profit"] or 0 for r in rows if (r["spread_pct"] or 0) < 0.0020]
            t8  = [r["net_profit"] or 0 for r in rows if 0.0020 <= (r["spread_pct"] or 0) < 0.0030]
            t10 = [r["net_profit"] or 0 for r in rows if (r["spread_pct"] or 0) >= 0.0030]

            tiers = ""
            if t7:  tiers += f"\n 7x: {len(t7)}t  ${sum(t7):.2f}"
            if t8:  tiers += f"\n 8x: {len(t8)}t  ${sum(t8):.2f}"
            if t10: tiers += f"\n10x: {len(t10)}t  ${sum(t10):.2f}"

            self._send(
                f"<b>📈 Snapshot [{mode}]  {today}</b>\n"
                f"<code>─────────────────────────</code>\n"
                f"Net P&amp;L    : <b>{sign}${net:.2f}</b>\n"
                f"Trades     : {n}  |  WR: {wr:.0f}%\n"
                f"Avg/trade  : ${avg:.4f}\n"
                f"Avg spread : {avg_sp:.4f}%\n"
                f"Open pos   : {opens}\n"
                f"<code>─────────────────────────</code>\n"
                f"<b>Leverage tiers:</b>{tiers}\n"
                f"<code>─────────────────────────</code>\n"
                f"Month net  : ${m_net:.2f}\n"
                f"Projected  : ${proj:.0f}/mo"
            )
        except Exception as e:
            self._send(f"⚠ Snapshot error: {e}")

    # ── /pairs ────────────────────────────────────────────────────────────────

    def _cmd_pairs(self):
        try:
            today = date.today().isoformat()
            con   = sqlite3.connect(self._db_path())
            con.row_factory = sqlite3.Row
            rows  = con.execute("""
                SELECT leg_a_symbol,
                       COUNT(*) as t,
                       SUM(net_profit) as net,
                       SUM(CASE WHEN net_profit>0 THEN 1 ELSE 0 END) as wins
                FROM trades
                WHERE DATE(timestamp,'localtime')=? AND status='closed'
                GROUP BY leg_a_symbol ORDER BY net DESC
            """, (today,)).fetchall()
            con.close()

            if not rows:
                self._send("No closed trades today yet.")
                return

            lines = [f"<b>🏆 Pairs Today — {today}</b>"]
            lines.append("<code>Pair         T    Net      WR</code>")
            for r in rows[:15]:
                sym = r["leg_a_symbol"].replace("/USDT:USDT", "")
                wr  = r["wins"] / r["t"] * 100 if r["t"] else 0
                avg = (r["net"] or 0) / r["t"]
                emoji = "🟢" if (r["net"] or 0) > 0 else "🔴"
                lines.append(
                    f"{emoji} <code>{sym:<10}</code> {r['t']:3d}t "
                    f"${(r['net'] or 0):7.2f}  {wr:.0f}%"
                )
            if len(rows) > 15:
                lines.append(f"... and {len(rows)-15} more pairs")

            self._send("\n".join(lines))
        except Exception as e:
            self._send(f"⚠ Pairs error: {e}")

    # ── /risk ─────────────────────────────────────────────────────────────────

    def _cmd_risk(self):
        try:
            rm     = self.risk_manager
            limit  = rm.get_effective_daily_loss_limit()
            halted = not rm.is_trading_allowed()
            pdm    = rm.is_post_drawdown_mode()
            mode   = "PAPER" if self.paper_mode else "LIVE"

            today = date.today().isoformat()
            con   = sqlite3.connect(self._db_path())
            daily_net = con.execute(
                "SELECT SUM(net_profit) FROM trades WHERE DATE(timestamp,'localtime')=? AND status='closed'",
                (today,)
            ).fetchone()[0] or 0
            con.close()

            state = "⛔ HALTED" if halted else ("⚠ POST-DRAWDOWN" if pdm else "✅ ACTIVE")

            # Margin info (live only)
            margin_lines = ""
            if not self.paper_mode:
                for name, ex in self.exchanges.items():
                    try:
                        bal   = ex.fetch_balance()
                        used  = float(bal.get("used",  {}).get("USDT", 0) or 0)
                        total = float(bal.get("total", {}).get("USDT", 1) or 1)
                        pct   = used / total * 100
                        margin_lines += f"\n{name.capitalize()} margin: {pct:.1f}% used"
                    except Exception:
                        pass

            self._send(
                f"<b>🛡 Risk Status [{mode}]</b>\n"
                f"<code>─────────────────────────</code>\n"
                f"Trading state  : {state}\n"
                f"Daily P&amp;L   : ${daily_net:.2f}\n"
                f"Loss limit     : -${limit:.0f}  (dynamic)\n"
                f"Remaining room : ${daily_net + limit:.2f}\n"
                f"Post-drawdown  : {'YES — reduced sizing' if pdm else 'No'}"
                f"{margin_lines}\n"
                f"<code>─────────────────────────</code>\n"
                f"Account SL     : -{self.cfg['risk']['account_stop_loss_pct']*100:.0f}% unrealised\n"
                f"HWM floor      : -{self.cfg['risk']['high_water_mark_floor']*100:.0f}% from peak\n"
                f"Max margin use : {self.cfg['risk']['max_margin_usage']*100:.0f}%"
            )
        except Exception as e:
            self._send(f"⚠ Risk error: {e}")

    # ── /pause ────────────────────────────────────────────────────────────────

    def _cmd_pause(self):
        if not self.risk_manager.is_trading_allowed():
            self._send("⚠ Bot is already halted.")
            return
        self.risk_manager.halt_trading("Manual pause via Telegram")
        self._send(
            "⏸ <b>Bot PAUSED</b>\n"
            "New entries halted. Open positions remain open.\n"
            "Send /resume to restart trading."
        )
        logger.warning("Bot paused via Telegram command")

    # ── /resume ───────────────────────────────────────────────────────────────

    def _cmd_resume(self):
        if self.risk_manager.is_trading_allowed():
            self._send("✅ Bot is already running — nothing to resume.")
            return
        # Clear the halt
        self.risk_manager._trading_halted = False
        self.risk_manager._halt_reason    = ""
        self.risk_manager._halt_until     = None
        self._send(
            "▶️ <b>Bot RESUMED</b>\n"
            "Trading re-enabled. Strategy tick will pick up on next scan."
        )
        logger.info("Bot resumed via Telegram command")

    # ── /restart ──────────────────────────────────────────────────────────────

    def _cmd_restart(self):
        """
        Soft restart — no process kill, no position close.
        1. Clears any trading halt (resume if paused)
        2. Resets post-drawdown mode
        3. Re-syncs capital from live balances
        4. Reschedules the strategy tick so it fires immediately
        Use this after fixing a config issue, after a network blip, or any time
        the bot feels 'stuck' without needing to SSH in.
        """
        self._send("🔄 <b>Soft restart initiated...</b>")
        try:
            # 1. Clear halt state
            rm = self.risk_manager
            was_halted = rm._trading_halted
            rm._trading_halted      = False
            rm._halt_reason         = ""
            rm._halt_until          = None
            rm._post_drawdown_mode  = False
            rm._post_drawdown_until = None

            # 2. Re-sync capital from balances
            self.bot_engine._sync_capital_from_balances(startup=False)

            # 3. Reschedule strategy tick to fire immediately on next cycle
            from apscheduler.triggers.interval import IntervalTrigger
            interval_s = self.cfg["strategy"]["position_check_interval"]
            try:
                self.scheduler.reschedule_job(
                    "strategy_tick",
                    trigger=IntervalTrigger(seconds=interval_s)
                )
            except Exception:
                pass  # job may not exist yet on first call

            new_capital = self.cfg["capital"]["total_usdt"]
            halt_msg    = " (was HALTED — now resumed)" if was_halted else ""
            self._send(
                f"✅ <b>Soft restart complete{halt_msg}</b>\n"
                f"<code>─────────────────────────</code>\n"
                f"Trading state : ✅ ACTIVE\n"
                f"Capital synced: <b>${new_capital:,.2f}</b>\n"
                f"Strategy tick : rescheduled\n"
                f"All risk flags: cleared\n\n"
                f"Bot is scanning for trades again."
            )
            logger.info("Soft restart executed via Telegram command")
        except Exception as e:
            self._send(f"⚠ Restart error: {e}")
            logger.error(f"Soft restart error: {e}", exc_info=True)

    # ── /kill (request) ───────────────────────────────────────────────────────

    def _cmd_kill_request(self):
        self._pending_kill = True
        self._send(
            "⚡ <b>KILL SWITCH — Confirmation Required</b>\n"
            "<code>─────────────────────────────</code>\n"
            "This will:\n"
            "  • Close ALL open positions immediately\n"
            "  • Halt the bot entirely\n"
            "  • Require manual restart to resume\n\n"
            "To confirm, send:\n"
            "<code>/kill CONFIRM</code>\n\n"
            "To cancel, send anything else or just ignore."
        )

    # ── /kill CONFIRM ─────────────────────────────────────────────────────────

    def _cmd_kill_confirmed(self):
        self._send("⚡ <b>KILL SWITCH ACTIVATED — Closing all positions...</b>")
        logger.critical("KILL SWITCH activated via Telegram")
        try:
            # Use the full emergency_kill() from kill_switch.py — it builds fresh
            # exchange clients (bypassing any degraded in-process clients) and uses
            # the direction-aware close logic.  After it completes, halt the bot.
            from core.kill_switch import emergency_kill
            emergency_kill(self.cfg)
            self.risk_manager.halt_trading("Kill switch — Telegram command")
            self._send(
                "✅ <b>All positions closed. Bot halted.</b>\n"
                "<code>─────────────────────────</code>\n"
                "All open trades have been processed.\n"
                "Send /resume to re-enable trading (positions are closed, bot stays running).\n"
                "No PC restart needed."
            )
        except Exception as e:
            self._send(f"⚠ Kill switch error: {e}\n\n<b>CHECK EXCHANGE UIs AND CLOSE POSITIONS MANUALLY!</b>")
            logger.error(f"Kill switch error: {e}", exc_info=True)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _send(self, text: str):
        self.alert_manager.send_telegram(text)

    def _db_path(self) -> str:
        return str(Path(__file__).resolve().parent.parent / "data" / "bot.db")
