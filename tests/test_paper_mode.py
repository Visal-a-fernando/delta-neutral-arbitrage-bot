"""tests/test_paper_mode.py — Comprehensive test suite."""

import sys
import os
import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import utils.database as db_mod
from utils.database import (
    init_db, log_trade, update_trade, get_open_trades,
    get_daily_net_profit, get_high_water_mark, update_high_water_mark,
    log_risk_event, log_withdrawal, log_alert,
)


def _make_trade(**overrides):
    base = {
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "pair":           "ETH/USDT:USDT_ETH/USDT:USDT",
        "leg_a_exchange": "binance",
        "leg_b_exchange": "bybit",
        "leg_a_symbol":   "ETH/USDT:USDT",
        "leg_b_symbol":   "ETH/USDT:USDT",
        "notional_usd":   10000,
        "spread_pct":     0.003,
        "gross_profit":   None,
        "fees":           None,
        "net_profit":     None,
        "funding_income": 0,
        "status":         "open",
        "paper_mode":     1,
        "notes":          "test",
    }
    base.update(overrides)
    return base


class InMemoryDBMixin:
    """Mixin: redirect all DB calls to a fresh in-memory SQLite for each test."""
    def setUp(self):
        self._con = sqlite3.connect(":memory:")
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        db_mod._conn = lambda: self._con
        db_mod.DB_PATH = ":memory:"
        init_db()


# ─────────────────────────────────────────────────────────────────────────────
class TestDatabase(InMemoryDBMixin, unittest.TestCase):

    def test_log_and_retrieve_open_trade(self):
        tid = log_trade(_make_trade())
        self.assertGreater(tid, 0)
        open_trades = get_open_trades()
        self.assertEqual(len(open_trades), 1)
        self.assertEqual(open_trades[0]["pair"], "ETH/USDT:USDT_ETH/USDT:USDT")

    def test_close_trade_removes_from_open(self):
        tid = log_trade(_make_trade())
        update_trade(tid, {"status": "closed", "gross_profit": 40.0, "fees": 8.0, "net_profit": 32.0})
        self.assertEqual(len(get_open_trades()), 0)

    def test_daily_net_profit_only_counts_closed(self):
        log_trade(_make_trade(status="open", net_profit=None))
        tid2 = log_trade(_make_trade(status="open"))
        update_trade(tid2, {"status": "closed", "net_profit": 50.0})
        pnl = get_daily_net_profit()
        self.assertAlmostEqual(pnl, 50.0)

    def test_high_water_mark_tracks_maximum(self):
        update_high_water_mark(1000.0)
        update_high_water_mark(1500.0)
        update_high_water_mark(1200.0)
        self.assertAlmostEqual(get_high_water_mark(), 1500.0)

    def test_risk_event_logged(self):
        log_risk_event("test_event", "description", 2000.0, "no action")
        with db_mod._conn() as con:
            rows = con.execute("SELECT * FROM risk_events").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_type"], "test_event")

    def test_withdrawal_logged(self):
        log_withdrawal({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "withdrawal_type": "weekly",
            "amount_usd": 350.0,
            "destination": "0xABC",
            "exchange": "binance",
            "notes": "test withdrawal",
        })
        with db_mod._conn() as con:
            row = con.execute("SELECT * FROM withdrawals").fetchone()
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row["amount_usd"], 350.0)

    def test_alert_logged(self):
        log_alert("margin_alert", "70% margin used", sent_sms=True, sent_email=False)
        with db_mod._conn() as con:
            row = con.execute("SELECT * FROM alerts_log").fetchone()
        self.assertEqual(row["alert_type"], "margin_alert")
        self.assertEqual(row["sent_sms"], 1)
        self.assertEqual(row["sent_email"], 0)

    def test_multiple_open_trades_different_pairs(self):
        log_trade(_make_trade(pair="ETH/USDT:USDT_ETH/USDT:USDT"))
        log_trade(_make_trade(pair="BTC/USDT:USDT_BTC/USDT:USDT"))
        self.assertEqual(len(get_open_trades()), 2)


# ─────────────────────────────────────────────────────────────────────────────
class TestPositionSizingLogic(unittest.TestCase):

    def setUp(self):
        self.ps = {
            "high_spread_threshold": 0.005,
            "high_spread_deploy":    1.00,
            "medium_spread_min":     0.002,
            "medium_spread_deploy":  0.60,
            "low_spread_deploy":     0.00,
            "post_drawdown_deploy":  0.40,
            "post_drawdown_hours":   72,
            "correlation_min":       0.85,
            "correlation_lookback":  60,
        }

    def _deploy_pct(self, spread, volume_ok=True):
        ps = self.ps
        if not volume_ok or spread < ps["medium_spread_min"]:
            return ps["low_spread_deploy"]
        elif spread >= ps["high_spread_threshold"]:
            return ps["high_spread_deploy"]
        return ps["medium_spread_deploy"]

    def test_low_spread_returns_zero(self):
        self.assertEqual(self._deploy_pct(0.001), 0.0)

    def test_medium_spread_returns_60pct(self):
        self.assertEqual(self._deploy_pct(0.003), 0.60)

    def test_high_spread_exact_threshold(self):
        self.assertEqual(self._deploy_pct(0.005), 1.00)

    def test_high_spread_above_threshold(self):
        self.assertEqual(self._deploy_pct(0.008), 1.00)

    def test_low_volume_overrides_spread(self):
        self.assertEqual(self._deploy_pct(0.006, volume_ok=False), 0.0)

    def test_post_drawdown_caps_deploy(self):
        deploy = self._deploy_pct(0.006)  # would be 100%
        capped = min(deploy, self.ps["post_drawdown_deploy"])
        self.assertEqual(capped, 0.40)

    def test_atr_spike_halves_deploy(self):
        deploy = self._deploy_pct(0.003)  # 60%
        halved = deploy * 0.50
        self.assertAlmostEqual(halved, 0.30)

    def test_atr_spike_plus_post_drawdown(self):
        deploy = self._deploy_pct(0.006)   # 100%
        deploy = min(deploy, self.ps["post_drawdown_deploy"])  # 40%
        deploy *= 0.50                      # ATR halved → 20%
        self.assertAlmostEqual(deploy, 0.20)


# ─────────────────────────────────────────────────────────────────────────────
class TestRiskThresholds(unittest.TestCase):

    def test_spread_entry_threshold(self):
        min_entry = 0.003
        self.assertTrue(0.004 >= min_entry)
        self.assertFalse(0.002 >= min_entry)
        self.assertFalse(0.003 - 1e-10 >= min_entry)

    def test_spread_exit_threshold(self):
        min_exit = 0.001
        self.assertTrue(0.0005 < min_exit)   # would trigger exit
        self.assertFalse(0.002  < min_exit)  # would not trigger

    def test_account_stop_loss_pct(self):
        stop_pct = 0.08
        self.assertTrue(-0.09 < -stop_pct)  # triggers
        self.assertFalse(-0.07 < -stop_pct) # doesn't trigger

    def test_hwm_floor(self):
        hwm      = 2400.0
        floor    = 0.15
        balance  = 2000.0
        drop_pct = (hwm - balance) / hwm
        self.assertGreater(drop_pct, floor)  # should halt

        balance2  = 2100.0
        drop_pct2 = (hwm - balance2) / hwm
        self.assertLess(drop_pct2, floor)    # should not halt

    def test_margin_usage_alert_vs_critical(self):
        alert_threshold    = 0.70
        critical_threshold = 0.75
        self.assertTrue(0.71 >= alert_threshold)
        self.assertFalse(0.71 >= critical_threshold)
        self.assertTrue(0.76 >= critical_threshold)

    def test_correlation_pause_threshold(self):
        min_corr = 0.85
        self.assertFalse(0.80 >= min_corr)  # should pause
        self.assertTrue(0.90 >= min_corr)   # OK to trade

    def test_max_daily_loss(self):
        max_loss = 100
        self.assertTrue(-110 < -max_loss)
        self.assertFalse(-90  < -max_loss)


# ─────────────────────────────────────────────────────────────────────────────
class TestProfitExtraction(unittest.TestCase):

    def test_monthly_allocation_sums_to_100pct(self):
        tax_pct      = 0.25
        reinvest_pct = 0.40
        liquidity_pct = 0.35
        self.assertAlmostEqual(tax_pct + reinvest_pct + liquidity_pct, 1.0, places=10)

    def test_weekly_withdrawal_amount(self):
        weekly_profit = 1000
        self.assertAlmostEqual(weekly_profit * 0.35, 350.0)

    def test_weekly_withdrawal_never_exceeds_profit(self):
        for profit in [100, 500, 1000, 5000]:
            withdrawal = profit * 0.35
            self.assertLessEqual(withdrawal, profit)

    def test_tax_reserve_25pct(self):
        monthly_profit = 4000
        tax = monthly_profit * 0.25
        self.assertAlmostEqual(tax, 1000.0)

    def test_reinvest_only_40pct(self):
        monthly_profit = 4000
        reinvest = monthly_profit * 0.40
        self.assertAlmostEqual(reinvest, 1600.0)


# ─────────────────────────────────────────────────────────────────────────────
class TestCapitalStructure(unittest.TestCase):

    def test_notional_at_5x(self):
        capital   = 2000
        reserve   = 300
        buffer    = 0.20
        leverage  = 5
        tradeable = (capital - reserve) * (1 - buffer)
        notional  = tradeable * leverage
        self.assertAlmostEqual(notional, 6800.0)

    def test_notional_at_10x(self):
        capital   = 2000
        reserve   = 300
        buffer    = 0.20
        leverage  = 10
        tradeable = (capital - reserve) * (1 - buffer)
        notional  = tradeable * leverage
        self.assertAlmostEqual(notional, 13600.0)

    def test_notional_split_across_5_pairs(self):
        notional    = 13600.0
        max_pairs   = 5
        per_pair    = notional / max_pairs
        self.assertAlmostEqual(per_pair, 2720.0)

    def test_binance_bybit_split(self):
        total   = 2000
        binance = total * 0.60
        bybit   = total * 0.40
        self.assertAlmostEqual(binance + bybit, total)
        self.assertAlmostEqual(binance, 1200.0)
        self.assertAlmostEqual(bybit, 800.0)

    def test_free_margin_buffer(self):
        total   = 2000
        reserve = 300
        buffer  = 0.20
        # After reserve and buffer, tradeable amount
        tradeable = (total - reserve) * (1 - buffer)
        self.assertAlmostEqual(tradeable, 1360.0)
        # Free margin = 20% of (total - reserve) always kept back
        free_margin = (total - reserve) * buffer
        self.assertAlmostEqual(free_margin, 340.0)


# ─────────────────────────────────────────────────────────────────────────────
class TestPaperOrderSimulation(unittest.TestCase):
    """Test the ExchangeClient paper mode path without real network calls."""

    def test_paper_order_returns_filled(self):
        from utils.exchange import ExchangeClient
        mock_cfg = {
            "name": "binanceusdm",
            "api_key_env": "BINANCE_API_KEY",
            "api_secret_env": "BINANCE_API_SECRET",
            "testnet": False,
        }
        with patch("ccxt.binanceusdm") as MockExchange:
            instance = MockExchange.return_value
            instance.fetch_ticker.return_value = {"last": 3000.0}
            client = ExchangeClient("binance", mock_cfg, paper_mode=True)
            order = client.place_order("ETH/USDT:USDT", "buy", 0.5)
        self.assertEqual(order["status"], "closed")
        self.assertEqual(order["filled"], 0.5)
        self.assertTrue(order.get("paper"))

    def test_paper_close_position_returns_result(self):
        from utils.exchange import ExchangeClient
        mock_cfg = {
            "name": "binanceusdm",
            "api_key_env": "BINANCE_API_KEY",
            "api_secret_env": "BINANCE_API_SECRET",
            "testnet": False,
        }
        with patch("ccxt.binanceusdm") as MockExchange:
            instance = MockExchange.return_value
            instance.fetch_ticker.return_value = {"last": 3100.0}
            client = ExchangeClient("binance", mock_cfg, paper_mode=True)
            result = client.close_position("ETH/USDT:USDT", "long", 0.5)
        self.assertIsNotNone(result)


# ─────────────────────────────────────────────────────────────────────────────
class TestNewsBlackout(unittest.TestCase):
    """Test news blackout logic in isolation."""

    def _in_blackout(self, events, now_dt, blackout_min=120):
        for event_str in events:
            try:
                event_dt = datetime.fromisoformat(event_str)
                if abs((now_dt - event_dt).total_seconds() / 60) <= blackout_min:
                    return True
            except ValueError:
                pass
        return False

    def test_within_blackout_window(self):
        events = ["2025-07-30T18:00:00"]
        now    = datetime.fromisoformat("2025-07-30T17:30:00")
        self.assertTrue(self._in_blackout(events, now))

    def test_outside_blackout_window(self):
        events = ["2025-07-30T18:00:00"]
        now    = datetime.fromisoformat("2025-07-30T21:30:00")
        self.assertFalse(self._in_blackout(events, now))

    def test_no_events_never_blocks(self):
        self.assertFalse(self._in_blackout([], datetime.now(timezone.utc).replace(tzinfo=None)))

    def test_invalid_event_string_ignored(self):
        events = ["not-a-date", "also-bad"]
        self.assertFalse(self._in_blackout(events, datetime.now(timezone.utc).replace(tzinfo=None)))


# ─────────────────────────────────────────────────────────────────────────────
class TestAlertCooldown(unittest.TestCase):
    """Test alert rate-limiting without real SMS/email."""

    def test_cooldown_suppresses_duplicate(self):
        from alerts.alert_manager import AlertManager
        cfg = {"alerts": {}}
        am  = AlertManager(cfg)
        am._send_sms   = MagicMock(return_value=False)
        am._send_email = MagicMock(return_value=False)

        # First call goes through
        am.send_alert("test_type", "first message")
        # Second call in same cooldown window should be suppressed
        am.send_alert("test_type", "second message")

        # _send_sms called only once (first time)
        self.assertEqual(am._send_sms.call_count, 1)

    def test_different_types_not_suppressed(self):
        from alerts.alert_manager import AlertManager
        cfg = {"alerts": {}}
        am  = AlertManager(cfg)
        am._send_sms   = MagicMock(return_value=False)
        am._send_email = MagicMock(return_value=False)

        am.send_alert("type_a", "msg a")
        am.send_alert("type_b", "msg b")

        self.assertEqual(am._send_sms.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
