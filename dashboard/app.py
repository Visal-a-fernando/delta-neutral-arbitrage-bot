"""dashboard/app.py — Read-only Flask web dashboard."""

from flask import Flask, jsonify, render_template
from utils.database import get_reports, get_open_trades, get_high_water_mark, _conn


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates")

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/summary")
    def api_summary():
        reports = get_reports(days=30)
        hwm     = get_high_water_mark()
        open_t  = get_open_trades()

        total_net   = sum(r["net_profit"]    for r in reports)
        total_gross = sum(r["gross_profit"]  for r in reports)
        total_trades = sum(r["trades_count"] for r in reports)
        latest_balance = reports[0]["account_balance"] if reports else 0

        return jsonify({
            "hwm":             round(hwm, 2),
            "total_net_30d":   round(total_net, 2),
            "total_gross_30d": round(total_gross, 2),
            "total_trades_30d": total_trades,
            "open_positions":  len(open_t),
            "latest_balance":  round(latest_balance, 2),
        })

    @app.route("/api/reports")
    def api_reports():
        return jsonify(get_reports(days=30))

    @app.route("/api/positions")
    def api_positions():
        return jsonify(get_open_trades())

    @app.route("/api/risk_events")
    def api_risk_events():
        with _conn() as con:
            events = [dict(r) for r in con.execute(
                "SELECT * FROM risk_events ORDER BY timestamp DESC LIMIT 50"
            ).fetchall()]
        return jsonify(events)

    @app.route("/api/withdrawals")
    def api_withdrawals():
        with _conn() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM withdrawals ORDER BY timestamp DESC LIMIT 100"
            ).fetchall()]
        return jsonify(rows)

    @app.route("/api/alerts")
    def api_alerts():
        with _conn() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM alerts_log ORDER BY timestamp DESC LIMIT 50"
            ).fetchall()]
        return jsonify(rows)

    return app
