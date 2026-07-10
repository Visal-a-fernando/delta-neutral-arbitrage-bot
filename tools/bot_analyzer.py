"""
Delta-Neutral Bot — Local Analyzer
==================================
Streamlit dashboard for analyzing bot.db (and optionally bot.log) offline.
Run locally — does not connect to the bot or the VPS.

Usage:
    pip install streamlit pandas plotly
    streamlit run bot_analyzer.py

Then drag-drop your bot.db file in the sidebar.
"""

import io
import re
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Delta-Neutral Bot Analyzer", layout="wide")
st.title("Delta-Neutral Bot — Analyzer")
st.caption("Offline analytics for bot.db and bot.log. Nothing is uploaded anywhere.")


# ── Helpers ───────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_db(file_bytes: bytes) -> dict:
    """Save uploaded DB to temp file, read all tables into DataFrames."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.write(file_bytes)
    tmp.close()
    con = sqlite3.connect(tmp.name)
    tables = {}
    for tbl in ["trades", "daily_reports", "risk_events",
                "high_water_marks", "alerts_log", "withdrawals"]:
        try:
            tables[tbl] = pd.read_sql_query(f"SELECT * FROM {tbl}", con)
        except Exception:
            tables[tbl] = pd.DataFrame()
    con.close()
    return tables


def fmt_money(x: float) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.2f}"


def kpi(col, label, value, delta=None):
    col.metric(label, value, delta)


# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.header("Data sources")
db_file = st.sidebar.file_uploader("Upload bot.db — Bot A (e.g. VPS)", type=["db", "sqlite"])
log_file = st.sidebar.file_uploader("Upload bot.log (optional)", type=["log", "txt"])

st.sidebar.markdown("---")
st.sidebar.markdown("**Compare with second bot** (optional)")
db_file_b = st.sidebar.file_uploader("Upload bot.db — Bot B (e.g. local)", type=["db", "sqlite"])
label_a = st.sidebar.text_input("Bot A label", "Bot A (VPS)")
label_b = st.sidebar.text_input("Bot B label", "Bot B (Local)")

st.sidebar.markdown("---")
mode_filter = st.sidebar.radio("Mode filter", ["All", "Live only", "Paper only"], index=0)


if db_file is None:
    st.info("👈 Upload your `bot.db` to begin. Optionally also upload `bot.log` for log analysis, "
            "or a second DB to compare two bots side-by-side.")
    st.stop()

def prep(file_bytes):
    """Load + clean a DB into trades/closed/open/reports/risk/hwm dataframes."""
    t = load_db(file_bytes)
    tr = t["trades"].copy()
    if not tr.empty and "paper_mode" in tr.columns:
        if mode_filter == "Live only":
            tr = tr[tr["paper_mode"] == 0]
        elif mode_filter == "Paper only":
            tr = tr[tr["paper_mode"] == 1]
    if not tr.empty:
        tr["timestamp"] = pd.to_datetime(tr["timestamp"], errors="coerce", utc=True)
        tr = tr.sort_values("timestamp")
        cl = tr[tr["status"] == "closed"].copy()
        op = tr[tr["status"] == "open"].copy()
    else:
        cl, op = pd.DataFrame(), pd.DataFrame()
    return {
        "trades": tr, "closed": cl, "open": op,
        "reports": t["daily_reports"].copy(),
        "risk": t["risk_events"].copy(),
        "hwm": t["high_water_marks"].copy(),
    }


A = prep(db_file.getvalue())
B = prep(db_file_b.getvalue()) if db_file_b is not None else None

trades = A["trades"]
closed = A["closed"]
open_t = A["open"]
reports = A["reports"]
risk_events = A["risk"]
hwm_log = A["hwm"]


# ── Top-level KPIs ────────────────────────────────────────────────────────────
st.subheader("Overview")
c1, c2, c3, c4, c5, c6 = st.columns(6)

if not closed.empty:
    n_trades = len(closed)
    net = closed["net_profit"].fillna(0).sum()
    gross = closed["gross_profit"].fillna(0).sum()
    fees = closed["fees"].fillna(0).sum()
    wins = (closed["net_profit"] > 0).sum()
    win_rate = (wins / n_trades * 100) if n_trades else 0
    avg_pnl = closed["net_profit"].mean()
    hwm = hwm_log["balance"].max() if not hwm_log.empty else 0

    kpi(c1, "Closed trades", f"{n_trades:,}")
    kpi(c2, "Net P&L", fmt_money(net))
    kpi(c3, "Gross P&L", fmt_money(gross))
    kpi(c4, "Fees paid", fmt_money(fees))
    kpi(c5, "Win rate", f"{win_rate:.1f}%")
    kpi(c6, "HWM", fmt_money(hwm))
    c1, c2, c3 = st.columns(3)
    kpi(c1, "Avg net / trade", fmt_money(avg_pnl))
    kpi(c2, "Open positions", f"{len(open_t)}")
    kpi(c3, "Avg spread", f"{closed['spread_pct'].mean()*100:.3f}%")
else:
    st.warning("No closed trades found in this DB.")


# ── Cumulative P&L ────────────────────────────────────────────────────────────
if not closed.empty:
    st.subheader("Cumulative net P&L")
    cum = closed[["timestamp", "net_profit"]].copy()
    cum["cum_net"] = cum["net_profit"].fillna(0).cumsum()
    fig = px.area(cum, x="timestamp", y="cum_net", labels={"cum_net": "Cumulative net P&L ($)"})
    fig.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)


# ── Daily / Hourly P&L ────────────────────────────────────────────────────────
if not closed.empty:
    closed["date"] = closed["timestamp"].dt.date
    closed["hour"] = closed["timestamp"].dt.hour
    closed["dow"] = closed["timestamp"].dt.day_name()

    col_l, col_r = st.columns(2)

    daily = closed.groupby("date").agg(
        net=("net_profit", "sum"),
        trades=("id", "count")
    ).reset_index()
    fig_d = px.bar(daily, x="date", y="net", color=daily["net"] >= 0,
                   color_discrete_map={True: "#22c55e", False: "#ef4444"},
                   labels={"net": "Net P&L ($)"}, title="Daily net P&L")
    fig_d.update_layout(showlegend=False, height=320, margin=dict(l=10, r=10, t=40, b=10))
    col_l.plotly_chart(fig_d, use_container_width=True)

    hourly = closed.groupby("hour").agg(
        net=("net_profit", "sum"),
        trades=("id", "count"),
        avg=("net_profit", "mean"),
    ).reset_index()
    fig_h = px.bar(hourly, x="hour", y="net", title="P&L by hour-of-day (UTC)",
                   labels={"hour": "Hour (UTC)", "net": "Net P&L ($)"})
    fig_h.update_layout(height=320, margin=dict(l=10, r=10, t=40, b=10))
    col_r.plotly_chart(fig_h, use_container_width=True)

    # Day-of-week × Hour heatmap
    if len(closed) > 20:
        st.subheader("Trade-count heatmap (UTC)")
        heat = closed.groupby(["dow", "hour"]).size().reset_index(name="count")
        dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday",
                     "Friday", "Saturday", "Sunday"]
        heat["dow"] = pd.Categorical(heat["dow"], categories=dow_order, ordered=True)
        pivot = heat.pivot(index="dow", columns="hour", values="count").fillna(0)
        fig_heat = px.imshow(pivot, aspect="auto",
                             labels=dict(x="Hour (UTC)", y="Day", color="Trades"),
                             color_continuous_scale="Blues")
        fig_heat.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig_heat, use_container_width=True)


# ── Per-pair breakdown ────────────────────────────────────────────────────────
if not closed.empty:
    st.subheader("Per-pair performance")
    grp = closed.groupby("pair").agg(
        trades=("id", "count"),
        net=("net_profit", "sum"),
        gross=("gross_profit", "sum"),
        fees=("fees", "sum"),
        avg_net=("net_profit", "mean"),
        avg_spread=("spread_pct", "mean"),
        wins=("net_profit", lambda s: (s > 0).sum()),
    ).reset_index()
    grp["win_rate_%"] = (grp["wins"] / grp["trades"] * 100).round(1)
    grp["avg_spread_%"] = (grp["avg_spread"] * 100).round(4)
    grp = grp.drop(columns=["wins", "avg_spread"])
    grp = grp.sort_values("net", ascending=False)
    grp["net"] = grp["net"].round(2)
    grp["gross"] = grp["gross"].round(2)
    grp["fees"] = grp["fees"].round(2)
    grp["avg_net"] = grp["avg_net"].round(2)
    st.dataframe(grp, use_container_width=True, hide_index=True)


# ── Drawdown ──────────────────────────────────────────────────────────────────
if not closed.empty:
    st.subheader("Drawdown")
    eq = closed[["timestamp", "net_profit"]].copy()
    eq["cum"] = eq["net_profit"].fillna(0).cumsum()
    eq["peak"] = eq["cum"].cummax()
    eq["drawdown"] = eq["cum"] - eq["peak"]
    max_dd = eq["drawdown"].min()
    max_dd_pct = (max_dd / eq["peak"].max() * 100) if eq["peak"].max() > 0 else 0
    c1, c2 = st.columns([1, 3])
    c1.metric("Max drawdown", fmt_money(max_dd), f"{max_dd_pct:.1f}%")
    fig_dd = px.area(eq, x="timestamp", y="drawdown",
                     labels={"drawdown": "Drawdown ($)"})
    fig_dd.update_traces(fillcolor="rgba(239,68,68,0.3)", line_color="#ef4444")
    fig_dd.update_layout(height=250, margin=dict(l=10, r=10, t=10, b=10))
    c2.plotly_chart(fig_dd, use_container_width=True)


# ── Fees vs profit + funding income ───────────────────────────────────────────
if not closed.empty:
    st.subheader("Fees vs profit · Funding income")
    c1, c2 = st.columns(2)

    gross = closed["gross_profit"].fillna(0).sum()
    fees = closed["fees"].fillna(0).sum()
    funding = closed["funding_income"].fillna(0).sum() if "funding_income" in closed else 0
    net = closed["net_profit"].fillna(0).sum()
    fee_ratio = (fees / gross * 100) if gross > 0 else 0

    fig = go.Figure(go.Bar(
        x=["Gross profit", "Fees paid", "Funding income", "Net P&L"],
        y=[gross, -fees, funding, net],
        marker_color=["#22c55e", "#ef4444", "#3b82f6", "#0ea5e9"],
        text=[fmt_money(gross), fmt_money(-fees), fmt_money(funding), fmt_money(net)],
        textposition="outside",
    ))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="USD")
    c1.plotly_chart(fig, use_container_width=True)

    c2.markdown(f"""
**Fee burden**
- Fees as % of gross profit: **{fee_ratio:.1f}%**
- Net retention (net / gross): **{(net/gross*100 if gross>0 else 0):.1f}%**
- Funding income contribution: **{fmt_money(funding)}**
- Avg fee per trade: **{fmt_money(fees / max(len(closed),1))}**

A healthy delta-neutral run typically has fee ratio < 60%. Above 80% means
spreads are barely covering fees — tighten the entry threshold or disable
low-spread pairs.
""")


# ── Leverage tier analysis ────────────────────────────────────────────────────
if not closed.empty and "spread_pct" in closed.columns:
    st.subheader("Leverage tier breakdown")
    st.caption("Tiers from config: 7× (<0.20% spread) · 8× (0.20–0.30%) · 10× (≥0.30%)")

    def tier(s):
        if pd.isna(s):
            return "unknown"
        if s < 0.0020:
            return "7× (low)"
        elif s < 0.0030:
            return "8× (medium)"
        else:
            return "10× (high)"

    cl = closed.copy()
    cl["tier"] = cl["spread_pct"].apply(tier)
    tier_stats = cl.groupby("tier").agg(
        trades=("id", "count"),
        net=("net_profit", "sum"),
        gross=("gross_profit", "sum"),
        fees=("fees", "sum"),
        avg_net=("net_profit", "mean"),
        wins=("net_profit", lambda s: (s > 0).sum()),
    ).reset_index()
    tier_stats["win_rate_%"] = (tier_stats["wins"] / tier_stats["trades"] * 100).round(1)
    tier_stats = tier_stats.drop(columns=["wins"]).round(2)
    tier_order = ["7× (low)", "8× (medium)", "10× (high)", "unknown"]
    tier_stats["tier"] = pd.Categorical(tier_stats["tier"], categories=tier_order, ordered=True)
    tier_stats = tier_stats.sort_values("tier")

    c1, c2 = st.columns([2, 1])
    c1.dataframe(tier_stats, use_container_width=True, hide_index=True)

    fig = px.bar(tier_stats, x="tier", y="net",
                 color=tier_stats["net"] >= 0,
                 color_discrete_map={True: "#22c55e", False: "#ef4444"},
                 labels={"net": "Net P&L ($)"}, title="Net P&L per leverage tier")
    fig.update_layout(showlegend=False, height=300, margin=dict(l=10, r=10, t=40, b=10))
    c2.plotly_chart(fig, use_container_width=True)


# ── Comparison: Bot A vs Bot B ────────────────────────────────────────────────
if B is not None:
    st.markdown("---")
    st.header(f"Comparison: {label_a} vs {label_b}")
    st.caption("Same config running live (real money) vs paper (no slippage). "
               "The gap is your real-world execution cost.")

    cl_a = A["closed"]
    cl_b = B["closed"]

    def stats(cl):
        if cl.empty:
            return dict(n=0, net=0, gross=0, fees=0, funding=0, wr=0, avg=0, sp=0)
        n = len(cl)
        net = cl["net_profit"].fillna(0).sum()
        gross = cl["gross_profit"].fillna(0).sum()
        fees = cl["fees"].fillna(0).sum()
        funding = cl["funding_income"].fillna(0).sum() if "funding_income" in cl else 0
        wr = (cl["net_profit"] > 0).sum() / n * 100
        avg = cl["net_profit"].mean()
        sp = cl["spread_pct"].mean() * 100
        return dict(n=n, net=net, gross=gross, fees=fees, funding=funding,
                    wr=wr, avg=avg, sp=sp)

    sa, sb = stats(cl_a), stats(cl_b)

    def delta(a, b, money=True):
        d = a - b
        s = fmt_money(d) if money else f"{d:+.2f}"
        return s

    rows = [
        ("Closed trades",     f"{sa['n']:,}",          f"{sb['n']:,}",          f"{sa['n']-sb['n']:+,}"),
        ("Net P&L",           fmt_money(sa['net']),    fmt_money(sb['net']),    delta(sa['net'], sb['net'])),
        ("Gross P&L",         fmt_money(sa['gross']),  fmt_money(sb['gross']),  delta(sa['gross'], sb['gross'])),
        ("Fees paid",         fmt_money(sa['fees']),   fmt_money(sb['fees']),   delta(sa['fees'], sb['fees'])),
        ("Funding income",    fmt_money(sa['funding']),fmt_money(sb['funding']),delta(sa['funding'], sb['funding'])),
        ("Win rate",          f"{sa['wr']:.1f}%",      f"{sb['wr']:.1f}%",      f"{sa['wr']-sb['wr']:+.1f} pp"),
        ("Avg net / trade",   fmt_money(sa['avg']),    fmt_money(sb['avg']),    delta(sa['avg'], sb['avg'])),
        ("Avg spread",        f"{sa['sp']:.3f}%",      f"{sb['sp']:.3f}%",      f"{sa['sp']-sb['sp']:+.3f} pp"),
    ]
    cmp_df = pd.DataFrame(rows, columns=["Metric", label_a, label_b, "Δ (A − B)"])
    st.dataframe(cmp_df, use_container_width=True, hide_index=True)

    # Slippage / execution gap headline
    if sb["net"] != 0:
        gap = sa["net"] - sb["net"]
        gap_pct = (gap / abs(sb["net"]) * 100) if sb["net"] else 0
        st.info(
            f"**Real-world execution gap (A − B): {fmt_money(gap)}** "
            f"({gap_pct:+.1f}% vs paper). "
            f"This is what slippage + real fills cost you compared to the idealised paper run."
        )

    # Overlaid cumulative P&L
    st.subheader("Cumulative net P&L — overlaid")
    def cum_df(cl, lbl):
        if cl.empty:
            return pd.DataFrame()
        d = cl[["timestamp", "net_profit"]].copy()
        d["cum"] = d["net_profit"].fillna(0).cumsum()
        d["bot"] = lbl
        return d[["timestamp", "cum", "bot"]]

    both = pd.concat([cum_df(cl_a, label_a), cum_df(cl_b, label_b)], ignore_index=True)
    if not both.empty:
        fig = px.line(both, x="timestamp", y="cum", color="bot",
                      labels={"cum": "Cumulative net P&L ($)"})
        fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

    # Per-pair side-by-side
    st.subheader("Per-pair: net P&L A vs B")
    def per_pair(cl, lbl):
        if cl.empty:
            return pd.DataFrame(columns=["pair", lbl])
        g = cl.groupby("pair")["net_profit"].sum().reset_index()
        g.columns = ["pair", lbl]
        return g
    pa, pb = per_pair(cl_a, label_a), per_pair(cl_b, label_b)
    if not pa.empty or not pb.empty:
        merged = pa.merge(pb, on="pair", how="outer").fillna(0)
        merged["Δ (A − B)"] = merged[label_a] - merged[label_b]
        merged = merged.sort_values("Δ (A − B)")
        merged[label_a] = merged[label_a].round(2)
        merged[label_b] = merged[label_b].round(2)
        merged["Δ (A − B)"] = merged["Δ (A − B)"].round(2)
        st.dataframe(merged, use_container_width=True, hide_index=True, height=400)

        # Bar chart: per-pair A vs B
        long = merged.melt(id_vars="pair",
                           value_vars=[label_a, label_b],
                           var_name="bot", value_name="net")
        fig2 = px.bar(long, x="pair", y="net", color="bot", barmode="group",
                      labels={"net": "Net P&L ($)"})
        fig2.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10),
                           xaxis_tickangle=-45)
        st.plotly_chart(fig2, use_container_width=True)


# ── Trade table (filterable) ──────────────────────────────────────────────────
if not closed.empty:
    st.subheader("All trades")
    pairs = sorted(closed["pair"].unique().tolist())
    sel_pairs = st.multiselect("Filter pair(s)", pairs, default=[])
    show = closed.copy()
    if sel_pairs:
        show = show[show["pair"].isin(sel_pairs)]
    cols = ["timestamp", "pair", "notional_usd", "spread_pct",
            "gross_profit", "fees", "net_profit",
            "funding_income", "leg_a_exchange", "leg_b_exchange", "paper_mode"]
    cols = [c for c in cols if c in show.columns]
    st.dataframe(show[cols].sort_values("timestamp", ascending=False),
                 use_container_width=True, hide_index=True, height=400)
    csv = show[cols].to_csv(index=False).encode("utf-8")
    st.download_button("Download filtered trades as CSV", csv, "trades.csv", "text/csv")


# ── Risk events ───────────────────────────────────────────────────────────────
if not risk_events.empty:
    st.subheader("Risk events")
    st.dataframe(risk_events.sort_values("timestamp", ascending=False),
                 use_container_width=True, hide_index=True, height=250)


# ── Daily report table ────────────────────────────────────────────────────────
if not reports.empty:
    st.subheader("Daily reports")
    rep = reports.sort_values("report_date", ascending=False).copy()
    st.dataframe(rep, use_container_width=True, hide_index=True, height=300)


# ── HWM history ───────────────────────────────────────────────────────────────
if not hwm_log.empty:
    st.subheader("High-water mark over time")
    hwm_log["timestamp"] = pd.to_datetime(hwm_log["timestamp"], errors="coerce", utc=True)
    fig = px.line(hwm_log.sort_values("timestamp"), x="timestamp", y="balance",
                  labels={"balance": "Balance ($)"})
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)


# ── Log analysis ──────────────────────────────────────────────────────────────
if log_file is not None:
    st.subheader("Log analysis")
    raw = log_file.getvalue().decode("utf-8", errors="replace")
    lines = raw.splitlines()

    levels = {"INFO": 0, "WARNING": 0, "ERROR": 0, "CRITICAL": 0, "DEBUG": 0}
    for ln in lines:
        for lvl in levels:
            if f"| {lvl:<8}" in ln or f"| {lvl} " in ln:
                levels[lvl] += 1
                break

    cols = st.columns(len(levels))
    for c, (lvl, n) in zip(cols, levels.items()):
        c.metric(lvl, f"{n:,}")

    # Quick filters
    needle = st.text_input("Search log (case-insensitive substring)", "")
    only_err = st.checkbox("Only ERROR / CRITICAL / WARNING", value=False)

    filtered = lines
    if only_err:
        filtered = [l for l in filtered
                    if any(x in l for x in ("ERROR", "CRITICAL", "WARNING"))]
    if needle:
        nl = needle.lower()
        filtered = [l for l in filtered if nl in l.lower()]

    st.write(f"Showing {len(filtered):,} of {len(lines):,} lines")
    # Show last 500 to keep UI snappy
    tail = "\n".join(filtered[-500:])
    st.code(tail or "(no matches)", language="log")

    # Surface ENTRY / EXIT / KILL events
    pat = re.compile(r"(ENTRY|EXIT|KILL|STOP|HALT|SCAN)\b")
    events = [l for l in lines if pat.search(l)]
    if events:
        with st.expander(f"Detected events ({len(events):,})"):
            st.code("\n".join(events[-300:]), language="log")
