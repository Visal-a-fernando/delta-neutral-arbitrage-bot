"""strategies/spread_capture.py — Delta-neutral spread capture + funding harvest."""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

# Funding settles at 00:00, 08:00, 16:00 UTC on Binance & Bybit.
# Spreads widen in the 30-min window before each settlement as traders
# hedge positions — lower the entry threshold to catch those opportunities.
_FUNDING_SETTLEMENT_HOURS_UTC = (0, 8, 16)
_FUNDING_WINDOW_DEFAULT_MIN   = 30   # minutes each side of settlement

from utils.database import log_trade, update_trade, get_open_trades, get_daily_net_profit
from utils.exchange import ExchangeClient

logger = logging.getLogger(__name__)


class SpreadCaptureStrategy:
    """
    Core strategy: simultaneously long Asset A on Exchange 1 and short the same
    asset on Exchange 2. Captures the spread (price divergence between venues).
    Also harvests funding rates when favourable.
    """

    # Round-trip taker fees per exchange (entry + close), for orphan accounting.
    # When an orphan closes, fees were paid on entry AND will be on close.
    _TAKER_FEES = {"binance": 0.0004, "bybit": 0.00055}

    def _is_pair_disabled(self, pair: str) -> bool:
        """True if this pair is currently paused by the circuit breaker."""
        until = self._disabled_pairs.get(pair)
        if until is None:
            return False
        if datetime.now(timezone.utc) >= until:
            self._disabled_pairs.pop(pair, None)
            logger.info(f"CIRCUIT BREAKER: {pair} pause expired — re-enabling.")
            return False
        return True

    def _record_orphan_event(self, pair: str):
        """Record an orphan/imbalance event. If too many in window, disable pair."""
        now = datetime.now(timezone.utc)
        history = self._orphan_history.setdefault(pair, [])
        history.append(now)
        # Prune events older than window
        cutoff = now - timedelta(minutes=self._cb_window_min)
        history[:] = [t for t in history if t >= cutoff]
        if len(history) >= self._cb_max_orphans and pair not in self._disabled_pairs:
            until = now + timedelta(minutes=self._cb_pause_min)
            self._disabled_pairs[pair] = until
            logger.critical(
                f"🚨 CIRCUIT BREAKER TRIPPED: {pair} — {len(history)} orphan events "
                f"in last {self._cb_window_min:.0f} min. Disabled until "
                f"{until.strftime('%H:%M:%S UTC')} ({self._cb_pause_min:.0f} min)."
            )
            try:
                self.alert_manager.send_telegram(
                    f"<b>🚨 CIRCUIT BREAKER — {pair}</b>\n"
                    f"{len(history)} orphan events in {self._cb_window_min:.0f} min.\n"
                    f"Pair paused for {self._cb_pause_min:.0f} min "
                    f"(until {until.strftime('%H:%M UTC')})."
                )
            except Exception:
                pass

    def _log_orphan_trade(self, pair_label, ex_name, sym, side, contracts,
                          entry_price, mark_price, leg_label="A"):
        """Record an orphan close to the trades table so /snapshot reflects truth.

        Estimates net = (mark - entry) * contracts * (sign by side) - round-trip fees.
        Net for short positions is inverted.
        """
        try:
            from utils.database import log_trade
            try:    contracts_f = abs(float(contracts))
            except: contracts_f = 0.0
            try:    entry_f = float(entry_price or 0)
            except: entry_f = 0.0
            try:    mark_f  = float(mark_price or 0)
            except: mark_f  = 0.0
            notional = contracts_f * mark_f
            # Directional gross: long profits when mark>entry, short the inverse.
            side_lc = (str(side) or "").lower()
            sign = 1.0 if side_lc in ("long", "buy") else -1.0
            gross = (mark_f - entry_f) * contracts_f * sign
            fee_rate = self._TAKER_FEES.get(ex_name, 0.0005)
            fees = notional * fee_rate * 2  # entry + close
            net  = gross - fees
            log_trade({
                "timestamp":      datetime.now(timezone.utc).isoformat(),
                "pair":           f"ORPHAN_{ex_name}_{sym}",
                "leg_a_exchange": ex_name,
                "leg_b_exchange": None,
                "leg_a_symbol":   sym,
                "leg_b_symbol":   None,
                "notional_usd":   notional,
                "spread_pct":     0.0,
                "gross_profit":   gross,
                "fees":           fees,
                "net_profit":     net,
                "funding_income": 0.0,
                "status":         "closed",
                "paper_mode":     0 if not self.paper_mode else 1,
                "amount_a":       contracts_f,
                "amount_b":       0.0,
                "entry_price_a":  entry_f,
                "entry_price_b":  0.0,
                "direction":      side_lc,
                "notes":          f"orphan_close_{leg_label}_{pair_label}",
            })
        except Exception as e:
            logger.warning(f"Failed to log synthetic orphan trade: {e}")

    def __init__(self, cfg: dict, exchanges: dict, risk_manager, alert_manager, paper_mode: bool):
        self.cfg           = cfg
        self.exchanges     = exchanges
        self.risk_manager  = risk_manager
        self.alert_manager = alert_manager
        self.paper_mode    = paper_mode

        self.s_cfg     = cfg["strategy"]
        self.ps_cfg    = cfg["position_sizing"]
        self.risk_cfg  = cfg["risk"]
        self.pairs_cfg = [p for p in cfg["pairs"] if p["active"]]

        # Use lower thresholds in paper mode so mechanics can be validated
        # without waiting for rare 0.3% spreads on liquid pairs
        if paper_mode:
            self._min_spread_entry = self.s_cfg.get("paper_min_spread_entry",
                                                     self.s_cfg["min_spread_entry"])
            self._min_spread_exit  = self.s_cfg.get("paper_min_spread_exit",
                                                     self.s_cfg["min_spread_exit"])
        else:
            self._min_spread_entry = self.s_cfg["min_spread_entry"]
            self._min_spread_exit  = self.s_cfg["min_spread_exit"]

        self._open_positions: dict[str, dict] = {}
        self._last_trade_time: Optional[datetime] = None
        self._atr_cache: dict[str, tuple] = {}  # sym -> (atr_pct, fetched_at)
        self._last_close_time: dict[str, datetime] = {}  # pair -> UTC close time (re-entry cooldown)

        # ── Circuit breaker for orphan-prone pairs ────────────────────────────
        # If a pair generates N orphans within `window_min`, disable it for `pause_min`.
        # Prevents bleeding from pairs whose execution structure has degraded
        # (e.g. one exchange consistently failing to fill in current regime).
        cb_cfg = self.s_cfg.get("circuit_breaker", {})
        self._cb_max_orphans = int(cb_cfg.get("max_orphans", 3))
        self._cb_window_min  = float(cb_cfg.get("window_min", 30))
        self._cb_pause_min   = float(cb_cfg.get("pause_min",  60))
        self._orphan_history: dict[str, list[datetime]] = {}    # pair -> [event timestamps]
        self._disabled_pairs: dict[str, datetime] = {}          # pair -> disabled_until (UTC)

        # ── Adaptive per-pair thresholds ──────────────────────────────────────
        # Each pair tracks its own effective entry threshold.
        # Losing trade → threshold rises (need wider spread to enter).
        # Profitable trade → threshold falls back toward the global floor.
        # This allows ALL pairs to stay active — bad pairs self-throttle instead
        # of being manually disabled.
        self._pair_thresholds: dict[str, float] = {}   # pair -> current threshold
        self._pair_threshold_updated_at: dict[str, datetime] = {}  # pair -> last update time (UTC)

        self._funding_window_min = self.s_cfg.get(
            "funding_window_minutes", _FUNDING_WINDOW_DEFAULT_MIN
        )

        # Lock to prevent any concurrent position entry (defence in depth on top
        # of APScheduler's max_instances=1 — also guards against future threading changes).
        self._entry_lock = threading.Lock()

        # Rehydrate from DB on restart
        self._rehydrate_open_positions()
        self._rehydrate_pair_thresholds()
        logger.info(f"SpreadCaptureStrategy ready with {len(self.pairs_cfg)} active pairs")

    def _rehydrate_open_positions(self):
        for trade in get_open_trades():
            try:
                pair = trade["pair"]
                if pair not in self._open_positions:
                    direction = trade.get("direction") or "buy_a_sell_b"
                    # Use explicit None checks so that a legitimate 0.0 value (legacy
                    # trade with missing amounts) falls back to 0.0 — NOT to notional_usd
                    # (which is in USD dollars, not contract units).  The close path
                    # already handles amount==0 by recomputing from notional/exit_price.
                    amt_a = trade.get("amount_a")
                    amt_b = trade.get("amount_b")
                    self._open_positions[pair] = {
                        "trade_id":     trade["id"],
                        "entry_price_a": trade.get("entry_price_a") or 0.0,
                        "entry_price_b": trade.get("entry_price_b") or 0.0,
                        "amount_a":     amt_a if (amt_a is not None and amt_a > 0) else 0.0,
                        "amount_b":     amt_b if (amt_b is not None and amt_b > 0) else 0.0,
                        "order_id_a":   None,
                        "order_id_b":   None,
                        "direction":    direction,
                        "close_side_a": "long"  if direction == "buy_a_sell_b" else "short",
                        "close_side_b": "short" if direction == "buy_a_sell_b" else "long",
                        # For orphan detector — exchange/symbol info from rehydrated DB row
                        "leg_a_exchange": trade.get("leg_a_exchange"),
                        "leg_a_symbol":   trade.get("leg_a_symbol"),
                        "leg_b_exchange": trade.get("leg_b_exchange"),
                        "leg_b_symbol":   trade.get("leg_b_symbol"),
                    }
            except Exception as e:
                logger.warning(f"Could not rehydrate trade {trade.get('id')}: {e}")
        if self._open_positions:
            logger.info(f"Rehydrated {len(self._open_positions)} open positions from DB")

    def _rehydrate_pair_thresholds(self):
        """
        On restart, replay the last N closed trades per pair to reconstruct
        each pair's adaptive threshold so it continues from where it left off,
        not from a cold start that ignores recent losing streaks.
        """
        adaptive = self.s_cfg.get("adaptive_spread", {})
        if not adaptive.get("enabled", True):
            return

        lookback = adaptive.get("lookback", 10)
        floor    = adaptive.get("floor",    self._min_spread_entry)
        ceiling  = adaptive.get("ceiling",  0.0025)
        step_up  = adaptive.get("step_up",  0.0010)
        step_dn  = adaptive.get("step_down",0.0005)

        try:
            from utils.database import _conn
            max_notional = self.s_cfg.get("max_notional_per_trade", 3000)
            with _conn() as con:
                # Only replay trades placed under the CURRENT position sizing rules
                # (notional <= max_notional_per_trade * 1.1).
                # This excludes pre-fix trades that had $12,138 notional — those losses
                # were caused by the old sizing bug and must not penalise pairs going forward.
                rows = con.execute("""
                    SELECT pair, net_profit FROM (
                        SELECT pair, net_profit, ROW_NUMBER() OVER (
                            PARTITION BY pair ORDER BY id DESC
                        ) AS rn FROM trades
                        WHERE status='closed'
                          AND notional_usd <= ?
                    ) WHERE rn <= ?
                    ORDER BY pair, rn DESC
                """, (max_notional * 1.1, lookback,)).fetchall()
        except Exception as e:
            logger.warning(f"Could not rehydrate adaptive thresholds: {e}")
            return

        # Replay threshold adjustments for each pair
        pair_trades: dict[str, list[float]] = {}
        for row in rows:
            pair_trades.setdefault(row["pair"], []).append(row["net_profit"] or 0.0)

        for pair, profits in pair_trades.items():
            t = floor
            for net in profits:   # oldest first
                if net < 0:
                    t = min(t + step_up, ceiling)
                else:
                    t = max(t - step_dn, floor)
            self._pair_thresholds[pair] = t
            self._pair_threshold_updated_at[pair] = datetime.now(timezone.utc)

        if self._pair_thresholds:
            lines = [f"  {p}: {v*100:.3f}%" for p, v in sorted(self._pair_thresholds.items())
                     if v > floor]
            if lines:
                logger.info("Adaptive thresholds rehydrated (above floor):\n" + "\n".join(lines))

    def tick(self):
        self._apply_time_decay_to_thresholds()
        self._manage_open_positions()
        self._scan_for_entries()
        self._scan_funding_harvest()
        self._check_inactivity()

    def _apply_time_decay_to_thresholds(self):
        """
        Apply time decay to pair thresholds: gradually lower thresholds for pairs
        that haven't traded recently, encouraging re-entry as market conditions improve.

        Decay rate: configurable via decay_per_hour (default 0.01%/h).
        Bounds: stays at or above floor — never decays below global min_spread_entry.
        """
        adaptive = self.s_cfg.get("adaptive_spread", {})
        if not adaptive.get("enabled", True):
            return

        floor = adaptive.get("floor", self._min_spread_entry)
        decay_per_hour = adaptive.get("decay_per_hour", 0.0001)  # 0.01% per hour default

        now = datetime.now(timezone.utc)
        for pair in list(self._pair_thresholds.keys()):
            last_update = self._pair_threshold_updated_at.get(pair)
            if last_update is None:
                continue

            hours_elapsed = (now - last_update).total_seconds() / 3600
            if hours_elapsed <= 0:
                continue

            old_threshold = self._pair_thresholds[pair]
            decay_amount = decay_per_hour * hours_elapsed
            new_threshold = max(old_threshold - decay_amount, floor)

            if new_threshold < old_threshold:
                self._pair_thresholds[pair] = new_threshold
                self._pair_threshold_updated_at[pair] = now
                logger.debug(f"TIME DECAY {pair}: {old_threshold*100:.3f}% -> {new_threshold*100:.3f}% "
                             f"({hours_elapsed:.1f}h elapsed)")

    def _manage_open_positions(self):
        max_hold_h     = self.s_cfg.get("max_hold_hours", 12)
        trail_buffer   = self.s_cfg.get("trailing_exit_buffer", 0.0002)

        for trade in get_open_trades():
            pair = trade["pair"]
            try:
                spread = self._get_current_spread(trade)
                if spread is None:
                    continue

                # ── Track trailing minimum spread ──────────────────────────
                pos_data = self._open_positions.get(pair, {})
                min_seen = min(pos_data.get("min_spread_seen", spread), spread)
                if pair in self._open_positions:
                    self._open_positions[pair]["min_spread_seen"] = min_seen

                # ── Max hold time — free up capital after 12h ──────────────
                try:
                    trade_ts = datetime.fromisoformat(trade["timestamp"])
                    if trade_ts.tzinfo is None:
                        trade_ts = trade_ts.replace(tzinfo=timezone.utc)
                    hold_h = (datetime.now(timezone.utc) - trade_ts).total_seconds() / 3600
                except Exception:
                    hold_h = 0
                if hold_h >= max_hold_h:
                    logger.info(f"MAX HOLD {pair}: {hold_h:.1f}h — closing to free capital")
                    self._close_position(trade, spread, reason="max_hold")
                    continue

                # ── Exit conditions ────────────────────────────────────────
                # CRITICAL FIX 2026-05-07: exit must be measured in REALIZED P&L
                # (round-trip return), not absolute exit-spread.
                #
                # The exit metric (bid_a-ask_b)/ask_b is structurally negative at
                # entry time due to bid-ask spread. Comparing it directly to a
                # small positive threshold (0.01%) made `fixed_exit` true from
                # the very first tick, causing the bot to exit on the FIRST
                # management cycle regardless of actual price movement.
                #
                # Realized round-trip = entry_spread + current_exit_spread.
                # That's the actual % return we'd realize closing now (excl. fees).
                # We want this to be at or near 0 (full convergence captured).
                entry_spread = trade.get("spread_pct") or 0.0
                realized_rt  = entry_spread + spread   # round-trip return % (incl bid-ask cost)

                # Min profit target: capture at least this fraction of entry spread
                # before exiting. 0.5 = "exit when half the entry spread is realized".
                profit_capture_ratio = self.s_cfg.get("profit_capture_ratio", 0.5)
                profit_target  = entry_spread * profit_capture_ratio

                # Stop loss: round-trip has gone deeply negative (price moved against us)
                stop_loss_rt   = self.s_cfg.get("realized_stop_loss", -0.0040)

                fixed_exit = realized_rt >= profit_target or realized_rt <= stop_loss_rt

                # Trailing exit: realized hit profit then partially gave back
                # min_seen here represents the WORST exit_spread observed; we want
                # the BEST realized round-trip we've seen instead.
                max_realized = max(pos_data.get("max_realized", realized_rt), realized_rt)
                if pair in self._open_positions:
                    self._open_positions[pair]["max_realized"] = max_realized

                trailing_exit = (
                    max_realized >= profit_target * 0.7               # got close to target
                    and (max_realized - realized_rt) >= trail_buffer   # gave some back
                )

                if fixed_exit or trailing_exit:
                    if self._should_hold_for_funding(trade):
                        logger.info(
                            f"HOLD {pair}: spread ready to exit but funding "
                            f"settlement <15min away — holding for payment"
                        )
                        continue
                    if realized_rt <= stop_loss_rt:
                        reason = "stop_loss"
                    elif trailing_exit:
                        reason = "trailing_exit"
                    else:
                        reason = "profit_target"
                    logger.info(
                        f"EXIT {pair}: {reason} entry={entry_spread*100:.3f}% "
                        f"exit_metric={spread*100:.3f}% "
                        f"realized_rt={realized_rt*100:.3f}% "
                        f"target={profit_target*100:.3f}% "
                        f"max_seen={max_realized*100:.3f}%"
                    )
                    self._close_position(trade, spread, reason=reason)
                    continue

                # Funding rate exit — only fires when we're CLOSE to a settlement.
                # Funding only costs us at the 8h settlement boundaries (00/08/16 UTC).
                # If next settlement is far away, adverse funding now is irrelevant —
                # the trade will likely close on profit_target/stop_loss long before.
                # Old logic exited immediately on any adverse rate, generating
                # 1-second-loop trades that lost only to friction.
                ex_a = self.exchanges.get(trade["leg_a_exchange"])
                funding_exit_window_min = self.s_cfg.get("funding_exit_window_min", 30)
                now_utc = datetime.now(timezone.utc)
                # Minutes until next 00/08/16 UTC settlement
                hour = now_utc.hour
                next_settlement_hour = next((h for h in (0, 8, 16, 24) if h > hour), 24)
                mins_to_settlement = (next_settlement_hour - hour) * 60 - now_utc.minute
                if (ex_a
                    and mins_to_settlement <= funding_exit_window_min
                    and self._is_funding_rate_adverse(ex_a, trade["leg_a_symbol"])):
                    logger.info(
                        f"EXIT {pair}: adverse funding within {mins_to_settlement}min "
                        f"of settlement — closing to avoid charge"
                    )
                    self._close_position(trade, spread, reason="adverse_funding_rate")
                    continue

                unrealised = self._estimate_unrealised_pnl_pct(trade)
                if unrealised < -self.risk_cfg["account_stop_loss_pct"]:
                    logger.warning(f"STOP LOSS {pair}: unrealised {unrealised*100:.2f}%")
                    self._close_position(trade, spread, reason="position_stop_loss")
            except Exception as e:
                logger.error(f"Error managing {pair}: {e}", exc_info=True)

    def _scan_for_entries(self):
        if self._in_news_blackout():
            return

        max_pos    = self.s_cfg["max_simultaneous_positions"]
        open_count = len(get_open_trades())
        if open_count >= max_pos:
            return

        cooldown_s = self.s_cfg.get("reentry_cooldown_seconds", 120)

        for pair_cfg in self.pairs_cfg:
            pair = f"{pair_cfg['leg_a_symbol']}_{pair_cfg['leg_b_symbol']}"

            if pair in self._open_positions:
                continue
            if open_count >= max_pos:
                break
            # Circuit breaker: skip pairs paused after too many orphans
            if self._is_pair_disabled(pair):
                continue

            # Re-entry cooldown: prevent immediately re-entering a pair after closing it.
            # Stops the bot from spam-trading at identical prices when the spread is noisy.
            last_close = self._last_close_time.get(pair)
            if last_close:
                secs_since = (datetime.now(timezone.utc) - last_close).total_seconds()
                if secs_since < cooldown_s:
                    logger.debug(f"COOLDOWN {pair}: {secs_since:.0f}s since last close (need {cooldown_s}s)")
                    continue

            try:
                signal = self._evaluate_entry(pair_cfg)
                if signal:
                    # Pre-entry funding check: skip if adverse funding is imminent.
                    # Without this, the bot opens then immediately closes the trade
                    # on adverse_funding_rate exit = pure friction loss.
                    funding_exit_window_min = self.s_cfg.get("funding_exit_window_min", 30)
                    now_utc = datetime.now(timezone.utc)
                    next_settlement_hour = next((h for h in (0, 8, 16, 24) if h > now_utc.hour), 24)
                    mins_to_settlement = (next_settlement_hour - now_utc.hour) * 60 - now_utc.minute
                    if mins_to_settlement <= funding_exit_window_min:
                        ex_a_check = self.exchanges.get(pair_cfg["leg_a_exchange"])
                        if ex_a_check and self._is_funding_rate_adverse(ex_a_check, pair_cfg["leg_a_symbol"]):
                            logger.info(
                                f"SKIP {pair}: adverse funding within {mins_to_settlement}min "
                                f"of settlement — would exit immediately"
                            )
                            continue
                    self._enter_position(pair_cfg, signal)
                    open_count += 1
                    self._last_trade_time = datetime.now(timezone.utc)
            except Exception as e:
                logger.error(f"Error evaluating {pair}: {e}", exc_info=True)

    def _evaluate_entry(self, pair_cfg: dict) -> Optional[dict]:
        ex_a: ExchangeClient = self.exchanges[pair_cfg["leg_a_exchange"]]
        ex_b: ExchangeClient = self.exchanges[pair_cfg["leg_b_exchange"]]
        sym_a = pair_cfg["leg_a_symbol"]
        sym_b = pair_cfg["leg_b_symbol"]

        ticker_a = ex_a.fetch_ticker(sym_a)
        ticker_b = ex_b.fetch_ticker(sym_b)

        # Use executable prices — fall back to last if bid/ask not available (paper mode).
        ask_a = float(ticker_a.get("ask") or ticker_a["last"])
        bid_a = float(ticker_a.get("bid") or ticker_a["last"])
        ask_b = float(ticker_b.get("ask") or ticker_b["last"])
        bid_b = float(ticker_b.get("bid") or ticker_b["last"])

        if min(ask_a, bid_a, ask_b, bid_b) <= 0:
            return None

        # Check BOTH directions — pick whichever gives a positive executable spread.
        # Direction 1: buy on ex_a (pay ask_a), sell on ex_b (receive bid_b)
        spread_ab = (bid_b - ask_a) / ask_a
        # Direction 2: buy on ex_b (pay ask_b), sell on ex_a (receive bid_a)
        spread_ba = (bid_a - ask_b) / ask_b

        if spread_ab >= spread_ba:
            spread    = spread_ab
            direction = "buy_a_sell_b"
            price_a, price_b = ask_a, bid_b
        else:
            spread    = spread_ba
            direction = "buy_b_sell_a"
            price_a, price_b = ask_b, bid_a

        # ── Determine effective entry threshold ──────────────────────────────
        # Priority order:
        #   1. Adaptive per-pair threshold (self-tuned from live results)
        #   2. Per-pair config override (e.g. MEXC min_spread_entry: 0.0010)
        #   3. Global min_spread_entry from strategy config
        # During funding windows, threshold is halved regardless of source.
        pair        = f"{sym_a}_{sym_b}"
        pair_static = pair_cfg.get("min_spread_entry", self._min_spread_entry)
        adaptive_t  = self._pair_thresholds.get(pair, pair_static)
        # Adaptive threshold can only raise above the static floor, never below it
        effective_threshold = max(adaptive_t, pair_static)

        in_funding_window = self._is_funding_window()
        # Funding-window halving DISABLED 2026-05-03 in both paper and live modes.
        # Analysis of 772 sub-0.18% paper trades showed avg -$0.52/trade, requiring
        # 0.022% funding income to break even. Real funding-rate divergence between
        # Binance/Bybit averages 0.005-0.015% (below break-even). Live slippage at
        # low spreads adds 0.05-0.20% per trade, exceeding any funding income.
        # Net: feature is unprofitable in both modes.
        # if in_funding_window:
        #     effective_threshold *= 0.5

        if spread < effective_threshold:
            adaptive_cfg = self.s_cfg.get("adaptive_spread", {})
            ceiling = adaptive_cfg.get("ceiling", 0.0025)
            throttled = adaptive_t >= ceiling * 0.95   # within 5% of ceiling = effectively paused
            logger.info(
                f"SCAN {sym_a}: spread {spread*100:.4f}% "
                f"(need >={effective_threshold*100:.3f}%"
                f"{' FUNDING WINDOW' if in_funding_window else ''}"
                f"{' [THROTTLED BY LOSSES]' if throttled else ''})"
            )
            return None

        correlation = self._get_rolling_correlation(ex_a, ex_b, sym_a, sym_b)
        # Use `not >= ` rather than `<` so NaN values are treated as failing the check.
        # In Python/NumPy: NaN < 0.85 evaluates to False (NaN comparisons always False),
        # which would silently allow a trade with undefined correlation.
        if correlation is not None and not (correlation >= self.ps_cfg["correlation_min"]):
            logger.info(f"{sym_a}: correlation {correlation} too low or NaN — skipping")
            return None

        atr_pct    = self._get_atr_pct(ex_a, sym_a)
        atr_halved = atr_pct is not None and atr_pct > self.risk_cfg["atr_spike_threshold"]
        if atr_halved:
            logger.info(f"{sym_a}: ATR spike {atr_pct*100:.2f}% — position size halved")

        volume_ok  = self._check_volume(ticker_a)
        deploy_pct = self._get_deploy_pct(spread, volume_ok)
        if deploy_pct == 0:
            return None

        # Post-drawdown: cap at 40%
        if self.risk_manager.is_post_drawdown_mode():
            deploy_pct = min(deploy_pct, self.ps_cfg["post_drawdown_deploy"])

        # ATR spike: halve size
        if atr_halved:
            deploy_pct *= 0.50

        funding_income = self._get_funding_income(ex_a, sym_a)

        lev_cfg  = self.cfg["leverage"]
        if spread >= lev_cfg["high_spread_threshold"]:
            leverage = lev_cfg["maximum"]                          # 10x at >= 0.30%
        elif spread >= lev_cfg.get("medium_threshold", 0.0015):
            leverage = lev_cfg.get("medium_leverage", 8)           # 8x at 0.15–0.30%
        else:
            leverage = lev_cfg["default"]                          # 7x below 0.15%

        total_capital    = self.cfg["capital"]["total_usdt"]
        reserve          = self.cfg["capital"]["stablecoin_reserve"]
        free_buffer      = self.cfg["capital"]["free_margin_buffer"]
        tradeable        = (total_capital - reserve) * (1 - free_buffer)
        open_count       = len(get_open_trades())
        # Divide available capital among currently open positions plus this new one.
        # Use at least 4 as the divisor so a single lonely position never gets more
        # than 25% of tradeable capital — prevents the "$12k notional on a $4k account"
        # problem when concurrent = 1 at the start of the day.
        concurrent       = max(min(open_count + 1, self.s_cfg["max_simultaneous_positions"]), 4)
        notional         = tradeable * leverage * deploy_pct / concurrent

        # Hard cap: no single trade may exceed max_notional_per_trade regardless of sizing.
        max_notional = self.s_cfg.get("max_notional_per_trade", 3000)
        notional     = min(notional, max_notional)

        return {
            "spread": spread, "notional": notional, "leverage": leverage,
            "deploy_pct": deploy_pct, "funding_income": funding_income,
            "price_a": price_a, "price_b": price_b, "atr_halved": atr_halved,
            "direction": direction,   # "buy_a_sell_b" or "buy_b_sell_a"
        }

    # ── Fill timeout helper ───────────────────────────────────────────────────

    def _wait_for_fill(self, exchange, order_id: str, symbol: str,
                       timeout_s: int = None) -> bool:
        """
        Poll order status every second until filled or timeout.
        Returns True if filled, False if timed out or cancelled.
        Skipped entirely in paper mode (paper orders always fill instantly).
        """
        if timeout_s is None:
            timeout_s = self.s_cfg.get("order_fill_timeout_seconds", 10)
        if self.paper_mode:
            return True
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                order = exchange.fetch_order(order_id, symbol)
                status = order.get("status", "")
                filled = float(order.get("filled") or 0)
                amount = float(order.get("amount") or 1)
                if status == "closed" or filled >= amount * 0.99:
                    return True
                if status in ("cancelled", "rejected", "expired"):
                    logger.warning(f"Order {order_id} on {exchange.name} status={status}")
                    return False
            except Exception as e:
                logger.debug(f"fill-check {order_id}: {e}")
            time.sleep(1)
        return False   # timed out

    def _place_limit_single(self, exchange, symbol, side, amount, price, timeout_s=8):
        """
        Place a single limit order, wait for fill or timeout. NO re-quoting.
        Simple, predictable, no margin-race risk from cancel-replace cycles.
        Returns (filled, order_id, fill_price).
        """
        if self.paper_mode:
            try:
                order = exchange.place_order(symbol, side, amount, order_type="limit", price=price)
                return True, order.get("id"), price
            except Exception as e:
                logger.warning(f"paper place_limit failed: {e}")
                return False, None, None

        try:
            order    = exchange.place_order(symbol, side, amount, order_type="limit", price=price)
            order_id = order.get("id")
        except Exception as e:
            logger.warning(f"place_limit failed on {exchange.name}: {e}")
            return False, None, None

        # Poll for fill until deadline
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                status = exchange.fetch_order(order_id, symbol)
                filled = float(status.get("filled") or 0)
                amt    = float(status.get("amount") or amount)
                if status.get("status") == "closed" or filled >= amt * 0.99:
                    avg = float(status.get("average") or status.get("price") or price)
                    return True, order_id, avg
                if status.get("status") in ("cancelled", "rejected", "expired"):
                    return False, order_id, None
            except Exception:
                # Bybit may reject fetch_order — keep waiting silently
                pass
            time.sleep(0.4)

        # Timeout: cancel
        try: exchange.cancel_order(order_id, symbol)
        except Exception: pass
        return False, order_id, None

    def _place_market_single(self, exchange, symbol, side, amount, ref_price):
        """
        Place a single market order. Eliminates the cancel-fill race vector at
        the cost of taker fees on both legs. ref_price is used as a fallback
        fill price if the exchange doesn't return one.
        """
        if self.paper_mode:
            try:
                order = exchange.place_order(symbol, side, amount, order_type="market")
                return True, order.get("id"), ref_price
            except Exception as e:
                logger.warning(f"paper place_market failed: {e}")
                return False, None, None
        try:
            order = exchange.place_order(symbol, side, amount, order_type="market")
            order_id = order.get("id")
            avg = float(order.get("average") or order.get("price") or ref_price or 0)
            # Some exchanges return market orders as already-closed; others need a poll.
            time.sleep(0.3)
            try:
                status = exchange.fetch_order(order_id, symbol)
                filled = float(status.get("filled") or 0)
                amt    = float(status.get("amount") or amount)
                if filled >= amt * 0.95:
                    avg = float(status.get("average") or avg or ref_price)
                    return True, order_id, avg
                # Even if status is unclear, market orders almost always fill —
                # let the verify-actual-position path downstream confirm.
                return True, order_id, avg or ref_price
            except Exception:
                return True, order_id, avg or ref_price
        except Exception as e:
            logger.warning(f"place_market failed on {exchange.name}: {e}")
            return False, None, None

    def _place_both_legs_parallel(self, ex_a, sym_a, side_a, amount_a, price_a,
                                   ex_b, sym_b, side_b, amount_b, price_b,
                                   timeout_s=8, order_type="limit"):
        """
        Place both legs in parallel using threads. Single shot each (no re-quote).
        Caller pre-computes crossed prices and passes them.
        Returns ((a_filled, oid_a, fp_a), (b_filled, oid_b, fp_b)).
        """
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as exe:
            if order_type == "market":
                f_a = exe.submit(self._place_market_single, ex_a, sym_a, side_a, amount_a, price_a)
                f_b = exe.submit(self._place_market_single, ex_b, sym_b, side_b, amount_b, price_b)
            else:
                f_a = exe.submit(self._place_limit_single, ex_a, sym_a, side_a, amount_a, price_a, timeout_s)
                f_b = exe.submit(self._place_limit_single, ex_b, sym_b, side_b, amount_b, price_b, timeout_s)
            return f_a.result(), f_b.result()

    def _enter_position(self, pair_cfg: dict, signal: dict):
        sym_a = pair_cfg["leg_a_symbol"]
        sym_b = pair_cfg["leg_b_symbol"]
        ex_a  = self.exchanges[pair_cfg["leg_a_exchange"]]
        ex_b  = self.exchanges[pair_cfg["leg_b_exchange"]]
        pair  = f"{sym_a}_{sym_b}"

        notional  = signal["notional"]
        direction = signal.get("direction", "buy_a_sell_b")

        # Guard: never place an order with zero, negative, or NaN sizing.
        # This can happen if capital falls to zero or prices are unavailable.
        if not (notional > 0 and signal["price_a"] > 0 and signal["price_b"] > 0):
            logger.warning(
                f"{pair}: Refusing entry - invalid signal values "
                f"notional={notional:.2f} price_a={signal['price_a']} price_b={signal['price_b']}"
            )
            return

        # Race-condition guard: atomically check limits and pre-claim the slot.
        # We pre-reserve the pair in _open_positions BEFORE any network call so
        # a second concurrent evaluation (e.g. from a re-entrant tick or future
        # threading change) can see it and bail out.  The slot is released if
        # entry fails below.  Network I/O runs OUTSIDE the lock.
        with self._entry_lock:
            current_open = len(get_open_trades())
            max_pos      = self.s_cfg["max_simultaneous_positions"]
            if current_open >= max_pos:
                logger.info(f"{pair}: Entry aborted - {current_open}/{max_pos} positions already open (checked under lock)")
                return
            if pair in self._open_positions:
                logger.info(f"{pair}: Entry aborted - position already tracked in memory")
                return
            # Pre-claim the slot so any concurrent evaluator AND the orphan detector
            # sees it immediately (prevents orphan detector from closing in-flight entries)
            self._open_positions[pair] = {
                "trade_id": None,
                "_claiming": True,
                "leg_a_exchange": pair_cfg["leg_a_exchange"],
                "leg_a_symbol":   sym_a,
                "leg_b_exchange": pair_cfg["leg_b_exchange"],
                "leg_b_symbol":   sym_b,
            }

        amount_a = notional / signal["price_a"]
        amount_b = notional / signal["price_b"]

        ex_a.set_leverage(sym_a, signal["leverage"])
        ex_b.set_leverage(sym_b, signal["leverage"])

        # Sides depend on direction: normal = long A / short B; reverse = short A / long B
        side_a_open  = "buy"  if direction == "buy_a_sell_b" else "sell"
        side_b_open  = "sell" if direction == "buy_a_sell_b" else "buy"
        close_side_a = "long" if direction == "buy_a_sell_b" else "short"
        close_side_b = "short" if direction == "buy_a_sell_b" else "long"

        # ── Parallel single-shot limit placement with cross-the-spread ────────
        # Both legs placed simultaneously in threads. Each leg gets ONE shot
        # (no re-quote, no cancel-replace cycles). Crosses spread by N bps
        # for guaranteed taker fills — eliminates Bybit margin-race issues.
        total_to = self.s_cfg.get("order_fill_timeout_seconds", 8)
        agg_bps  = self.s_cfg.get("entry_aggressiveness_bps", 5) / 10000.0

        # Pre-compute crossed prices once (single-shot — no re-fetching)
        if side_a_open == "buy":
            limit_price_a = signal["price_a"] * (1 + agg_bps)
        else:
            limit_price_a = signal["price_a"] * (1 - agg_bps)
        if side_b_open == "buy":
            limit_price_b = signal["price_b"] * (1 + agg_bps)
        else:
            limit_price_b = signal["price_b"] * (1 - agg_bps)

        # entry_order_type: "limit" (default) | "market"
        # Market orders eliminate cancel-fill race orphans at the cost of taker fees
        # on both legs. Use this when one exchange consistently fails to fill limits
        # in the current regime (e.g. directional spread collapse during 8s window).
        entry_order_type = self.s_cfg.get("entry_order_type", "limit")

        (a_filled, order_id_a, fill_price_a), (b_filled, order_id_b, fill_price_b) = \
            self._place_both_legs_parallel(
                ex_a, sym_a, side_a_open, amount_a, limit_price_a,
                ex_b, sym_b, side_b_open, amount_b, limit_price_b,
                timeout_s=total_to,
                order_type=entry_order_type,
            )

        # Handle the 4 outcome states:
        if a_filled and b_filled:
            # SUCCESS — both legs filled, proceed to log trade
            logger.info(f"{pair}: Both legs filled "
                        f"(A@{fill_price_a:.6f}, B@{fill_price_b:.6f})")

        elif (a_filled or b_filled):
            # ONE LEG REPORTED FILLED. This is either:
            #   (a) A genuine orphan — only one leg actually filled
            #   (b) A cancel-fill race — both filled, but our flag missed one
            # Distinguish by checking actual positions on BOTH exchanges.
            time.sleep(0.5)  # let fills/cancels settle on exchange side
            try:
                positions_a = ex_a.fetch_positions([sym_a])
                actual_size_a = sum(abs(float(p.get("contracts") or 0)) for p in positions_a
                                    if p.get("symbol") == sym_a)
            except Exception as e:
                logger.warning(f"{pair}: position check on A failed: {e}")
                actual_size_a = amount_a if a_filled else 0
            try:
                positions_b = ex_b.fetch_positions([sym_b])
                actual_size_b = sum(abs(float(p.get("contracts") or 0)) for p in positions_b
                                    if p.get("symbol") == sym_b)
            except Exception as e:
                logger.warning(f"{pair}: position check on B failed: {e}")
                actual_size_b = amount_b if b_filled else 0

            # Require BOTH legs to be substantially filled (>=90% of intended)
            # to declare a successful hedged entry. Anything less is dangerously
            # imbalanced — directional exposure pretending to be delta-neutral.
            ratio_a = (actual_size_a / amount_a) if amount_a > 0 else 0
            ratio_b = (actual_size_b / amount_b) if amount_b > 0 else 0
            FILL_QUALITY_THRESHOLD = 0.90   # both legs must be >=90% filled

            both_substantially_filled = (
                ratio_a >= FILL_QUALITY_THRESHOLD and ratio_b >= FILL_QUALITY_THRESHOLD
            )

            if both_substantially_filled:
                # CANCEL-FILL RACE — both legs substantially filled, this is a real hedged entry
                logger.info(f"{pair}: Both legs filled (race resolved). "
                            f"A={actual_size_a:.4f} ({ratio_a*100:.0f}% of {amount_a:.4f}), "
                            f"B={actual_size_b:.4f} ({ratio_b*100:.0f}% of {amount_b:.4f}). "
                            f"Treating as successful entry.")
                # Use actual fill sizes (handles small step-size rounding)
                amount_a = actual_size_a
                amount_b = actual_size_b
                # Recover fill prices for accurate P&L (use observed price as fallback)
                if not (fill_price_a and fill_price_a > 0):
                    fill_price_a = signal["price_a"]
                if not (fill_price_b and fill_price_b > 0):
                    fill_price_b = signal["price_b"]
                # Fall through to normal trade-logging flow below
                a_filled = True
                b_filled = True

            else:
                # GENUINE ORPHAN — close whichever leg(s) actually have positions
                self._open_positions.pop(pair, None)
                self._record_orphan_event(pair)   # circuit breaker
                close_errors = []
                # Capture mark prices BEFORE close so synthetic trade row can compute PnL.
                mark_a = mark_b = 0.0
                try:    mark_a = float((ex_a.fetch_ticker(sym_a) or {}).get("last") or 0)
                except: pass
                try:    mark_b = float((ex_b.fetch_ticker(sym_b) or {}).get("last") or 0)
                except: pass
                if actual_size_a > 0:
                    try:
                        ex_a.close_position(sym_a, close_side_a, actual_size_a, order_type="market")
                        logger.info(f"{pair}: Leg A orphan close succeeded ({actual_size_a} units)")
                        # Side held = opposite of close_side
                        side_held = "long" if (str(close_side_a).lower() == "sell") else "short"
                        self._log_orphan_trade(
                            pair, pair_cfg["leg_a_exchange"], sym_a,
                            side_held, actual_size_a,
                            signal["price_a"], mark_a, leg_label="A_imbalanced"
                        )
                    except Exception as ce:
                        close_errors.append(f"Leg A close: {ce}")
                if actual_size_b > 0:
                    try:
                        ex_b.close_position(sym_b, close_side_b, actual_size_b, order_type="market")
                        logger.info(f"{pair}: Leg B orphan close succeeded ({actual_size_b} units)")
                        side_held = "long" if (str(close_side_b).lower() == "sell") else "short"
                        self._log_orphan_trade(
                            pair, pair_cfg["leg_b_exchange"], sym_b,
                            side_held, actual_size_b,
                            signal["price_b"], mark_b, leg_label="B_imbalanced"
                        )
                    except Exception as ce:
                        close_errors.append(f"Leg B close: {ce}")

                if close_errors:
                    logger.critical(f"HALF-OPEN POSITION: {pair} — {' | '.join(close_errors)} — MANUAL INTERVENTION REQUIRED")
                    self.alert_manager.send_telegram(
                        f"<b>🚨 ORPHAN CLOSE FAILED — MANUAL ACTION REQUIRED</b>\n"
                        f"Pair: {pair}\nErrors: {' | '.join(close_errors)}\n"
                        f"Check both exchanges manually!"
                    )
                else:
                    logger.warning(
                        f"{pair}: Imbalanced/orphan fills (a_actual={actual_size_a} ratio={ratio_a*100:.0f}%, "
                        f"b_actual={actual_size_b} ratio={ratio_b*100:.0f}%) — closed at market"
                    )
                    # Alert if this was an imbalanced fill (both>0 but ratio mismatch) — common bug indicator
                    if actual_size_a > 0 and actual_size_b > 0:
                        self.alert_manager.send_telegram(
                            f"<b>⚠️ Imbalanced Fill Detected</b>\n"
                            f"Pair: {pair}\n"
                            f"A filled: {actual_size_a} ({ratio_a*100:.0f}% of intent)\n"
                            f"B filled: {actual_size_b} ({ratio_b*100:.0f}% of intent)\n"
                            f"Both closed at market — review fill quality."
                        )
                return

        else:
            # NEITHER REPORTED FILLED — but verify against actual exchange state
            # before trusting it. Cancel-fill races can return (False, False)
            # while exchanges silently fill the orders.
            time.sleep(0.5)  # let any in-flight fills settle
            try:
                positions_a = ex_a.fetch_positions([sym_a])
                actual_size_a = sum(abs(float(p.get("contracts") or 0)) for p in positions_a
                                    if p.get("symbol") == sym_a)
            except Exception as e:
                logger.warning(f"{pair}: post-cancel position check on A failed: {e}")
                actual_size_a = 0
            try:
                positions_b = ex_b.fetch_positions([sym_b])
                actual_size_b = sum(abs(float(p.get("contracts") or 0)) for p in positions_b
                                    if p.get("symbol") == sym_b)
            except Exception as e:
                logger.warning(f"{pair}: post-cancel position check on B failed: {e}")
                actual_size_b = 0

            if actual_size_a == 0 and actual_size_b == 0:
                # Genuinely nothing filled — clean state.
                logger.info(f"{pair}: Neither leg filled within {total_to}s — skipping trade")
                self._open_positions.pop(pair, None)
                return

            # Cancel-fill race on the "neither" path: at least one leg actually
            # filled despite the cancel. Check whether both filled enough to
            # declare a real entry, otherwise close the orphan(s) immediately.
            ratio_a = (actual_size_a / amount_a) if amount_a > 0 else 0
            ratio_b = (actual_size_b / amount_b) if amount_b > 0 else 0
            FILL_QUALITY_THRESHOLD = 0.90

            if ratio_a >= FILL_QUALITY_THRESHOLD and ratio_b >= FILL_QUALITY_THRESHOLD:
                logger.warning(
                    f"{pair}: CANCEL-FILL RACE on 'neither' path — both legs actually filled "
                    f"(A={actual_size_a:.4f} {ratio_a*100:.0f}%, "
                    f"B={actual_size_b:.4f} {ratio_b*100:.0f}%). Treating as successful entry."
                )
                amount_a = actual_size_a
                amount_b = actual_size_b
                if not (fill_price_a and fill_price_a > 0):
                    fill_price_a = signal["price_a"]
                if not (fill_price_b and fill_price_b > 0):
                    fill_price_b = signal["price_b"]
                a_filled = True
                b_filled = True
                # fall through to normal trade-logging flow below
            else:
                # Imbalanced orphan — close everything that exists.
                logger.critical(
                    f"{pair}: ORPHAN ON ENTRY (neither-path cancel-fill race) — "
                    f"A={actual_size_a} ratio={ratio_a*100:.0f}%, "
                    f"B={actual_size_b} ratio={ratio_b*100:.0f}% — closing immediately"
                )
                self._open_positions.pop(pair, None)
                self._record_orphan_event(pair)   # circuit breaker
                close_errors = []
                mark_a = mark_b = 0.0
                try:    mark_a = float((ex_a.fetch_ticker(sym_a) or {}).get("last") or 0)
                except: pass
                try:    mark_b = float((ex_b.fetch_ticker(sym_b) or {}).get("last") or 0)
                except: pass
                if actual_size_a > 0:
                    try:
                        ex_a.close_position(sym_a, close_side_a, actual_size_a, order_type="market")
                        logger.info(f"{pair}: Leg A neither-path orphan closed ({actual_size_a})")
                        side_held = "long" if (str(close_side_a).lower() == "sell") else "short"
                        self._log_orphan_trade(
                            pair, pair_cfg["leg_a_exchange"], sym_a,
                            side_held, actual_size_a,
                            signal["price_a"], mark_a, leg_label="A_neither"
                        )
                    except Exception as ce:
                        close_errors.append(f"Leg A close: {ce}")
                if actual_size_b > 0:
                    try:
                        ex_b.close_position(sym_b, close_side_b, actual_size_b, order_type="market")
                        logger.info(f"{pair}: Leg B neither-path orphan closed ({actual_size_b})")
                        side_held = "long" if (str(close_side_b).lower() == "sell") else "short"
                        self._log_orphan_trade(
                            pair, pair_cfg["leg_b_exchange"], sym_b,
                            side_held, actual_size_b,
                            signal["price_b"], mark_b, leg_label="B_neither"
                        )
                    except Exception as ce:
                        close_errors.append(f"Leg B close: {ce}")
                if close_errors:
                    logger.critical(f"HALF-OPEN POSITION: {pair} — {' | '.join(close_errors)} — MANUAL INTERVENTION REQUIRED")
                    self.alert_manager.send_telegram(
                        f"<b>🚨 ORPHAN CLOSE FAILED (neither-path) — MANUAL ACTION</b>\n"
                        f"Pair: {pair}\nErrors: {' | '.join(close_errors)}"
                    )
                else:
                    self.alert_manager.send_telegram(
                        f"<b>⚠️ Cancel-Fill Race (neither-path)</b>\n"
                        f"Pair: {pair}\n"
                        f"A: {actual_size_a} ({ratio_a*100:.0f}%)\n"
                        f"B: {actual_size_b} ({ratio_b*100:.0f}%)\n"
                        f"Closed at market — orphan detector race fixed."
                    )
                return

        # Use actual fill prices for entry record (more accurate than ticker snapshot)
        if fill_price_a and fill_price_a > 0:
            signal["price_a"] = fill_price_a
        if fill_price_b and fill_price_b > 0:
            signal["price_b"] = fill_price_b

        # CRITICAL FIX 2026-05-07: recompute entry_spread from ACTUAL fill prices.
        # Previously stored the DETECTED spread (from ticker scan), but slippage
        # between detection and fill can eat 80%+ of the spread.
        # Example: detected 0.463% → actual fills gave 0.085% real edge.
        # Without this fix, the exit profit_target was based on phantom edge,
        # causing trades to "hit profit target" while actually losing money.
        if signal["price_a"] > 0 and signal["price_b"] > 0:
            actual_entry_spread = (signal["price_b"] - signal["price_a"]) / signal["price_a"]
            if abs(actual_entry_spread - signal["spread"]) > 0.0005:
                logger.warning(
                    f"{pair}: SLIPPAGE detected — scan spread {signal['spread']*100:.3f}% "
                    f"vs filled spread {actual_entry_spread*100:.3f}% "
                    f"(slippage {(signal['spread']-actual_entry_spread)*100:.3f}%)"
                )
            signal["spread"] = actual_entry_spread

            # Slippage guard: if actual filled spread is below the minimum
            # entry threshold, the latency arms race ate our edge. Position is
            # already open — but immediately close it to cap the loss at friction
            # only, rather than holding a phantom "profitable" trade.
            min_entry = self._pair_thresholds.get(pair, self._min_spread_entry)
            slippage_floor = min_entry * 0.5    # require >=50% of intended edge survived
            if actual_entry_spread < slippage_floor:
                logger.critical(
                    f"{pair}: SLIPPAGE-KILL — filled spread {actual_entry_spread*100:.3f}% "
                    f"is below floor {slippage_floor*100:.3f}% (intended >={min_entry*100:.3f}%). "
                    f"Closing immediately to avoid holding a guaranteed loser."
                )
                # Immediately market-close both legs; fall through to log trade
                # (it will still record properly with the realistic entry_spread).
                close_side_a_kill = "long" if direction == "buy_a_sell_b" else "short"
                close_side_b_kill = "short" if direction == "buy_a_sell_b" else "long"
                try:
                    ex_a.close_position(sym_a, close_side_a_kill, amount_a, order_type="market")
                except Exception as e:
                    logger.error(f"{pair}: SLIPPAGE-KILL leg A close failed: {e}")
                try:
                    ex_b.close_position(sym_b, close_side_b_kill, amount_b, order_type="market")
                except Exception as e:
                    logger.error(f"{pair}: SLIPPAGE-KILL leg B close failed: {e}")
                self._open_positions.pop(pair, None)

                # CRITICAL: prevent retry-loop. Without this, the next 3s tick
                # sees the same scan-spread on the same pair and tries again,
                # bleeding friction repeatedly. Two defenses:
                #   1. Long cooldown (5 min default) on this pair specifically
                #   2. Treat as circuit-breaker event — 3 slippage-kills in
                #      30 min disables the pair for the breaker pause window
                slippage_cooldown_min = self.s_cfg.get("slippage_cooldown_min", 5)
                self._last_close_time[pair] = (
                    datetime.now(timezone.utc)
                    + timedelta(minutes=slippage_cooldown_min)
                    - timedelta(seconds=self.s_cfg.get("reentry_cooldown_seconds", 120))
                )
                # ^ this offsets the timestamp so the existing cooldown check
                #   blocks re-entry for `slippage_cooldown_min` minutes total
                self._record_orphan_event(pair)   # counts toward circuit breaker
                logger.critical(
                    f"{pair}: SLIPPAGE-KILL cooldown {slippage_cooldown_min} min "
                    f"(prevents retry-loop on stale-edge pair)"
                )

                # CRITICAL: log a synthetic trade row so /snapshot and the
                # calculator reflect this friction loss. Without this, the
                # wallet sees the loss but the trades table doesn't,
                # creating a /wallet vs /snapshot gap.
                try:
                    # Estimate friction = round-trip taker fees + ~0.05% slippage
                    fee_a_rate = self._TAKER_FEES.get(pair_cfg["leg_a_exchange"], 0.0005)
                    fee_b_rate = self._TAKER_FEES.get(pair_cfg["leg_b_exchange"], 0.0005)
                    fees_est = notional * (fee_a_rate + fee_b_rate) * 2  # entry + close
                    # Net is approximately -fees (no held P&L since we closed instantly)
                    log_trade({
                        "timestamp":      datetime.now(timezone.utc).isoformat(),
                        "pair":           f"SLIPKILL_{pair}",
                        "leg_a_exchange": pair_cfg["leg_a_exchange"],
                        "leg_b_exchange": pair_cfg["leg_b_exchange"],
                        "leg_a_symbol":   sym_a,
                        "leg_b_symbol":   sym_b,
                        "notional_usd":   notional,
                        "spread_pct":     actual_entry_spread,
                        "gross_profit":   0.0,
                        "fees":           fees_est,
                        "net_profit":     -fees_est,
                        "funding_income": 0.0,
                        "status":         "closed",
                        "paper_mode":     int(self.paper_mode),
                        "amount_a":       amount_a,
                        "amount_b":       amount_b,
                        "entry_price_a":  signal["price_a"],
                        "entry_price_b":  signal["price_b"],
                        "direction":      direction,
                        "notes":          (f"slippage_kill scan={signal['spread']*100:.3f}% "
                                           f"filled={actual_entry_spread*100:.3f}%"),
                    })
                except Exception as le:
                    logger.warning(f"Failed to log synthetic slippage-kill trade: {le}")
                return

        trade_id = log_trade({
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "pair":           pair,
            "leg_a_exchange": pair_cfg["leg_a_exchange"],
            "leg_b_exchange": pair_cfg["leg_b_exchange"],
            "leg_a_symbol":   sym_a,
            "leg_b_symbol":   sym_b,
            "notional_usd":   notional,
            "spread_pct":     signal["spread"],
            "gross_profit":   None,
            "fees":           None,
            "net_profit":     None,
            "funding_income": signal["funding_income"],
            "status":         "open",
            "paper_mode":     int(self.paper_mode),
            "amount_a":       amount_a,
            "amount_b":       amount_b,
            "entry_price_a":  signal["price_a"],
            "entry_price_b":  signal["price_b"],
            "direction":      direction,
            "notes": (
                f"entry spread={signal['spread']*100:.3f}% lev={signal['leverage']}x "
                f"atr_halved={signal['atr_halved']} order_a={order_id_a} order_b={order_id_b}"
            ),
        })

        self._open_positions[pair] = {
            "trade_id": trade_id, "entry_price_a": signal["price_a"],
            "entry_price_b": signal["price_b"], "amount_a": amount_a, "amount_b": amount_b,
            "order_id_a": order_id_a, "order_id_b": order_id_b,
            "direction": direction, "close_side_a": close_side_a, "close_side_b": close_side_b,
            # Preserve exchange/symbol info for orphan detector
            "leg_a_exchange": pair_cfg["leg_a_exchange"], "leg_a_symbol": sym_a,
            "leg_b_exchange": pair_cfg["leg_b_exchange"], "leg_b_symbol": sym_b,
        }

        logger.info(
            f"ENTERED {pair} | {direction} | notional=${notional:,.0f} "
            f"| spread={signal['spread']*100:.3f}% | lev={signal['leverage']}x "
            f"| {'PAPER' if self.paper_mode else 'LIVE'}"
        )

    def _close_position(self, trade: dict, current_spread: float, reason: str):
        sym_a = trade["leg_a_symbol"]
        sym_b = trade["leg_b_symbol"]
        ex_a  = self.exchanges.get(trade["leg_a_exchange"])
        ex_b  = self.exchanges.get(trade["leg_b_exchange"])
        pair  = trade["pair"]

        notional     = trade["notional_usd"]
        pos          = self._open_positions.get(pair, {})
        close_side_a = pos.get("close_side_a", "long")
        close_side_b = pos.get("close_side_b", "short")

        # Fetch exit prices — if the ticker call fails after retries, fall back to
        # market order (price=None) so we still exit rather than sending a limit at $0.
        exit_price_a = None
        exit_price_b = None
        if ex_a:
            try:
                t_a = ex_a.fetch_ticker(sym_a)
                # Long leg: close by selling → post at bid. Short leg: cover by buying → post at ask.
                exit_price_a = float(t_a.get("bid" if close_side_a == "long" else "ask") or t_a["last"])
            except Exception as e:
                logger.warning(f"{pair}: Could not fetch Leg A ticker ({e}) — will use market order")

        if ex_b:
            try:
                t_b = ex_b.fetch_ticker(sym_b)
                exit_price_b = float(t_b.get("bid" if close_side_b == "long" else "ask") or t_b["last"])
            except Exception as e:
                logger.warning(f"{pair}: Could not fetch Leg B ticker ({e}) — will use market order")

        # Resolve close amounts — priority: stored amount → compute from exit price → notional
        # Never fall back to a near-zero hardcoded value; instead skip the leg if price unknown.
        stored_a = pos.get("amount_a")
        stored_b = pos.get("amount_b")
        amount_a = (stored_a if stored_a and stored_a > 0
                    else (notional / exit_price_a if exit_price_a and exit_price_a > 0
                          else None))
        amount_b = (stored_b if stored_b and stored_b > 0
                    else (notional / exit_price_b if exit_price_b and exit_price_b > 0
                          else None))

        # Close with limit orders for maker fees; fall back to market on timeout.
        # CRITICAL: market fallback must verify actual remaining position size first
        # (cancel-fill races can leave partial fills, blind market close over-closes).
        if ex_a and amount_a:
            use_limit_a = exit_price_a is not None and exit_price_a > 0
            close_a    = ex_a.close_position(sym_a, close_side_a, amount_a,
                                             order_type="limit" if use_limit_a else "market",
                                             price=exit_price_a if use_limit_a else None)
            close_id_a = close_a.get("id", "unknown")
            if use_limit_a and not self._wait_for_fill(ex_a, close_id_a, sym_a):
                logger.warning(f"{pair}: Leg A close limit timed out — verifying remaining position")
                try:
                    ex_a.cancel_order(close_id_a, sym_a)
                except Exception:
                    pass
                time.sleep(0.5)  # let cancel/fill settle on exchange
                # Check ACTUAL remaining position before market-closing
                try:
                    positions = ex_a.fetch_positions([sym_a])
                    remaining_a = sum(abs(float(p.get("contracts") or 0)) for p in positions
                                      if p.get("symbol") == sym_a)
                    if remaining_a > 0:
                        ex_a.close_position(sym_a, close_side_a, remaining_a, order_type="market")
                        logger.info(f"{pair}: Leg A market-closed remaining {remaining_a} (intended {amount_a})")
                    else:
                        logger.info(f"{pair}: Leg A already flat after cancel — no market close needed")
                except Exception as e:
                    logger.warning(f"{pair}: Leg A position check failed: {e} — using intended amount {amount_a}")
                    try:
                        ex_a.close_position(sym_a, close_side_a, amount_a, order_type="market")
                    except Exception as ce:
                        logger.error(f"{pair}: Leg A market close fallback failed: {ce}")
        elif ex_a and not amount_a:
            logger.error(f"{pair}: Leg A amount unknown and ticker unavailable — leg A NOT closed. Manual intervention required!")
            self.alert_manager.send_alert("close_error", f"🚨 {pair}: Leg A could not be closed — amount unknown. Check exchange manually!")

        if ex_b and amount_b:
            use_limit_b = exit_price_b is not None and exit_price_b > 0
            close_b    = ex_b.close_position(sym_b, close_side_b, amount_b,
                                             order_type="limit" if use_limit_b else "market",
                                             price=exit_price_b if use_limit_b else None)
            close_id_b = close_b.get("id", "unknown")
            if use_limit_b and not self._wait_for_fill(ex_b, close_id_b, sym_b):
                logger.warning(f"{pair}: Leg B close limit timed out — verifying remaining position")
                try:
                    ex_b.cancel_order(close_id_b, sym_b)
                except Exception:
                    pass
                time.sleep(0.5)
                try:
                    positions = ex_b.fetch_positions([sym_b])
                    remaining_b = sum(abs(float(p.get("contracts") or 0)) for p in positions
                                      if p.get("symbol") == sym_b)
                    if remaining_b > 0:
                        ex_b.close_position(sym_b, close_side_b, remaining_b, order_type="market")
                        logger.info(f"{pair}: Leg B market-closed remaining {remaining_b} (intended {amount_b})")
                    else:
                        logger.info(f"{pair}: Leg B already flat after cancel — no market close needed")
                except Exception as e:
                    logger.warning(f"{pair}: Leg B position check failed: {e} — using intended amount {amount_b}")
                    try:
                        ex_b.close_position(sym_b, close_side_b, amount_b, order_type="market")
                    except Exception as ce:
                        logger.error(f"{pair}: Leg B market close fallback failed: {ce}")
        elif ex_b and not amount_b:
            logger.error(f"{pair}: Leg B amount unknown and ticker unavailable — leg B NOT closed. Manual intervention required!")
            self.alert_manager.send_alert("close_error", f"🚨 {pair}: Leg B could not be closed — amount unknown. Check exchange manually!")

        # ── Gross P&L: use actual fill prices when known ──────────────────────
        # NOTE on naming: for buy_b_sell_a, entry_price_a = ask_b (price paid on ex_b)
        # and entry_price_b = bid_a (price received on ex_a). The "a/b" suffix follows
        # the signal dict, not the exchange leg. See formula comments below.
        # Always read entry_spread from DB — used in the notes string regardless of path.
        entry_spread  = trade.get("spread_pct") or 0.0
        # Fall back to the spread-surrogate for rehydrated trades (entry prices = 0).
        entry_price_a = pos.get("entry_price_a", 0)
        entry_price_b = pos.get("entry_price_b", 0)
        direction     = pos.get("direction", "buy_a_sell_b")
        if entry_price_a > 0 and entry_price_b > 0 and amount_a > 0 and amount_b > 0:
            if direction == "buy_a_sell_b":
                # Long A (entry_price_a=ask_a), Short B (entry_price_b=bid_b)
                # exit_price_a = bid_a (sell long A), exit_price_b = ask_b (cover short B)
                gross = ((exit_price_a - entry_price_a) * amount_a +   # Long A P&L
                         (entry_price_b - exit_price_b) * amount_b)    # Short B P&L
            else:
                # buy_b_sell_a: Long B (entry_price_a=ask_b), Short A (entry_price_b=bid_a)
                # exit_price_b = bid_b (sell long B), exit_price_a = ask_a (cover short A)
                gross = ((exit_price_b - entry_price_a) * amount_a +   # Long B P&L
                         (entry_price_b - exit_price_a) * amount_b)    # Short A P&L
        else:
            # Surrogate for rehydrated trades: spread-compression × notional
            gross = (entry_spread - current_spread) * notional

        # Maker fee: 4 orders (open A, open B, close A, close B).
        # Rate is per-exchange from config — MEXC is 0%, others are 0.02%.
        fee_cfg  = self.cfg.get("exchange_fees", {})
        fee_a    = fee_cfg.get(trade.get("leg_a_exchange", ""), 0.0002)
        fee_b    = fee_cfg.get(trade.get("leg_b_exchange", ""), 0.0002)
        FEE_RATE = (fee_a + fee_b) * 2   # open + close for both legs
        fees     = notional * FEE_RATE

        # Add funding income collected during hold period
        # Approximate settlements = hold_hours / 8 (settlements every 8h)
        try:
            trade_ts = datetime.fromisoformat(trade["timestamp"])
            if trade_ts.tzinfo is None:
                trade_ts = trade_ts.replace(tzinfo=timezone.utc)
            hold_hours = (datetime.now(timezone.utc) - trade_ts).total_seconds() / 3600
        except Exception:
            hold_hours = 0
        funding_rate        = float(trade.get("funding_income") or 0)
        funding_settlements = max(int(hold_hours / 8), 0)
        funding_collected   = funding_rate * notional * funding_settlements

        net = gross + funding_collected - fees

        update_trade(trade["id"], {
            "status":       "closed",
            "gross_profit": round(gross + funding_collected, 6),
            "fees":         round(fees, 6),
            "net_profit":   round(net, 6),
            "notes": (
                f"closed:{reason} entry={entry_spread*100:.3f}% exit={current_spread*100:.3f}%"
                f" funding_collected={funding_collected:.4f}"
            ),
        })

        self._open_positions.pop(pair, None)
        self._last_close_time[pair] = datetime.now(timezone.utc)   # start re-entry cooldown
        self._update_pair_threshold(pair, net)             # adaptive threshold update
        logger.info(f"CLOSED {pair} | reason={reason} | net=${net:.2f}")

        daily_pnl = get_daily_net_profit()
        limit     = self.risk_manager.get_effective_daily_loss_limit()
        if daily_pnl < -limit:
            self.alert_manager.send_alert(
                "max_daily_loss",
                f"Daily P&L ${daily_pnl:.2f} exceeded -${limit:.0f} (dynamic limit). Paused 24h."
            )
            self.risk_manager.halt_trading(
                reason=f"Daily loss limit hit: ${daily_pnl:.2f} (effective limit -${limit:.0f})",
                duration_hours=24
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _update_pair_threshold(self, pair: str, net_profit: float):
        """
        Adaptive per-pair entry threshold.

        After a LOSING trade:  threshold rises by step_up  (default +0.10%)
        After a WINNING trade: threshold falls by step_down (default -0.05%)

        Bounds:
          floor   = global min_spread_entry (0.11%) — never go below this
          ceiling = 0.25% — pairs rarely have spreads above this; acts as soft-disable

        Effect: a pair that loses consistently needs an ever-wider spread to enter.
        After ~14 consecutive losses the threshold reaches 0.25% and the pair
        effectively pauses. One profitable trade earns back 0.05%, so it takes
        ~2 good trades to undo each bad one — conservative by design.

        Example (FIL scenario):
          Start 0.11% → loss → 0.21% → loss → 0.25% (ceiling, stops trading)
          Conditions improve → spread hits 0.25% → trade fills → profit → 0.20%
          Another profit → 0.15% → slowly works back toward 0.11%
        """
        adaptive = self.s_cfg.get("adaptive_spread", {})
        if not adaptive.get("enabled", True):
            return

        floor    = adaptive.get("floor",    self._min_spread_entry)
        ceiling  = adaptive.get("ceiling",  0.0025)
        step_up  = adaptive.get("step_up",  0.0010)
        step_dn  = adaptive.get("step_down",0.0005)

        current = self._pair_thresholds.get(pair, floor)

        if net_profit < 0:
            new = min(current + step_up, ceiling)
            verb = "RAISED"
        else:
            new = max(current - step_dn, floor)
            verb = "lowered"

        self._pair_thresholds[pair] = new
        self._pair_threshold_updated_at[pair] = datetime.now(timezone.utc)

        # Only log when threshold actually changes
        if abs(new - current) > 1e-6:
            at_ceiling = new >= ceiling * 0.999
            logger.info(
                f"ADAPTIVE {pair}: threshold {verb} "
                f"{current*100:.3f}% -> {new*100:.3f}% "
                f"(net=${net_profit:.2f})"
                f"{' -- pair THROTTLED until spread widens' if at_ceiling else ''}"
            )

    def _get_current_spread(self, trade: dict) -> Optional[float]:
        try:
            ex_a = self.exchanges.get(trade["leg_a_exchange"])
            ex_b = self.exchanges.get(trade["leg_b_exchange"])
            if not ex_a or not ex_b:
                return None
            t_a = ex_a.fetch_ticker(trade["leg_a_symbol"])
            t_b = ex_b.fetch_ticker(trade["leg_b_symbol"])
            direction = self._open_positions.get(trade["pair"], {}).get("direction", "buy_a_sell_b")
            if direction == "buy_a_sell_b":
                # We are long A, short B. Current exit value = sell A (bid_a), buy B (ask_b)
                bid_a = float(t_a.get("bid") or t_a["last"])
                ask_b = float(t_b.get("ask") or t_b["last"])
                return (bid_a - ask_b) / ask_b
            else:
                # We are short A, long B. Current exit value = buy A (ask_a), sell B (bid_b)
                ask_a = float(t_a.get("ask") or t_a["last"])
                bid_b = float(t_b.get("bid") or t_b["last"])
                return (bid_b - ask_a) / ask_a
        except Exception as e:
            logger.warning(f"Could not fetch spread: {e}")
            return None

    def _get_deploy_pct(self, spread: float, volume_ok: bool) -> float:
        ps = self.ps_cfg
        if not volume_ok or spread < ps["medium_spread_min"]:
            return ps["low_spread_deploy"]
        elif spread >= ps["high_spread_threshold"]:
            return ps["high_spread_deploy"]
        return ps["medium_spread_deploy"]

    def _get_funding_income(self, exchange: ExchangeClient, symbol: str) -> float:
        try:
            rate = float(exchange.fetch_funding_rate(symbol).get("fundingRate", 0) or 0)
            return rate if rate > self.s_cfg["funding_harvest_min_rate"] else 0.0
        except Exception:
            return 0.0

    def _is_funding_rate_adverse(self, exchange: ExchangeClient, symbol: str) -> bool:
        try:
            rate = float(exchange.fetch_funding_rate(symbol).get("fundingRate", 0) or 0)
            return rate < -self.s_cfg["funding_harvest_min_rate"]
        except Exception:
            return False

    def _get_atr_pct(self, exchange: ExchangeClient, symbol: str) -> Optional[float]:
        cached = self._atr_cache.get(symbol)
        if cached:
            atr_val, fetched_at = cached
            if (datetime.now(timezone.utc) - fetched_at).total_seconds() < 600:
                return atr_val
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, "1h", limit=24)
            if len(ohlcv) < 2:
                return None
            highs = np.array([c[2] for c in ohlcv], dtype=float)
            lows  = np.array([c[3] for c in ohlcv], dtype=float)
            mid   = float(np.mean((highs + lows) / 2))
            atr   = float(np.mean(highs - lows))
            val   = atr / mid if mid > 0 else 0.0
            self._atr_cache[symbol] = (val, datetime.now(timezone.utc))
            return val
        except Exception as e:
            logger.debug(f"ATR error {symbol}: {e}")
            return None

    def _check_volume(self, ticker: dict) -> bool:
        try:
            vol = float(
                ticker.get("quoteVolume") or
                ticker.get("info", {}).get("turnover24h", 0) or 0
            )
            return vol > 1_000_000
        except Exception:
            return True

    def _get_rolling_correlation(self, ex_a, ex_b, sym_a: str, sym_b: str) -> Optional[float]:
        try:
            lookback = self.ps_cfg["correlation_lookback"]
            closes_a = [c[4] for c in ex_a.fetch_ohlcv(sym_a, "1m", limit=lookback)]
            closes_b = [c[4] for c in ex_b.fetch_ohlcv(sym_b, "1m", limit=lookback)]
            if len(closes_a) < 10 or len(closes_b) < 10:
                return None
            n = min(len(closes_a), len(closes_b))
            a = np.array(closes_a[-n:], dtype=float)
            b = np.array(closes_b[-n:], dtype=float)
            if np.std(a) == 0 or np.std(b) == 0:
                return None
            return float(np.corrcoef(a, b)[0, 1])
        except Exception as e:
            logger.warning(f"Correlation error: {e}")
            return None

    def _estimate_unrealised_pnl_pct(self, trade: dict) -> float:
        pos = self._open_positions.get(trade["pair"])
        if not pos or pos["entry_price_a"] == 0:
            return 0.0
        try:
            ex_a    = self.exchanges.get(trade["leg_a_exchange"])
            ex_b    = self.exchanges.get(trade["leg_b_exchange"])
            price_a = float(ex_a.fetch_ticker(trade["leg_a_symbol"])["last"]) if ex_a else 0
            price_b = float(ex_b.fetch_ticker(trade["leg_b_symbol"])["last"]) if ex_b else 0
            entry_a = pos["entry_price_a"]
            entry_b = pos["entry_price_b"]
            if entry_a == 0 or entry_b == 0:
                return 0.0
            direction = pos.get("direction", "buy_a_sell_b")
            if direction == "buy_a_sell_b":
                long_pnl  = (price_a - entry_a) / entry_a
                short_pnl = (entry_b - price_b) / entry_b
            else:
                long_pnl  = (price_b - entry_b) / entry_b
                short_pnl = (entry_a - price_a) / entry_a
            return (long_pnl + short_pnl) / 2
        except Exception:
            return 0.0

    def _is_near_funding_settlement(self, window_min: int = 15) -> bool:
        """True if a funding settlement is within window_min minutes."""
        now = datetime.now(timezone.utc)
        for hour in _FUNDING_SETTLEMENT_HOURS_UTC:
            settlement = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if settlement < now:
                settlement += timedelta(days=1)
            mins_to = (settlement - now).total_seconds() / 60
            if 0 < mins_to <= window_min:
                return True
        return False

    def _scan_funding_harvest(self):
        """
        Proactively enter positions within 15 min of funding settlement when
        the funding rate is high enough to be worth harvesting on its own —
        even if the spread hasn't hit the normal entry threshold.
        Position is sized at 30% deploy (conservative) and tagged as a harvest entry.
        """
        harvest_min = self.s_cfg.get("funding_harvest_entry_min_rate", 0.0003)
        if not self._is_near_funding_settlement(window_min=15):
            return
        if self._in_news_blackout():
            return
        if not self.risk_manager.is_trading_allowed():
            return

        max_pos    = self.s_cfg["max_simultaneous_positions"]
        open_count = len(get_open_trades())
        if open_count >= max_pos:
            return

        for pair_cfg in self.pairs_cfg:
            pair = f"{pair_cfg['leg_a_symbol']}_{pair_cfg['leg_b_symbol']}"
            if pair in self._open_positions:
                continue
            if open_count >= max_pos:
                break

            ex_a = self.exchanges.get(pair_cfg["leg_a_exchange"])
            ex_b = self.exchanges.get(pair_cfg["leg_b_exchange"])
            if not ex_a or not ex_b:
                continue

            try:
                rate_data = ex_a.fetch_funding_rate(pair_cfg["leg_a_symbol"])
                rate      = float(rate_data.get("fundingRate", 0) or 0)
                if rate < harvest_min:
                    continue

                ticker_a = ex_a.fetch_ticker(pair_cfg["leg_a_symbol"])
                ticker_b = ex_b.fetch_ticker(pair_cfg["leg_b_symbol"])
                ask_a = float(ticker_a.get("ask") or ticker_a["last"])
                bid_a = float(ticker_a.get("bid") or ticker_a["last"])
                ask_b = float(ticker_b.get("ask") or ticker_b["last"])
                bid_b = float(ticker_b.get("bid") or ticker_b["last"])
                if min(ask_a, bid_a, ask_b, bid_b) <= 0:
                    continue

                spread_ab = (bid_b - ask_a) / ask_a
                spread_ba = (bid_a - ask_b) / ask_b
                if spread_ab >= spread_ba:
                    spread, direction, price_a, price_b = spread_ab, "buy_a_sell_b", ask_a, bid_b
                else:
                    spread, direction, price_a, price_b = spread_ba, "buy_b_sell_a", ask_b, bid_a

                # Conservative deploy for harvest-only entries (configurable, default 30%)
                harvest_deploy = self.s_cfg.get("funding_harvest_deploy_pct", 0.30)
                total_capital  = self.cfg["capital"]["total_usdt"]
                reserve        = self.cfg["capital"]["stablecoin_reserve"]
                free_buffer    = self.cfg["capital"]["free_margin_buffer"]
                tradeable      = (total_capital - reserve) * (1 - free_buffer)
                leverage       = self.cfg["leverage"]["default"]
                open_count     = len(get_open_trades())
                # Use min=4 (same as main scan) to prevent over-sizing when open_count is low.
                # Without this, a single harvest entry at open_count=0 would get 100% of
                # tradeable capital (concurrent=1) — 4x oversizing vs the intended 30% deploy.
                concurrent     = max(min(open_count + 1, self.s_cfg["max_simultaneous_positions"]), 4)
                notional       = tradeable * leverage * harvest_deploy / concurrent

                signal = {
                    "spread":         spread,
                    "notional":       notional,
                    "leverage":       leverage,
                    "deploy_pct":     0.30,
                    "funding_income": rate,
                    "price_a":        price_a,
                    "price_b":        price_b,
                    "entry_price_a":  price_a,
                    "entry_price_b":  price_b,
                    "direction":      direction,
                    "atr_halved":     False,
                }
                logger.info(
                    f"FUNDING HARVEST {pair}: rate={rate*100:.4f}%/8h "
                    f"— entering {notional:.0f} notional"
                )
                self._enter_position(pair_cfg, signal)
                open_count += 1
                self._last_trade_time = datetime.now(timezone.utc)

            except Exception as e:
                logger.debug(f"Funding harvest error {pair}: {e}")

    def _check_inactivity(self):
        alert_hours = self.cfg["alerts"].get("inactivity_alert_hours", 4)
        if self._last_trade_time is None:
            self._last_trade_time = datetime.now(timezone.utc)
            return
        idle_h = (datetime.now(timezone.utc) - self._last_trade_time).total_seconds() / 3600
        if idle_h >= alert_hours:
            msg = f"Bot idle {idle_h:.1f}h — no trades executed during market hours"
            logger.warning(msg)
            self.alert_manager.send_alert("inactivity", msg)
            self._last_trade_time = datetime.now(timezone.utc)  # reset so we don't spam

    def _is_funding_window(self) -> bool:
        """
        True if we are within funding_window_minutes of a funding settlement.
        Settlements occur at 00:00, 08:00, 16:00 UTC on Binance & Bybit.
        """
        now = datetime.now(timezone.utc)
        for hour in _FUNDING_SETTLEMENT_HOURS_UTC:
            settlement = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if settlement < now:
                settlement += timedelta(days=1)
            diff_min = abs((now - settlement).total_seconds() / 60)
            if diff_min <= self._funding_window_min:
                return True
        return False

    def _should_hold_for_funding(self, trade: dict) -> bool:
        """
        True if a position should hold through the next funding payment.
        Criteria: settlement is within 15 minutes AND rate is positive
        (meaning we receive funding, not pay it).
        """
        now = datetime.now(timezone.utc)
        for hour in _FUNDING_SETTLEMENT_HOURS_UTC:
            settlement = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            mins_to = (settlement - now).total_seconds() / 60
            if 0 < mins_to <= 15:   # settlement upcoming within 15 min
                ex_a = self.exchanges.get(trade["leg_a_exchange"])
                if ex_a:
                    rate = self._get_funding_income(ex_a, trade["leg_a_symbol"])
                    if rate > 0:
                        return True
        return False

    def _in_news_blackout(self) -> bool:
        blackout_min = self.risk_cfg.get("news_blackout_minutes", 120)
        events       = self.cfg.get("news_blackout_events", [])
        now          = datetime.now(timezone.utc)
        for event_str in events:
            try:
                event_dt = datetime.fromisoformat(event_str)
                if abs((now - event_dt).total_seconds() / 60) <= blackout_min:
                    logger.info(f"News blackout active: {event_str}")
                    return True
            except ValueError:
                pass
        return False
