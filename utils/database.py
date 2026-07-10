"""utils/database.py — SQLite schema, init, and helper functions."""

import sqlite3
import logging
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "bot.db")


def _conn() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db():
    """Create all tables if they don't exist."""
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT    NOT NULL,
            pair          TEXT    NOT NULL,
            leg_a_exchange TEXT   NOT NULL,
            leg_b_exchange TEXT   NOT NULL,
            leg_a_symbol  TEXT    NOT NULL,
            leg_b_symbol  TEXT    NOT NULL,
            notional_usd  REAL    NOT NULL,
            spread_pct    REAL,
            gross_profit  REAL,
            fees          REAL,
            net_profit    REAL,
            funding_income REAL   DEFAULT 0,
            status        TEXT    DEFAULT 'open',   -- open / closed / cancelled
            paper_mode    INTEGER DEFAULT 0,
            amount_a      REAL    DEFAULT 0,
            amount_b      REAL    DEFAULT 0,
            notes         TEXT
        );

        CREATE TABLE IF NOT EXISTS risk_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            event_type  TEXT NOT NULL,   -- stop_loss / hwm_floor / margin_alert / correlation / atr_spike
            description TEXT,
            account_balance REAL,
            action_taken TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_reports (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date     TEXT UNIQUE NOT NULL,
            trades_count    INTEGER DEFAULT 0,
            gross_profit    REAL    DEFAULT 0,
            fees            REAL    DEFAULT 0,
            net_profit      REAL    DEFAULT 0,
            funding_income  REAL    DEFAULT 0,
            avg_spread_pct  REAL,
            account_balance REAL,
            margin_usage_pct REAL,
            paper_mode      INTEGER DEFAULT 0,
            created_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS withdrawals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,
            withdrawal_type TEXT NOT NULL,   -- weekly / monthly_tax / monthly_reinvest
            amount_usd      REAL NOT NULL,
            destination     TEXT,
            exchange        TEXT,
            notes           TEXT
        );

        CREATE TABLE IF NOT EXISTS alerts_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            alert_type  TEXT NOT NULL,
            message     TEXT,
            sent_sms    INTEGER DEFAULT 0,
            sent_email  INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS high_water_marks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            balance     REAL NOT NULL
        );
        """)
    # ── Migrations: add columns that may be missing from older DB files ──────
    # SQLite has no "ADD COLUMN IF NOT EXISTS" — we catch the error instead.
    _migrations = [
        "ALTER TABLE trades ADD COLUMN amount_a REAL DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN amount_b REAL DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN entry_price_a REAL DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN entry_price_b REAL DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN direction TEXT DEFAULT 'buy_a_sell_b'",
    ]
    with _conn() as con:
        for sql in _migrations:
            try:
                con.execute(sql)
            except sqlite3.OperationalError:
                pass   # column already exists — safe to ignore

    logger.info("Database initialised")


# ── Trade helpers ─────────────────────────────────────────────────────────────

def log_trade(trade: dict) -> int:
    with _conn() as con:
        cur = con.execute("""
            INSERT INTO trades (timestamp, pair, leg_a_exchange, leg_b_exchange,
                leg_a_symbol, leg_b_symbol, notional_usd, spread_pct,
                gross_profit, fees, net_profit, funding_income, status, paper_mode,
                amount_a, amount_b, entry_price_a, entry_price_b, direction, notes)
            VALUES (:timestamp, :pair, :leg_a_exchange, :leg_b_exchange,
                :leg_a_symbol, :leg_b_symbol, :notional_usd, :spread_pct,
                :gross_profit, :fees, :net_profit, :funding_income, :status, :paper_mode,
                :amount_a, :amount_b, :entry_price_a, :entry_price_b, :direction, :notes)
        """, trade)
        return cur.lastrowid


def update_trade(trade_id: int, updates: dict):
    fields = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = trade_id
    with _conn() as con:
        con.execute(f"UPDATE trades SET {fields} WHERE id = :id", updates)


def get_open_trades() -> list:
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM trades WHERE status = 'open' ORDER BY timestamp"
        ).fetchall()]


def get_daily_net_profit(for_date: Optional[date] = None) -> float:
    d = (for_date or date.today()).isoformat()
    with _conn() as con:
        row = con.execute(
            "SELECT COALESCE(SUM(net_profit), 0) FROM trades WHERE DATE(timestamp) = ? AND status = 'closed'",
            (d,)
        ).fetchone()
        return row[0]


# ── Risk event helpers ────────────────────────────────────────────────────────

def log_risk_event(event_type: str, description: str, balance: float, action: str):
    with _conn() as con:
        con.execute("""
            INSERT INTO risk_events (timestamp, event_type, description, account_balance, action_taken)
            VALUES (?, ?, ?, ?, ?)
        """, (datetime.now(timezone.utc).isoformat(), event_type, description, balance, action))


# ── High-water mark helpers ───────────────────────────────────────────────────

def update_high_water_mark(balance: float):
    with _conn() as con:
        con.execute(
            "INSERT INTO high_water_marks (timestamp, balance) VALUES (?, ?)",
            (datetime.now(timezone.utc).isoformat(), balance)
        )


def get_high_water_mark() -> float:
    with _conn() as con:
        row = con.execute("SELECT MAX(balance) FROM high_water_marks").fetchone()
        return row[0] or 0.0


# ── Daily report helpers ──────────────────────────────────────────────────────

def save_daily_report(report: dict):
    with _conn() as con:
        con.execute("""
            INSERT OR REPLACE INTO daily_reports
                (report_date, trades_count, gross_profit, fees, net_profit,
                 funding_income, avg_spread_pct, account_balance, margin_usage_pct,
                 paper_mode, created_at)
            VALUES
                (:report_date, :trades_count, :gross_profit, :fees, :net_profit,
                 :funding_income, :avg_spread_pct, :account_balance, :margin_usage_pct,
                 :paper_mode, :created_at)
        """, report)


def get_reports(days: int = 30) -> list:
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM daily_reports ORDER BY report_date DESC LIMIT ?", (days,)
        ).fetchall()]


# ── Withdrawal helpers ────────────────────────────────────────────────────────

def log_withdrawal(withdrawal: dict):
    with _conn() as con:
        con.execute("""
            INSERT INTO withdrawals (timestamp, withdrawal_type, amount_usd, destination, exchange, notes)
            VALUES (:timestamp, :withdrawal_type, :amount_usd, :destination, :exchange, :notes)
        """, withdrawal)


def get_weekly_net_profit() -> float:
    with _conn() as con:
        row = con.execute("""
            SELECT COALESCE(SUM(net_profit), 0) FROM trades
            WHERE status = 'closed'
              AND DATE(timestamp) >= DATE('now', '-7 days')
        """).fetchone()
        return row[0]


def get_monthly_net_profit() -> float:
    with _conn() as con:
        row = con.execute("""
            SELECT COALESCE(SUM(net_profit), 0) FROM trades
            WHERE status = 'closed'
              AND strftime('%Y-%m', timestamp) = strftime('%Y-%m', 'now')
        """).fetchone()
        return row[0]


# ── Alert log helpers ─────────────────────────────────────────────────────────

def log_alert(alert_type: str, message: str, sent_sms: bool = False, sent_email: bool = False):
    with _conn() as con:
        con.execute("""
            INSERT INTO alerts_log (timestamp, alert_type, message, sent_sms, sent_email)
            VALUES (?, ?, ?, ?, ?)
        """, (datetime.now(timezone.utc).isoformat(), alert_type, message, int(sent_sms), int(sent_email)))
