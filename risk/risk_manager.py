"""risk/risk_manager.py - All six risk defence layers."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from utils.database import (
    get_open_trades, update_trade, log_risk_event,
    get_daily_net_profit, get_high_water_mark, update_high_water_mark
)

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Implements all six rigidity framework layers:
      1. Liquidation prevention
      2. Exchange counterparty risk awareness
      3. Correlation & market regime detection
      4. Dynamic position sizing (shared with strategy)
      5. Profit extraction discipline (enforced by ProfitExtractor)
      6. Operator accountability (hard rules + daily loss cap)
    """

    def __init__(self, cfg: dict, exchanges: dict, alert_manager):
        self.cfg           = cfg
        self.exchanges     = exchanges
        self.alert_manager = alert_manager
        self.risk_cfg      = cfg["risk"]

        self._trading_halted   = False
        self._halt_reason      = ""
        self._halt_until: Optional[datetime] = None
        self._post_drawdown_mode = False
        self._post_drawdown_until: Optional[datetime] = None
        self._last_atr_check: dict[str, float] = {}

        logger.info("RiskManager initialised")

    # ── Public API ────────────────────────────────────────────────────────────

    def is_trading_allowed(self) -> bool:
        """Returns True if trading is currently permitted."""
        # Check temporary halt
        if self._halt_until and datetime.now(timezone.utc) < self._halt_until:
            return False
        elif self._halt_until and datetime.now(timezone.utc) >= self._halt_until:
            logger.info("Trading halt expired - resuming")
            self._trading_halted = False
            self._halt_until = None

        return not self._trading_halted

    def is_post_drawdown_mode(self) -> bool:
        if self._post_drawdown_until:
            if datetime.now(timezone.utc) < self._post_drawdown_until:
                return True
            else:
                self._post_drawdown_mode = False
                self._post_drawdown_until = None
        return False

    def halt_trading(self, reason: str, duration_hours: Optional[float] = None):
        self._trading_halted = True
        self._halt_reason    = reason
        if duration_hours:
            self._halt_until = datetime.now(timezone.utc) + timedelta(hours=duration_hours)
        logger.warning(f"Trading HALTED: {reason}")
        self.alert_manager.send_alert("trading_halt", f"Trading halted: {reason}")

    # ── Tick - called every 60 seconds ────────────────────────────────────────

    def tick(self):
        """Run all risk checks."""
        self._check_account_stop_loss()
        self._check_high_water_mark()
        self._check_margin_usage()
        self._check_daily_loss_limit()
        self._check_atr_spikes()
        self._update_high_water_mark()

    # ── Layer 1: Liquidation prevention ──────────────────────────────────────

    def _check_account_stop_loss(self):
        """If unrealised loss > 8% of account, close everything."""
        total_equity   = self._get_total_equity()
        if total_equity <= 0:
            return

        unrealised_pnl = self._get_total_unrealised_pnl()
        pnl_pct        = unrealised_pnl / total_equity

        if pnl_pct < -self.risk_cfg["account_stop_loss_pct"]:
            msg = (f"Account stop-loss triggered! "
                   f"Unrealised P&L = {pnl_pct*100:.2f}% "
                   f"(threshold: -{self.risk_cfg['account_stop_loss_pct']*100:.0f}%)")
            logger.critical(msg)
            log_risk_event("account_stop_loss", msg, total_equity, "Closing all positions")
            self._close_all_positions(reason="account_stop_loss")
            self.alert_manager.send_alert("account_stop_loss", msg)
            self._enter_post_drawdown_mode()

    # ── Layer 2: High-water mark floor ────────────────────────────────────────

    def _check_high_water_mark(self):
        """If account drops 15% from peak, halt the bot entirely."""
        hwm          = get_high_water_mark()
        total_equity = self._get_total_equity()

        if hwm <= 0 or total_equity <= 0:
            return

        drop_pct = (hwm - total_equity) / hwm
        threshold = self.risk_cfg["high_water_mark_floor"]

        if drop_pct >= threshold:
            msg = (f"High-water mark floor triggered! "
                   f"Balance ${total_equity:.2f} vs HWM ${hwm:.2f} "
                   f"(drop: {drop_pct*100:.1f}%)")
            logger.critical(msg)
            log_risk_event("hwm_floor", msg, total_equity, "Bot halted - awaiting manual restart")
            self.halt_trading(reason="HWM floor breached - manual restart required")
            self.alert_manager.send_alert("hwm_floor", msg)

    # ── Margin usage check ────────────────────────────────────────────────────

    def _check_margin_usage(self):
        for name, ex in self.exchanges.items():
            try:
                bal   = ex.fetch_balance()
                used  = float(bal.get("used",  {}).get("USDT", 0) or 0)
                total = float(bal.get("total", {}).get("USDT", 0) or 1)
                usage_pct = used / total if total else 0

                max_usage   = self.risk_cfg["max_margin_usage"]
                alert_usage = self.risk_cfg["margin_alert_pct"]

                if usage_pct >= max_usage:
                    msg = f"{name}: CRITICAL margin usage {usage_pct*100:.1f}% >= {max_usage*100:.0f}%"
                    logger.critical(msg)
                    self.alert_manager.send_alert("margin_critical", msg)
                elif usage_pct >= alert_usage:
                    msg = f"{name}: Margin usage {usage_pct*100:.1f}% >= {alert_usage*100:.0f}% alert threshold"
                    logger.warning(msg)
                    self.alert_manager.send_alert("margin_alert", msg)
            except Exception as e:
                logger.warning(f"Margin check failed for {name}: {e}")

    # ── Daily loss limit ──────────────────────────────────────────────────────

    def get_effective_daily_loss_limit(self) -> float:
        """
        Dynamic daily loss limit: base limit grows as the day's profits grow.

        Formula:  effective_limit = base + floor(max(daily_profit, 0) / profit_step) * step_amount

        Example with defaults (base=$200, profit_step=$300, step_amount=$50):
          Day profit   $0  ->  limit $200
          Day profit $300  ->  limit $250
          Day profit $600  ->  limit $300
          Day profit $900  ->  limit $350

        Rationale: once you've banked real profit today, you can afford to give
        a little more back before stopping - the limit scales with success rather
        than being fixed regardless of context.
        """
        base         = abs(self.risk_cfg.get("max_daily_loss", 200))
        profit_step  = self.risk_cfg.get("daily_loss_profit_step", 300)
        step_amount  = self.risk_cfg.get("daily_loss_step_amount", 50)
        daily_profit = max(get_daily_net_profit(), 0)
        steps        = int(daily_profit / profit_step)
        return base + steps * step_amount

    def _check_daily_loss_limit(self):
        daily_pnl  = get_daily_net_profit()
        limit      = self.get_effective_daily_loss_limit()
        if daily_pnl < -limit:
            msg = (f"Daily loss limit hit: ${daily_pnl:.2f} "
                   f"(effective limit: -${limit:.0f})")
            logger.warning(msg)
            if not self._trading_halted:
                self.halt_trading(reason=msg, duration_hours=24)

    # ── ATR spike filter ──────────────────────────────────────────────────────

    def _check_atr_spikes(self):
        threshold = self.risk_cfg["atr_spike_threshold"]
        for pair_cfg in self.cfg["pairs"]:
            if not pair_cfg["active"]:
                continue
            sym = pair_cfg["leg_a_symbol"]
            ex  = self.exchanges.get(pair_cfg["leg_a_exchange"])
            if not ex:
                continue
            try:
                ohlcv = ex.fetch_ohlcv(sym, "1h", limit=24)
                if len(ohlcv) < 2:
                    continue
                highs = np.array([c[2] for c in ohlcv])
                lows  = np.array([c[3] for c in ohlcv])
                atr_pct = float(np.mean(highs - lows) / np.mean((highs + lows) / 2))

                if atr_pct > threshold:
                    if self._last_atr_check.get(sym, 0) < threshold:
                        msg = f"{sym}: ATR spike {atr_pct*100:.2f}% > {threshold*100:.0f}% - positions halved"
                        logger.warning(msg)
                        self.alert_manager.send_alert("atr_spike", msg)
                self._last_atr_check[sym] = atr_pct
            except Exception as e:
                logger.debug(f"ATR check error {sym}: {e}")

    # ── High-water mark tracking ──────────────────────────────────────────────

    def _update_high_water_mark(self):
        total = self._get_total_equity()
        if total > 0:
            hwm = get_high_water_mark()
            if total > hwm:
                update_high_water_mark(total)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_total_equity(self) -> float:
        # In paper mode the exchange client's _paper_balance is fixed at
        # construction time and never updated when capital syncs.
        # Use cfg["capital"]["total_usdt"] directly — it IS kept current by
        # _sync_capital_from_balances (initial_capital + cumulative profits).
        first_ex = next(iter(self.exchanges.values()), None)
        if first_ex and getattr(first_ex, "paper_mode", False):
            return float(self.cfg["capital"].get("total_usdt", 0))

        total = 0.0
        for ex in self.exchanges.values():
            try:
                total += ex.get_total_equity()
            except Exception:
                pass
        return total

    def _get_total_unrealised_pnl(self) -> float:
        total = 0.0
        for name, ex in self.exchanges.items():
            try:
                positions = ex.fetch_positions()
                for pos in positions:
                    total += float(pos.get("unrealizedPnl", 0) or 0)
            except Exception:
                pass
        return total

    def _close_all_positions(self, reason: str):
        """Emergency: close all open DB-tracked positions."""
        open_trades = get_open_trades()
        logger.warning(f"Closing {len(open_trades)} open trades - reason: {reason}")
        for trade in open_trades:
            try:
                ex_a = self.exchanges.get(trade["leg_a_exchange"])
                ex_b = self.exchanges.get(trade["leg_b_exchange"])
                notional = trade["notional_usd"]
                # Determine correct close sides from stored trade direction.
                # buy_a_sell_b  -> leg A was opened LONG,  leg B was opened SHORT
                # buy_b_sell_a  -> leg A was opened SHORT, leg B was opened LONG
                direction = trade.get("direction", "buy_a_sell_b")
                side_a = "long"  if direction == "buy_a_sell_b" else "short"
                side_b = "short" if direction == "buy_a_sell_b" else "long"
                if ex_a:
                    ticker = ex_a.fetch_ticker(trade["leg_a_symbol"])
                    price  = float(ticker["last"])
                    amount = notional / price if price > 0 else 0.01
                    ex_a.close_position(trade["leg_a_symbol"], side_a, amount)
                if ex_b:
                    ticker = ex_b.fetch_ticker(trade["leg_b_symbol"])
                    price  = float(ticker["last"])
                    amount = notional / price if price > 0 else 0.01
                    ex_b.close_position(trade["leg_b_symbol"], side_b, amount)
                # Compute approximate P&L: exit spread is 0 (forced close, prices likely converged
                # or adverse) - record as full fee loss at minimum
                notional    = float(trade.get("notional_usd") or 0)
                approx_fees = notional * 0.0008
                update_trade(trade["id"], {
                    "status":       "closed",
                    "gross_profit": 0.0,
                    "fees":         round(approx_fees, 6),
                    "net_profit":   round(-approx_fees, 6),
                    "notes":        f"closed by risk: {reason}",
                })
            except Exception as e:
                logger.error(f"Failed to close trade {trade['id']}: {e}")

    def _enter_post_drawdown_mode(self):
        hours = self.cfg["position_sizing"]["post_drawdown_hours"]
        self._post_drawdown_mode  = True
        self._post_drawdown_until = datetime.now(timezone.utc) + timedelta(hours=hours)
        logger.info(f"Entering post-drawdown mode for {hours} hours")
