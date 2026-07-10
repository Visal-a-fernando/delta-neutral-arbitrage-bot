"""
control_panel/app.py — Delta-Neutral Bot Control Panel
Standalone Flask app (port 8080). Does NOT modify any existing bot files.
Reads config.yaml and bot.db directly; writes config.yaml for settings changes.
"""

import os
import sys
import yaml
import json
import sqlite3
import shutil
import logging
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps
from collections import defaultdict
from flask import Flask, render_template, jsonify, request, session, redirect, url_for

# ── Paths (resolved relative to bot root, i.e. parent of this directory) ────
BOT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = BOT_ROOT / "config" / "config.yaml"
LOG_PATH = BOT_ROOT / "logs" / "bot.log"

app = Flask(__name__)
app.secret_key = os.environ.get("CONTROL_PANEL_SECRET", "cp-secret-delta-neutral-2026")

PANEL_PASSWORD = os.environ.get("CONTROL_PANEL_PASSWORD", "admin")
PANEL_PORT = int(os.environ.get("CONTROL_PANEL_PORT", "8080"))
INSTANCE_NAME = os.environ.get("CONTROL_PANEL_NAME", "VPS Bot")

# Restart command — both local and VPS are Windows services named DeltaBot.
# Override via env var if your service has a different name:
#   set CONTROL_PANEL_SERVICE=MyBotService
_SERVICE_NAME = os.environ.get("CONTROL_PANEL_SERVICE", "DeltaBot")
RESTART_CMD = f'powershell -Command "Restart-Service {_SERVICE_NAME}"'

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("control_panel")


# ── Config helpers ────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def save_config(cfg: dict):
    backup_dir = CONFIG_PATH.parent
    backup_name = f"config.yaml.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(CONFIG_PATH, backup_dir / backup_name)
    # Keep only the last 10 backups
    backups = sorted(backup_dir.glob("config.yaml.bak.*"))
    for old in backups[:-10]:
        old.unlink(missing_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    log.info("config.yaml saved (backup: %s)", backup_name)


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection | None:
    try:
        cfg = load_config()
        db_rel = cfg.get("database", {}).get("path", "data/bot.db")
        db_path = BOT_ROOT / db_rel
        if not db_path.exists():
            return None
        conn = sqlite3.connect(str(db_path), timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
    except Exception as e:
        log.warning("DB open failed: %s", e)
        return None


# ── Auth ──────────────────────────────────────────────────────────────────────

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == PANEL_PASSWORD:
            session["authenticated"] = True
            return redirect(url_for("index"))
        return redirect(url_for("login") + "?error=1")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@require_auth
def index():
    return render_template("panel.html")


@app.route("/api/meta/public")
def api_meta_public():
    """Unauthenticated — returns only safe display info for the login page."""
    return jsonify({"instance": INSTANCE_NAME})


# ── API: Config ───────────────────────────────────────────────────────────────

@app.route("/api/config")
@require_auth
def api_config():
    cfg = load_config()
    # Strip API key env var names (not values, just structure is fine)
    return jsonify(cfg)


@app.route("/api/pairs/<int:idx>/toggle", methods=["POST"])
@require_auth
def toggle_pair(idx: int):
    cfg = load_config()
    pairs = cfg.get("pairs", [])
    if idx < 0 or idx >= len(pairs):
        return jsonify({"error": "invalid index"}), 400
    pairs[idx]["active"] = not bool(pairs[idx].get("active", False))
    cfg["pairs"] = pairs
    save_config(cfg)
    return jsonify({"active": pairs[idx]["active"], "idx": idx})


@app.route("/api/settings", methods=["POST"])
@require_auth
def update_settings():
    data = request.get_json(force=True)
    cfg = load_config()
    strat = cfg.setdefault("strategy", {})
    risk = cfg.setdefault("risk", {})
    adaptive = strat.setdefault("adaptive_spread", {})

    mapping = {
        # strategy
        "min_spread_entry":        (strat, "min_spread_entry", float),
        "min_spread_exit":         (strat, "min_spread_exit", float),
        "max_simultaneous_positions": (strat, "max_simultaneous_positions", int),
        "max_hold_hours":          (strat, "max_hold_hours", int),
        "max_notional_per_trade":  (strat, "max_notional_per_trade", float),
        "reentry_cooldown_seconds":(strat, "reentry_cooldown_seconds", int),
        # adaptive
        "adaptive_floor":          (adaptive, "floor", float),
        "adaptive_ceiling":        (adaptive, "ceiling", float),
        "adaptive_step_up":        (adaptive, "step_up", float),
        "adaptive_step_down":      (adaptive, "step_down", float),
        # risk
        "account_stop_loss_pct":   (risk, "account_stop_loss_pct", float),
        "high_water_mark_floor":   (risk, "high_water_mark_floor", float),
        "max_margin_usage":        (risk, "max_margin_usage", float),
        "margin_alert_pct":        (risk, "margin_alert_pct", float),
        "max_daily_loss":          (risk, "max_daily_loss", float),
        "atr_spike_threshold":     (risk, "atr_spike_threshold", float),
    }

    changed = []
    for key, (target, field, cast) in mapping.items():
        if key in data and data[key] is not None:
            try:
                target[field] = cast(data[key])
                # Mirror spread entry into paper mode too
                if key == "min_spread_entry":
                    strat["paper_min_spread_entry"] = cast(data[key])
                if key == "min_spread_exit":
                    strat["paper_min_spread_exit"] = cast(data[key])
                changed.append(key)
            except (ValueError, TypeError) as e:
                return jsonify({"error": f"bad value for {key}: {e}"}), 400

    if changed:
        save_config(cfg)
    return jsonify({"ok": True, "changed": changed})


# ── API: Stats & pairs ────────────────────────────────────────────────────────

@app.route("/api/stats")
@require_auth
def api_stats():
    cfg = load_config()
    pairs = cfg.get("pairs", [])
    adaptive_cfg = cfg.get("strategy", {}).get("adaptive_spread", {})
    floor = adaptive_cfg.get("floor", 0.0018)
    ceiling = adaptive_cfg.get("ceiling", 0.0025)
    step_up = adaptive_cfg.get("step_up", 0.0005)
    step_down = adaptive_cfg.get("step_down", 0.0002)

    conn = get_db()
    if conn is None:
        # Return pairs from config only, no DB stats
        result = []
        for i, pair in enumerate(pairs):
            sym = pair.get("leg_a_symbol", "")
            result.append({
                "idx": i,
                "symbol": sym.split("/")[0] if "/" in sym else sym,
                "full_symbol": sym,
                "leg_a_exchange": pair.get("leg_a_exchange", ""),
                "leg_b_exchange": pair.get("leg_b_exchange", ""),
                "active": bool(pair.get("active", False)),
                "min_spread_entry": pair.get("min_spread_entry"),
                "total_trades": 0, "wins": 0, "win_rate": None,
                "total_pnl": 0, "total_fees": 0, "avg_spread": 0,
                "last_trade": None, "est_threshold": round(floor * 100, 3),
                "consec_losses": 0, "open_count": 0, "warnings": [],
                "recent_pnl": [],
            })
        summary = {
            "today_pnl": 0, "today_trades": 0, "week_pnl": 0, "month_pnl": 0,
            "open_positions": 0, "account_balance": None,
            "active_pairs": sum(1 for p in pairs if p.get("active")),
            "total_pairs": len(pairs),
            "db_connected": False,
        }
        return jsonify({"pairs": result, "summary": summary})

    try:
        cutoff_30d = (datetime.now() - timedelta(days=30)).isoformat()

        # Per-pair aggregates (30 days)
        rows = conn.execute("""
            SELECT leg_a_symbol, leg_a_exchange, leg_b_exchange,
                   COUNT(*) AS total_trades,
                   SUM(CASE WHEN net_profit > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(net_profit) AS total_pnl,
                   SUM(fees) AS total_fees,
                   MAX(timestamp) AS last_trade,
                   AVG(spread_pct) AS avg_spread
            FROM trades
            WHERE status = 'closed' AND timestamp > ?
            GROUP BY leg_a_symbol, leg_a_exchange, leg_b_exchange
        """, (cutoff_30d,)).fetchall()
        pair_stats = {
            f"{r['leg_a_symbol']}|{r['leg_a_exchange']}|{r['leg_b_exchange']}": dict(r)
            for r in rows
        }

        # Last 10 trades per pair for streak / threshold estimation
        all_recent = conn.execute("""
            SELECT leg_a_symbol, leg_a_exchange, leg_b_exchange, net_profit, timestamp
            FROM trades WHERE status = 'closed'
            ORDER BY timestamp DESC
        """).fetchall()
        pair_recent: dict[str, list] = defaultdict(list)
        for r in all_recent:
            key = f"{r['leg_a_symbol']}|{r['leg_a_exchange']}|{r['leg_b_exchange']}"
            if len(pair_recent[key]) < 10:
                pair_recent[key].append(r["net_profit"])

        # Open position counts
        open_rows = conn.execute("""
            SELECT leg_a_symbol, leg_a_exchange, leg_b_exchange, COUNT(*) AS cnt
            FROM trades WHERE status = 'open'
            GROUP BY leg_a_symbol, leg_a_exchange, leg_b_exchange
        """).fetchall()
        open_map = {
            f"{r['leg_a_symbol']}|{r['leg_a_exchange']}|{r['leg_b_exchange']}": r["cnt"]
            for r in open_rows
        }

        result_pairs = []
        for i, pair in enumerate(pairs):
            sym = pair.get("leg_a_symbol", "")
            exch_a = pair.get("leg_a_exchange", "")
            exch_b = pair.get("leg_b_exchange", "")
            key = f"{sym}|{exch_a}|{exch_b}"
            stats = pair_stats.get(key, {})
            recent = pair_recent.get(key, [])   # newest first

            total = stats.get("total_trades") or 0
            wins = stats.get("wins") or 0
            win_rate = round(wins / total * 100, 1) if total > 0 else None

            # Estimate current adaptive threshold by replaying last 10 trades
            est_threshold = floor
            for pnl in reversed(recent):   # oldest → newest
                if pnl < 0:
                    est_threshold = min(ceiling, est_threshold + step_up)
                else:
                    est_threshold = max(floor, est_threshold - step_down)

            # Consecutive losses (most recent first)
            consec_losses = 0
            for pnl in recent:
                if pnl < 0:
                    consec_losses += 1
                else:
                    break

            # Build warnings
            warnings = []
            if est_threshold >= ceiling:
                warnings.append("at_ceiling")
            if consec_losses >= 3:
                warnings.append(f"streak_{consec_losses}")
            pnl_val = stats.get("total_pnl") or 0
            if total >= 10 and win_rate is not None and win_rate < 40:
                warnings.append("low_win_rate")
            if total >= 5 and pnl_val < -20:
                warnings.append("negative_pnl")

            result_pairs.append({
                "idx": i,
                "symbol": sym.split("/")[0] if "/" in sym else sym,
                "full_symbol": sym,
                "leg_a_exchange": exch_a,
                "leg_b_exchange": exch_b,
                "active": bool(pair.get("active", False)),
                "min_spread_entry": pair.get("min_spread_entry"),
                "total_trades": total,
                "wins": wins,
                "win_rate": win_rate,
                "total_pnl": round(pnl_val, 2),
                "total_fees": round(stats.get("total_fees") or 0, 2),
                "avg_spread": round((stats.get("avg_spread") or 0) * 100, 3),
                "last_trade": (stats.get("last_trade") or "")[:16],
                "est_threshold": round(est_threshold * 100, 3),
                "consec_losses": consec_losses,
                "open_count": open_map.get(key, 0),
                "warnings": warnings,
                "recent_pnl": [round(p, 2) for p in recent],
            })

        # Summary stats
        today_str = datetime.now().strftime("%Y-%m-%d")
        week_cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        month_cutoff = (datetime.now() - timedelta(days=30)).isoformat()

        today_row = conn.execute("""
            SELECT COALESCE(SUM(net_profit),0) pnl, COUNT(*) cnt
            FROM trades WHERE status='closed' AND DATE(timestamp)=?
        """, (today_str,)).fetchone()

        week_row = conn.execute(
            "SELECT COALESCE(SUM(net_profit),0) pnl FROM trades WHERE status='closed' AND timestamp>?",
            (week_cutoff,)
        ).fetchone()

        month_row = conn.execute(
            "SELECT COALESCE(SUM(net_profit),0) pnl FROM trades WHERE status='closed' AND timestamp>?",
            (month_cutoff,)
        ).fetchone()

        open_total = conn.execute("SELECT COUNT(*) c FROM trades WHERE status='open'").fetchone()

        balance_row = conn.execute(
            "SELECT account_balance FROM daily_reports ORDER BY report_date DESC LIMIT 1"
        ).fetchone()

        hwm_row = conn.execute("SELECT MAX(balance) hwm FROM high_water_marks").fetchone()

        summary = {
            "today_pnl": round(today_row["pnl"], 2),
            "today_trades": today_row["cnt"],
            "week_pnl": round(week_row["pnl"], 2),
            "month_pnl": round(month_row["pnl"], 2),
            "open_positions": open_total["c"],
            "account_balance": round(balance_row["account_balance"], 2) if balance_row and balance_row["account_balance"] else None,
            "high_water_mark": round(hwm_row["hwm"], 2) if hwm_row and hwm_row["hwm"] else None,
            "active_pairs": sum(1 for p in pairs if p.get("active")),
            "total_pairs": len(pairs),
            "db_connected": True,
        }
        return jsonify({"pairs": result_pairs, "summary": summary})

    finally:
        conn.close()


# ── API: Charts ───────────────────────────────────────────────────────────────

@app.route("/api/pnl/daily")
@require_auth
def api_pnl_daily():
    days = int(request.args.get("days", 30))
    conn = get_db()
    if conn is None:
        return jsonify([])
    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = conn.execute("""
            SELECT report_date, net_profit, gross_profit, fees, trades_count, account_balance, funding_income
            FROM daily_reports WHERE report_date >= ?
            ORDER BY report_date ASC
        """, (cutoff,)).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@app.route("/api/pnl/pair")
@require_auth
def api_pnl_pair():
    days = int(request.args.get("days", 30))
    conn = get_db()
    if conn is None:
        return jsonify([])
    try:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = conn.execute("""
            SELECT leg_a_symbol, leg_a_exchange, leg_b_exchange,
                   SUM(net_profit) total_pnl, COUNT(*) trade_count,
                   SUM(CASE WHEN net_profit>0 THEN 1 ELSE 0 END) wins
            FROM trades WHERE status='closed' AND timestamp>?
            GROUP BY leg_a_symbol, leg_a_exchange, leg_b_exchange
            ORDER BY total_pnl DESC
        """, (cutoff,)).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


# ── API: Positions ────────────────────────────────────────────────────────────

@app.route("/api/positions")
@require_auth
def api_positions():
    conn = get_db()
    if conn is None:
        return jsonify([])
    try:
        rows = conn.execute("""
            SELECT id, leg_a_symbol, leg_a_exchange, leg_b_exchange,
                   entry_price_a, entry_price_b, amount_a, amount_b,
                   spread_pct, notional_usd, timestamp AS opened_at, paper_mode
            FROM trades WHERE status='open'
            ORDER BY timestamp DESC
        """).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            # Estimate hold duration
            try:
                opened = datetime.fromisoformat(d["opened_at"])
                hours = (datetime.now() - opened).total_seconds() / 3600
                d["hold_hours"] = round(hours, 1)
            except Exception:
                d["hold_hours"] = None
            result.append(d)
        return jsonify(result)
    finally:
        conn.close()


# ── API: Warnings ─────────────────────────────────────────────────────────────

@app.route("/api/warnings")
@require_auth
def api_warnings():
    conn = get_db()
    warnings = []
    if conn is None:
        return jsonify([{"type": "no_db", "severity": "medium",
                         "message": "Database not found — bot may not be running", "time": ""}])
    try:
        cfg = load_config()
        risk = cfg.get("risk", {})

        # Recent risk events (last 24h)
        cutoff_24h = (datetime.now() - timedelta(hours=24)).isoformat()
        risk_rows = conn.execute("""
            SELECT event_type, description, timestamp
            FROM risk_events WHERE timestamp > ?
            ORDER BY timestamp DESC LIMIT 10
        """, (cutoff_24h,)).fetchall()
        for row in risk_rows:
            evt = row["event_type"]
            severity = "high" if any(x in evt for x in ("stop_loss", "hwm_floor", "kill")) else "medium"
            warnings.append({
                "type": "risk_event",
                "severity": severity,
                "message": f"[{evt}] {row['description'] or ''}",
                "time": (row["timestamp"] or "")[:16],
            })

        # Today's loss approaching daily limit
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_row = conn.execute(
            "SELECT COALESCE(SUM(net_profit),0) pnl FROM trades WHERE status='closed' AND DATE(timestamp)=?",
            (today_str,)
        ).fetchone()
        max_loss = risk.get("max_daily_loss", 50)
        today_pnl = today_row["pnl"] if today_row else 0
        if today_pnl < -(max_loss * 0.6):
            pct = abs(today_pnl) / max_loss * 100
            warnings.append({
                "type": "daily_loss",
                "severity": "high" if pct > 80 else "medium",
                "message": f"Daily loss ${abs(today_pnl):.2f} = {pct:.0f}% of ${max_loss} limit",
                "time": today_str,
            })

        # Positions held > 8 hours
        cutoff_8h = (datetime.now() - timedelta(hours=8)).isoformat()
        long_held = conn.execute(
            "SELECT leg_a_symbol, leg_a_exchange, leg_b_exchange, timestamp FROM trades WHERE status='open' AND timestamp < ?",
            (cutoff_8h,)
        ).fetchall()
        for row in long_held:
            try:
                hours = (datetime.now() - datetime.fromisoformat(row["timestamp"])).total_seconds() / 3600
            except Exception:
                hours = 0
            warnings.append({
                "type": "long_hold",
                "severity": "medium",
                "message": f"{row['leg_a_symbol']} held {hours:.1f}h ({row['leg_a_exchange']}/{row['leg_b_exchange']})",
                "time": (row["timestamp"] or "")[:16],
            })

        # Inactivity check — no trades in last 4h during likely market hours
        inactivity_hours = cfg.get("alerts", {}).get("inactivity_alert_hours", 4)
        cutoff_inact = (datetime.now() - timedelta(hours=inactivity_hours)).isoformat()
        last_trade = conn.execute(
            "SELECT MAX(timestamp) ts FROM trades WHERE timestamp > ?", (cutoff_inact,)
        ).fetchone()
        if not last_trade["ts"]:
            warnings.append({
                "type": "inactivity",
                "severity": "low",
                "message": f"No trades in the last {inactivity_hours}h",
                "time": datetime.now().isoformat()[:16],
            })

        return jsonify(warnings)
    finally:
        conn.close()


# ── API: Logs ────────────────────────────────────────────────────────────────

@app.route("/api/logs")
@require_auth
def api_logs():
    n = int(request.args.get("n", 150))
    if not LOG_PATH.exists():
        return jsonify(["[log file not found — bot may not have started yet]"])
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return jsonify([l.rstrip() for l in lines[-n:]])
    except Exception as e:
        return jsonify([f"[error reading log: {e}]"])


# ── API: Restart ─────────────────────────────────────────────────────────────

@app.route("/api/restart", methods=["POST"])
@require_auth
def api_restart():
    import subprocess, shlex
    log.info("Restart requested by panel user. Command: %s", RESTART_CMD)
    try:
        result = subprocess.run(
            RESTART_CMD,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            log.info("Restart succeeded")
            return jsonify({"ok": True, "output": result.stdout.strip() or "Service restarted."})
        else:
            err = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            log.warning("Restart failed (rc=%d): %s", result.returncode, err)
            return jsonify({"ok": False, "error": err}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Command timed out after 30s"}), 500
    except Exception as e:
        log.exception("Restart exception")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── API: Meta ────────────────────────────────────────────────────────────────

@app.route("/api/meta")
@require_auth
def api_meta():
    conn = get_db()
    db_ok = conn is not None
    if conn:
        conn.close()
    return jsonify({
        "instance": INSTANCE_NAME,
        "config_path": str(CONFIG_PATH),
        "db_connected": db_ok,
        "server_time": datetime.now().isoformat()[:19],
        "bot_root": str(BOT_ROOT),
    })


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"╔══════════════════════════════════════════╗")
    print(f"║  Delta-Neutral Bot — Control Panel       ║")
    print(f"║  Instance : {INSTANCE_NAME:<30}║")
    print(f"║  URL      : http://0.0.0.0:{PANEL_PORT:<14}║")
    print(f"║  Password : {PANEL_PASSWORD:<30}║")
    print(f"╚══════════════════════════════════════════╝")
    print(f"  Config : {CONFIG_PATH}")
    print(f"  Bot root: {BOT_ROOT}")
    app.run(host="0.0.0.0", port=PANEL_PORT, debug=False, threaded=True)
