"""
strategies/basis_capture.py — Spot-vs-perp basis trading strategy.

Buys spot on Binance and shorts the perpetual on Binance Futures when the
perpetual trades at a premium (basis) above the spot price.

Revenue comes from two sources:
  1. Basis convergence: perp premium compresses back toward zero
  2. Funding payments: when funding rate > 0, short perp holders receive payment
     every 8 hours (00:00 / 08:00 / 16:00 UTC)

Zero directional risk: both legs move together, so market direction is irrelevant.

Typical basis on ETH/SOL/BTC: 0.05–0.20% per 8h during bull markets.
That's 0.6–2.4% per day on the deployed capital — before leverage.

Enable basis_pairs in config.yaml once paper-tested.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from utils.database import log_trade, update_trade, get_open_trades

logger = logging.getLogger(__name__)

# Funding settlements (UTC hours) — same as perp schedule
_SETTLEMENT_HOURS = (0, 8, 16)

# Fee assumptions for basis trades
# Round-trip fees for basis trade:
# Entry:  spot market taker 0.10% + perp limit maker 0.02% = 0.12%
# Exit:   spot market taker 0.10% + perp limit maker 0.02% = 0.12%
# Total:  0.24% of notional
FEE_RATE_BASIS = 0.0024


class BasisCaptureStrategy:
    """
    Spot-vs-perp basis capture for Binance spot + Binance Futures.
    Configured via the 'basis_pairs' section of config.yaml.
    """

    def __init__(self, cfg: dict, exchanges: dict, risk_manager,
                 alert_manager, paper_mode: bool):
        self.cfg           = cfg
        self.exchanges     = exchanges
        self.risk_manager  = risk_manager
        self.alert_manager = alert_manager
        self.paper_mode    = paper_mode

        self.basis_pairs   = [p for p in cfg.get("basis_pairs", []) if p.get("active")]
        self._open_basis   = {}   # pair_key -> trade info

        self._rehydrate()
        if self.basis_pairs:
            logger.info(
                f"BasisCaptureStrategy ready with {len(self.basis_pairs)} active basis pairs"
            )

    def _rehydrate(self):
        """Reload open basis trades from DB on restart."""
        for trade in get_open_trades():
            notes = trade.get("notes", "") or ""
            if "basis_trade" in notes:
                pair = trade["pair"]
                if pair not in self._open_basis:
                    self._open_basis[pair] = {"trade_id": trade["id"]}

    def tick(self):
        if not self.basis_pairs:
            return
        if not self.risk_manager.is_trading_allowed():
            return
        self._manage_open_basis()
        self._scan_for_basis_entries()

    # ── Entry ──────────────────────────────────────────────────────────────────

    def _scan_for_basis_entries(self):
        max_pos    = self.cfg["strategy"]["max_simultaneous_positions"]
        open_count = len(get_open_trades())
        if open_count >= max_pos:
            return

        for pair_cfg in self.basis_pairs:
            pair_key = f"{pair_cfg['spot_symbol']}_basis"
            if pair_key in self._open_basis:
                continue
            if open_count >= max_pos:
                break

            try:
                basis, spot_price, perp_price = self._get_basis(pair_cfg)
                if basis is None:
                    continue

                min_entry = pair_cfg.get("min_basis_entry", 0.0010)
                if basis < min_entry:
                    logger.info(
                        f"BASIS SCAN {pair_cfg['spot_symbol']}: "
                        f"basis={basis*100:.4f}% (need >={min_entry*100:.3f}%)"
                    )
                    continue

                funding_rate = self._get_funding_rate(
                    pair_cfg["perp_exchange"], pair_cfg["perp_symbol"]
                )

                self._enter_basis(pair_cfg, basis, spot_price, perp_price, funding_rate)
                open_count += 1

            except Exception as e:
                logger.error(f"Basis scan error {pair_cfg['spot_symbol']}: {e}", exc_info=True)

    def _enter_basis(self, pair_cfg: dict, basis: float, spot_price: float,
                     perp_price: float, funding_rate: float):
        """Buy spot on spot_exchange, short perp on perp_exchange."""
        spot_ex  = self.exchanges.get(pair_cfg["spot_exchange"])
        perp_ex  = self.exchanges.get(pair_cfg["perp_exchange"])
        if not spot_ex or not perp_ex:
            logger.warning(f"Basis entry skipped — exchange not found for {pair_cfg}")
            return

        spot_sym = pair_cfg["spot_symbol"]
        perp_sym = pair_cfg["perp_symbol"]
        pair_key = f"{spot_sym}_basis"

        # Size: configurable % of tradeable capital (default 20%).
        # Spot requires full margin (no leverage), so smaller allocation than perp strategy.
        size_pct = self.cfg["strategy"].get("basis_size_pct", 0.20)
        total    = self.cfg["capital"]["total_usdt"]
        reserve  = self.cfg["capital"]["stablecoin_reserve"]
        buffer   = self.cfg["capital"]["free_margin_buffer"]
        notional = (total - reserve) * (1 - buffer) * size_pct

        amount_spot = notional / spot_price
        amount_perp = notional / perp_price

        # Buy spot (market order — needs immediate fill)
        spot_order = spot_ex.place_order(spot_sym, "buy", amount_spot,
                                         order_type="market")
        spot_id    = spot_order.get("id", "unknown")

        # Short perp (limit order — maker fee)
        try:
            perp_order = perp_ex.place_order(perp_sym, "sell", amount_perp,
                                             order_type="limit", price=perp_price)
            perp_id    = perp_order.get("id", "unknown")
        except Exception as e:
            logger.warning(f"Basis perp leg failed ({e}) — selling spot back")
            try:
                spot_ex.place_order(spot_sym, "sell", amount_spot, order_type="market")
            except Exception as ce:
                logger.error(f"Emergency spot sell failed: {ce}")
            return

        trade_id = log_trade({
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "pair":            pair_key,
            "leg_a_exchange":  pair_cfg["spot_exchange"],
            "leg_b_exchange":  pair_cfg["perp_exchange"],
            "leg_a_symbol":    spot_sym,
            "leg_b_symbol":    perp_sym,
            "notional_usd":    notional,
            "spread_pct":      basis,
            "gross_profit":    None,
            "fees":            None,
            "net_profit":      None,
            "funding_income":  funding_rate,
            "status":          "open",
            "paper_mode":      int(self.paper_mode),
            "notes": (
                f"basis_trade entry={basis*100:.4f}% "
                f"spot_id={spot_id} perp_id={perp_id} "
                f"funding={funding_rate*100:.4f}%"
            ),
        })

        self._open_basis[pair_key] = {
            "trade_id":    trade_id,
            "entry_basis": basis,
            "spot_price":  spot_price,
            "perp_price":  perp_price,
            "amount_spot": amount_spot,
            "amount_perp": amount_perp,
        }

        logger.info(
            f"BASIS ENTERED {spot_sym}: basis={basis*100:.4f}% "
            f"funding={funding_rate*100:.4f}%/8h notional=${notional:,.0f} "
            f"| {'PAPER' if self.paper_mode else 'LIVE'}"
        )

    # ── Exit ───────────────────────────────────────────────────────────────────

    def _manage_open_basis(self):
        for trade in get_open_trades():
            notes = trade.get("notes", "") or ""
            if "basis_trade" not in notes:
                continue

            pair_key = trade["pair"]
            try:
                pair_cfg = self._find_pair_cfg(pair_key)
                if not pair_cfg:
                    continue

                basis, spot_price, perp_price = self._get_basis(pair_cfg)
                if basis is None:
                    continue

                min_exit  = pair_cfg.get("min_basis_exit", 0.0002)
                max_hold  = self.cfg["strategy"].get("max_hold_hours", 12)

                # Check hold time
                try:
                    ts = datetime.fromisoformat(trade["timestamp"])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    hold_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
                except Exception:
                    hold_h = 0

                should_exit = basis <= min_exit or hold_h >= max_hold
                if should_exit:
                    reason = "basis_converged" if basis <= min_exit else "max_hold"
                    self._close_basis(trade, pair_cfg, basis, spot_price, perp_price, reason)

            except Exception as e:
                logger.error(f"Basis manage error {pair_key}: {e}", exc_info=True)

    def _close_basis(self, trade: dict, pair_cfg: dict, current_basis: float,
                     spot_price: float, perp_price: float, reason: str):
        spot_ex  = self.exchanges.get(pair_cfg["spot_exchange"])
        perp_ex  = self.exchanges.get(pair_cfg["perp_exchange"])
        pos      = self._open_basis.get(trade["pair"], {})
        notional = trade["notional_usd"]

        amount_spot = pos.get("amount_spot", notional / spot_price) if spot_price else 0.001
        amount_perp = pos.get("amount_perp", notional / perp_price) if perp_price else 0.001

        # Sell spot (market — need immediate fill to close cleanly)
        if spot_ex:
            try:
                spot_ex.place_order(
                    pair_cfg["spot_symbol"], "sell", amount_spot, order_type="market"
                )
            except Exception as e:
                logger.error(f"Basis spot close error: {e}")

        # Buy back perp (limit → market fallback)
        if perp_ex:
            try:
                close_order = perp_ex.place_order(
                    pair_cfg["perp_symbol"], "buy", amount_perp,
                    order_type="limit", price=perp_price
                )
            except Exception as e:
                logger.warning(f"Basis perp close limit failed ({e}) — using market")
                try:
                    perp_ex.place_order(
                        pair_cfg["perp_symbol"], "buy", amount_perp, order_type="market"
                    )
                except Exception as ce:
                    logger.error(f"Basis perp market close failed: {ce}")

        entry_basis = trade.get("spread_pct") or 0.0
        gross       = (entry_basis - current_basis) * notional
        fees        = notional * FEE_RATE_BASIS

        # Funding collected = rate × notional × number of 8h settlements during hold
        try:
            ts = datetime.fromisoformat(trade["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            hold_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        except Exception:
            hold_hours = 0
        funding_rate        = float(trade.get("funding_income") or 0)
        funding_settlements = max(int(hold_hours / 8), 0)
        funding_col         = funding_rate * notional * funding_settlements

        net = gross + funding_col - fees

        update_trade(trade["id"], {
            "status":       "closed",
            "gross_profit": round(gross, 6),
            "fees":         round(fees, 6),
            "net_profit":   round(net, 6),
            "notes": (
                f"basis_trade closed:{reason} "
                f"entry={entry_basis*100:.4f}% exit={current_basis*100:.4f}%"
            ),
        })

        self._open_basis.pop(trade["pair"], None)
        logger.info(
            f"BASIS CLOSED {pair_cfg['spot_symbol']} | reason={reason} "
            f"| net=${net:.2f} (gross=${gross:.2f} funding=${funding_col:.2f} fees=${fees:.2f})"
        )

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get_basis(self, pair_cfg: dict) -> tuple[Optional[float], float, float]:
        """Return (basis_pct, spot_price, perp_price). basis = (perp - spot) / spot."""
        spot_ex  = self.exchanges.get(pair_cfg["spot_exchange"])
        perp_ex  = self.exchanges.get(pair_cfg["perp_exchange"])
        if not spot_ex or not perp_ex:
            return None, 0, 0
        try:
            spot_price = float(spot_ex.fetch_ticker(pair_cfg["spot_symbol"])["last"])
            perp_price = float(perp_ex.fetch_ticker(pair_cfg["perp_symbol"])["last"])
            if spot_price <= 0 or perp_price <= 0:
                return None, 0, 0
            basis = (perp_price - spot_price) / spot_price
            return basis, spot_price, perp_price
        except Exception as e:
            logger.debug(f"Basis fetch error: {e}")
            return None, 0, 0

    def _get_funding_rate(self, exchange_name: str, symbol: str) -> float:
        ex = self.exchanges.get(exchange_name)
        if not ex:
            return 0.0
        try:
            data = ex.fetch_funding_rate(symbol)
            return float(data.get("fundingRate", 0) or 0)
        except Exception:
            return 0.0

    def _find_pair_cfg(self, pair_key: str) -> Optional[dict]:
        spot_sym = pair_key.replace("_basis", "")
        for p in self.basis_pairs:
            if p["spot_symbol"] == spot_sym:
                return p
        return None
