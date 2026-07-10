"""Generate comprehensive technical report for Delta-Neutral Bot v2.0"""

from docx import Document
from docx.shared import Pt, RGBColor, Inches, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import datetime

doc = Document()

# ── Page setup ────────────────────────────────────────────────────────────────
section = doc.sections[0]
section.page_width  = Inches(8.5)
section.page_height = Inches(11)
section.left_margin = section.right_margin = Inches(1)
section.top_margin  = section.bottom_margin = Inches(1)

# ── Colour palette ────────────────────────────────────────────────────────────
C_DARK   = RGBColor(0x1A, 0x1A, 0x2E)   # deep navy
C_BLUE   = RGBColor(0x16, 0x13, 0x75)   # mid blue
C_ACCENT = RGBColor(0x0F, 0x3D, 0x91)   # accent blue
C_GREEN  = RGBColor(0x1B, 0x6C, 0x3A)   # profit green
C_RED    = RGBColor(0xC0, 0x39, 0x2B)   # risk red
C_ORANGE = RGBColor(0xD3, 0x54, 0x00)   # warning orange
C_GREY   = RGBColor(0x50, 0x50, 0x50)   # body grey
C_LIGHT  = RGBColor(0xF5, 0xF7, 0xFA)   # light bg (approx; used in tables)
C_WHITE  = RGBColor(0xFF, 0xFF, 0xFF)

# ── Helper: set cell background ───────────────────────────────────────────────
def set_cell_bg(cell, hex_color: str):
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd  = OxmlElement("w:shd")
    shd.set(qn("w:val"),   "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"),  hex_color)
    tcPr.append(shd)

def set_cell_border(cell, **kwargs):
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcBorders = OxmlElement("w:tcBorders")
    for edge in ("top","left","bottom","right"):
        val = kwargs.get(edge, {})
        if val:
            e = OxmlElement(f"w:{edge}")
            e.set(qn("w:val"),   val.get("val","single"))
            e.set(qn("w:sz"),    str(val.get("sz",4)))
            e.set(qn("w:color"), val.get("color","CCCCCC"))
            tcBorders.append(e)
    tcPr.append(tcBorders)

# ── Style helpers ─────────────────────────────────────────────────────────────
def h1(text, color=C_DARK):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(24)
    p.paragraph_format.space_after  = Pt(6)
    run = p.add_run(text)
    run.bold      = True
    run.font.size = Pt(20)
    run.font.color.rgb = color
    # bottom border
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bot  = OxmlElement("w:bottom")
    bot.set(qn("w:val"),   "single")
    bot.set(qn("w:sz"),    "6")
    bot.set(qn("w:space"), "4")
    bot.set(qn("w:color"), "0F3D91")
    pBdr.append(bot)
    pPr.append(pBdr)
    return p

def h2(text, color=C_BLUE):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(16)
    p.paragraph_format.space_after  = Pt(4)
    run = p.add_run(text)
    run.bold      = True
    run.font.size = Pt(14)
    run.font.color.rgb = color
    return p

def h3(text, color=C_ACCENT):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after  = Pt(2)
    run = p.add_run(text)
    run.bold      = True
    run.font.size = Pt(12)
    run.font.color.rgb = color
    return p

def body(text, indent=0):
    p = doc.add_paragraph()
    p.paragraph_format.space_after  = Pt(4)
    p.paragraph_format.left_indent  = Inches(indent)
    run = p.add_run(text)
    run.font.size = Pt(10)
    run.font.color.rgb = C_GREY
    return p

def bullet(text, indent=0.25):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.left_indent  = Inches(indent)
    p.paragraph_format.space_after  = Pt(2)
    run = p.add_run(text)
    run.font.size = Pt(10)
    run.font.color.rgb = C_GREY
    return p

def code_block(lines):
    """Grey-background monospace block."""
    for line in lines:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.left_indent = Inches(0.3)
        run = p.add_run(line if line else " ")
        run.font.name = "Courier New"
        run.font.size = Pt(8.5)
        run.font.color.rgb = RGBColor(0x1E, 0x1E, 0x1E)
        # light grey shading on paragraph
        pPr  = p._p.get_or_add_pPr()
        shd  = OxmlElement("w:shd")
        shd.set(qn("w:val"),   "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"),  "F0F0F0")
        pPr.append(shd)
    doc.add_paragraph()  # spacer

def info_box(label, text, fill="E8F0FE", label_color=C_ACCENT):
    tbl = doc.add_table(rows=1, cols=1)
    tbl.style = "Table Grid"
    tbl.alignment = WD_TABLE_ALIGNMENT.LEFT
    cell = tbl.rows[0].cells[0]
    set_cell_bg(cell, fill)
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    bold_run = p.add_run(f"{label}  ")
    bold_run.bold = True
    bold_run.font.size = Pt(10)
    bold_run.font.color.rgb = label_color
    rest = p.add_run(text)
    rest.font.size = Pt(10)
    rest.font.color.rgb = C_GREY
    doc.add_paragraph()

def two_col_table(headers, rows, col_widths=None):
    """Generic bordered table."""
    n_cols = len(headers)
    tbl = doc.add_table(rows=1+len(rows), cols=n_cols)
    tbl.style = "Table Grid"
    tbl.alignment = WD_TABLE_ALIGNMENT.LEFT

    if col_widths:
        for i, (cell, w) in enumerate(zip(tbl.rows[0].cells, col_widths)):
            cell.width = Inches(w)

    # Header row
    for i, hdr in enumerate(headers):
        cell = tbl.rows[0].cells[i]
        set_cell_bg(cell, "1A1A2E")
        p = cell.paragraphs[0]
        run = p.add_run(hdr)
        run.bold = True
        run.font.size = Pt(9)
        run.font.color.rgb = C_WHITE

    # Data rows
    for r_idx, row_data in enumerate(rows):
        fill = "F8F9FA" if r_idx % 2 == 0 else "FFFFFF"
        for c_idx, val in enumerate(row_data):
            cell = tbl.rows[r_idx+1].cells[c_idx]
            set_cell_bg(cell, fill)
            p = cell.paragraphs[0]
            run = p.add_run(str(val))
            run.font.size = Pt(9)
            run.font.color.rgb = C_GREY
    doc.add_paragraph()

def page_break():
    doc.add_page_break()

# ══════════════════════════════════════════════════════════════════════════════
# COVER PAGE
# ══════════════════════════════════════════════════════════════════════════════
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(72)
p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run("DELTA-NEUTRAL TRADING BOT")
run.bold = True
run.font.size = Pt(28)
run.font.color.rgb = C_DARK

p = doc.add_paragraph()
p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run("v2.0")
run.bold = True
run.font.size = Pt(18)
run.font.color.rgb = C_ACCENT

p = doc.add_paragraph()
p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_before = Pt(12)
run = p.add_run("Complete Technical Engineering Reference")
run.font.size = Pt(14)
run.font.color.rgb = C_GREY

p = doc.add_paragraph()
p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_before = Pt(6)
run = p.add_run("Architecture  |  Strategies  |  Risk  |  Fees  |  Every Function")
run.font.size = Pt(11)
run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

for _ in range(4):
    doc.add_paragraph()

# Summary box on cover
tbl = doc.add_table(rows=3, cols=3)
tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
cover_stats = [
    ("Starting Capital", "$4,000 USDT", "1B6C3A"),
    ("Monthly Target", "$600+",        "0F3D91"),
    ("Strategy",       "Delta-Neutral","1A1A2E"),
    ("Primary Venues", "Binance + Bybit","161375"),
    ("Active Pairs",   "15 Pairs",     "D35400"),
    ("Leverage Range", "7x – 10x",     "C0392B"),
]
for idx, (label, val, color) in enumerate(cover_stats):
    row = idx // 3
    col = idx % 3
    cell = tbl.rows[row].cells[col]
    set_cell_bg(cell, color)
    cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = cell.paragraphs[0]
    p.add_run(f"\n{label}\n").font.color.rgb = C_WHITE
    bold = p.add_run(val + "\n")
    bold.bold = True
    bold.font.size = Pt(13)
    bold.font.color.rgb = C_WHITE
    for run in p.runs:
        run.font.size = Pt(9)
    bold.font.size = Pt(13)

p = doc.add_paragraph()
p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_before = Pt(36)
run = p.add_run(f"Generated: {datetime.datetime.now().strftime('%d %B %Y')}")
run.font.size = Pt(9)
run.font.color.rgb = RGBColor(0xAA, 0xAA, 0xAA)

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 1. EXECUTIVE SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
h1("1. Executive Summary")
body(
    "The Delta-Neutral Trading Bot v2.0 is an automated cryptocurrency arbitrage "
    "system designed to generate consistent monthly income from a $4,000 USDT starting "
    "capital, targeting $600+ per month (15%+ monthly return). It achieves this by "
    "simultaneously holding a long futures position on one exchange and a short futures "
    "position on the same asset on another exchange, capturing the price spread between "
    "venues without taking any directional market risk."
)
body(
    "Because both legs move together with the underlying asset price, gains on the long "
    "leg offset losses on the short leg (and vice versa). The only variable that "
    "determines profit or loss is the change in the spread between the two exchanges — "
    "not whether the market goes up or down. This is the core meaning of 'delta-neutral'."
)

h2("Key Design Principles")
bullet("Market direction is irrelevant — hedged positions cancel out price risk")
bullet("Income comes from spread compression, not speculation")
bullet("Limit orders on all legs — earns maker rebates, never pays taker fees")
bullet("Six-layer risk framework prevents catastrophic loss scenarios")
bullet("Auto-compounding: 40% of monthly profit reinvested automatically")
bullet("Paper mode for safe testing before any live capital is deployed")

h2("Revenue Sources")
two_col_table(
    ["Source", "Mechanism", "Typical Contribution"],
    [
        ["Spread Capture", "Enter when spread > 0.11%; exit when < 0.01%", "Primary — ~80% of income"],
        ["Funding Harvest", "Hold through 8h funding settlements when rate >= 0.03%", "Secondary — ~15% of income"],
        ["Basis Trading", "Spot vs perp premium on same exchange (inactive by default)", "Future — ~5% of income"],
    ],
    col_widths=[1.8, 3.2, 2.0]
)

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 2. SYSTEM ARCHITECTURE
# ══════════════════════════════════════════════════════════════════════════════
h1("2. System Architecture")
body(
    "The bot is written entirely in Python 3.12 and uses the CCXT library to communicate "
    "with exchange APIs. The scheduler (APScheduler) runs strategy ticks on a fixed interval. "
    "All state is persisted in a local SQLite database. A Flask dashboard provides read-only "
    "web access to live performance data."
)

h2("2.1 Directory Structure")
code_block([
    "delta_neutral_bot/",
    "├── bot.py                    # Main entry point — CLI flags, startup, shutdown",
    "├── config/",
    "│   ├── config.yaml           # ALL configuration parameters",
    "│   ├── settings.py           # YAML loader + env var resolver",
    "│   └── logging_setup.py      # Rotating file + console logging",
    "├── core/",
    "│   ├── bot_engine.py         # APScheduler orchestration + job registration",
    "│   ├── kill_switch.py        # Emergency: close all positions immediately",
    "│   └── status.py             # CLI status display",
    "├── strategies/",
    "│   ├── spread_capture.py     # PRIMARY: cross-exchange futures spread trading",
    "│   └── basis_capture.py      # SECONDARY: spot-vs-perp basis trading (inactive)",
    "├── risk/",
    "│   └── risk_manager.py       # Six risk defence layers, HWM tracking",
    "├── alerts/",
    "│   └── alert_manager.py      # SMS (Twilio) + Email (SMTP) alerting",
    "├── reporting/",
    "│   ├── reporter.py           # Daily reports + monthly auto-compound",
    "│   └── profit_extractor.py   # Weekly withdrawals + monthly allocation",
    "├── utils/",
    "│   ├── exchange.py           # CCXT wrapper + paper trading simulation",
    "│   ├── database.py           # SQLite schema, all read/write helpers",
    "│   └── ws_feeds.py           # WebSocket price cache (ccxt.pro)",
    "├── dashboard/",
    "│   └── app.py                # Flask REST API for web dashboard",
    "├── data/",
    "│   └── bot.db                # SQLite database (auto-created)",
    "├── logs/                     # Rotating log files (auto-created)",
    "├── backtest.py               # Historical OHLCV backtest engine",
    "├── scan_pairs.py             # Pair scanner — identifies best exchange pairs",
    "└── progress.py               # CLI progress display with projections",
])

h2("2.2 Startup Sequence")
body("When bot.py runs, the following sequence executes in order:")
two_col_table(
    ["Step", "Action", "File"],
    [
        ["1","Load .env file (API keys, SMTP, Twilio credentials)",".env / dotenv"],
        ["2","Parse CLI flags (--paper, --kill, --report, --status)","bot.py"],
        ["3","Load config.yaml into memory dict","config/settings.py"],
        ["4","Initialize SQLite DB — create tables if missing, run migrations","utils/database.py"],
        ["5","Build exchange clients (paper or live CCXT instances)","utils/exchange.py"],
        ["6","Start WebSocket price feed thread (ccxt.pro)","utils/ws_feeds.py"],
        ["7","Instantiate AlertManager, RiskManager","alerts/, risk/"],
        ["8","Instantiate SpreadCaptureStrategy, BasisCaptureStrategy","strategies/"],
        ["9","Rehydrate open positions from DB (restore state after restart)","strategies/"],
        ["10","Register APScheduler jobs and start scheduler","core/bot_engine.py"],
        ["11","Start Flask dashboard in background thread","dashboard/app.py"],
        ["12","Block on scheduler — main loop runs indefinitely","core/bot_engine.py"],
    ],
    col_widths=[0.4, 4.8, 1.8]
)

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 3. CONFIGURATION (config.yaml)
# ══════════════════════════════════════════════════════════════════════════════
h1("3. Configuration Reference (config.yaml)")
body(
    "All operational parameters live in config/config.yaml. No API keys are ever stored "
    "in this file — they are resolved from environment variables at runtime. The file is "
    "re-read on bot restart. The compound_capital() function writes the updated total_usdt "
    "back to this file at end-of-month."
)

h2("3.1 Capital Block")
two_col_table(
    ["Parameter","Value","Purpose"],
    [
        ["total_usdt","4000","Total capital under management. Updated monthly by auto-compound."],
        ["binance_allocation","0.60","60% ($2,400) deployed on Binance Futures"],
        ["bybit_allocation","0.40","40% ($1,600) deployed on Bybit"],
        ["stablecoin_reserve","600","Hard reserve — never used as margin. Parked in Binance Earn."],
        ["free_margin_buffer","0.15","15% of tradeable capital kept undeployed as safety buffer"],
    ],
    col_widths=[2.4, 1.2, 3.4]
)
info_box("Tradeable Capital Formula:",
    "(total_usdt - stablecoin_reserve) x (1 - free_margin_buffer)  =  "
    "($4,000 - $600) x 0.85  =  $2,890 deployable")

h2("3.2 Leverage Block")
two_col_table(
    ["Spread Size","Leverage","Rationale"],
    [
        ["< 0.20% (default)","7x","Conservative — small spreads need less amplification"],
        ["0.20% to 0.30%","8x","Medium spreads justify slightly higher leverage"],
        [">= 0.30% (maximum)","10x","Large spread events (liquidation cascades, news spikes)"],
    ],
    col_widths=[2.2, 1.4, 3.4]
)

h2("3.3 Strategy Block")
two_col_table(
    ["Parameter","Value","Purpose"],
    [
        ["min_spread_entry","0.0011 (0.11%)","Minimum spread to enter a trade. Break-even is ~0.09% (fees+exit)."],
        ["min_spread_exit","0.0001 (0.01%)","Exit when spread compresses to this level"],
        ["position_check_interval","10s","How often the scheduler checks open/entry positions"],
        ["funding_window_minutes","30","Minutes each side of 00:00/08:00/16:00 UTC to lower entry bar"],
        ["funding_window_fast_interval","3s","Faster scan interval near funding settlements"],
        ["target_trades_per_day","12","Max new trades per pair per day"],
        ["max_simultaneous_positions","20","Maximum concurrent open positions across all pairs"],
        ["max_hold_hours","12","Force-close any position open longer than this"],
        ["trailing_exit_buffer","0.0002","Bounce tolerance for trailing exit logic"],
        ["funding_harvest_min_rate","0.0001","Minimum funding rate to consider it income"],
        ["funding_harvest_entry_min_rate","0.0003","Rate threshold to enter a harvest-only position"],
        ["funding_harvest_deploy_pct","0.30","30% of tradeable capital for harvest-only entries"],
    ],
    col_widths=[2.6, 1.4, 3.0]
)

h2("3.4 Exchange Fees Block")
body("These rates drive all fee calculations. Round-trip cost = (fee_a + fee_b) x 2 (open + close both legs).")
two_col_table(
    ["Exchange","Maker Fee","Round-Trip (vs Bybit)","Notes"],
    [
        ["binance","0.020%","0.080%","Can reduce to 0.018% with BNB fee discount"],
        ["bybit","0.020%","0.080%","Standard maker rate"],
        ["gate","0.015%","0.070%","Slightly cheaper than Bybit"],
        ["bitget","0.020%","0.080%","Same as Bybit"],
        ["mexc","0.000%","0.040%","Free maker — best fee structure available"],
        ["okx","0.020%","0.080%","EEA accounts via my.okx.com"],
    ],
    col_widths=[1.4, 1.2, 1.8, 2.6]
)

h2("3.5 Risk Block")
two_col_table(
    ["Parameter","Value","Trigger Action"],
    [
        ["account_stop_loss_pct","8%","Close ALL positions immediately"],
        ["high_water_mark_floor","15%","Halt bot entirely — requires manual restart"],
        ["max_margin_usage","75%","Critical alert — no new entries"],
        ["margin_alert_pct","70%","Warning alert sent"],
        ["max_daily_loss","$120","Halt trading for 24 hours"],
        ["atr_spike_threshold","5%","Halve all position sizes for that asset"],
        ["news_blackout_minutes","120","Pause all entries 2h around listed events"],
    ],
    col_widths=[2.4, 1.2, 3.4]
)

h2("3.6 Position Sizing Block")
two_col_table(
    ["Parameter","Value","Meaning"],
    [
        ["high_spread_threshold","0.20%","Spread level to deploy 100% of available margin"],
        ["high_spread_deploy","1.00","100% deploy factor"],
        ["medium_spread_min","0.08%","Spread level for 60% deploy"],
        ["medium_spread_deploy","0.60","60% deploy factor"],
        ["low_spread_deploy","0.00","Below 0.08% — sit flat, no entry"],
        ["post_drawdown_deploy","0.40","Cap at 40% for 72h after a stop-loss trigger"],
        ["correlation_min","0.85","Minimum rolling correlation to allow entry"],
        ["correlation_lookback","60 min","Window for rolling correlation calculation"],
    ],
    col_widths=[2.4, 1.2, 3.4]
)

h2("3.7 Profit Extraction Block")
two_col_table(
    ["Parameter","Value","Purpose"],
    [
        ["weekly_withdrawal_pct","35%","Of weekly net profit withdrawn to cold wallet (Fridays 18:00 AWST)"],
        ["monthly_tax_pct","25%","Reserved for Australian tax (ATO compliance)"],
        ["monthly_reinvest_pct","40%","Added back to trading capital (auto-compound)"],
        ["monthly_liquidity_pct","35%","Kept as USDT spot buffer"],
    ],
    col_widths=[2.4, 1.0, 3.6]
)

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 4. CORE ENGINE (bot_engine.py)
# ══════════════════════════════════════════════════════════════════════════════
h1("4. Core Bot Engine (core/bot_engine.py)")
body(
    "BotEngine is the central orchestrator. It owns all component instances and registers "
    "timed jobs with APScheduler (BackgroundScheduler). Every action the bot takes — from "
    "scanning for trades to compounding capital — originates from a job registered here."
)

h2("4.1 Scheduler Jobs")
two_col_table(
    ["Job Method","Interval / Schedule","Purpose"],
    [
        ["_strategy_tick()","Every 10s (position_check_interval)","Run SpreadCaptureStrategy.tick() — scan entries, manage exits"],
        ["_risk_tick()","Every 60s","Run RiskManager.tick() — all 6 risk checks"],
        ["_report_tick()","Daily at 23:55 UTC","Save daily report to DB, email summary"],
        ["_profit_tick()","Fridays 18:00 AWST","Run ProfitExtractor.weekly_withdrawal()"],
        ["_monthly_tick()","Last day of month 22:00 AWST","Run ProfitExtractor.monthly_tasks()"],
        ["_compound_job()","Last day of month 22:05 AWST","Run compound_capital() — add 40% profit to total_usdt"],
        ["_funding_scan_tick()","Every 3s near settlement windows","Faster strategy tick during funding windows"],
        ["_dashboard_tick()","Background thread (Flask)","Serve web dashboard on port 5000"],
    ],
    col_widths=[2.0, 2.2, 2.8]
)

h2("4.2 Graceful Shutdown")
body(
    "BotEngine.shutdown() is called on KeyboardInterrupt or SIGTERM. It stops the APScheduler, "
    "stops the WebSocket price feed thread (cancelling all async tasks before stopping the event "
    "loop), and logs the shutdown event. Positions are NOT automatically closed on shutdown — "
    "they remain open and are rehydrated from the database on next startup."
)

h2("4.3 _strategy_tick()")
body(
    "Calls strategy.tick() which internally calls three sub-methods in sequence: "
    "_manage_open_positions() to check exits on all open trades, _scan_for_entries() "
    "to look for new opportunities, then _scan_funding_harvest() to look for near-settlement "
    "funding opportunities. Finally _check_inactivity() fires an alert if no trades have "
    "executed in 4+ hours."
)

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 5. PRIMARY STRATEGY — SPREAD CAPTURE
# ══════════════════════════════════════════════════════════════════════════════
h1("5. Primary Strategy: Spread Capture (strategies/spread_capture.py)")

body(
    "SpreadCaptureStrategy is the core revenue engine. It exploits temporary price "
    "divergences between the same perpetual futures contract listed on two different exchanges. "
    "Because both are the same asset, prices revert toward each other — the bot captures this "
    "convergence as profit."
)

h2("5.1 The Core Concept")
info_box("Example Trade (buy_a_sell_b):",
    "ETH/USDT:USDT — Binance ask=$3,001.50, Bybit bid=$3,008.20  |  "
    "Spread = (3008.20 - 3001.50) / 3001.50 = 0.223%  |  "
    "Action: Buy ETH on Binance at $3,001.50, Short ETH on Bybit at $3,008.20  |  "
    "Exit when spread < 0.01%  |  "
    "Gross profit = ($3,006.00 - $3,001.50) x qty + ($3,008.20 - $3,003.00) x qty = spread x notional")

h2("5.2 Bidirectional Spread Detection")
body(
    "Every scan checks BOTH directions and selects whichever offers a positive executable spread. "
    "This doubles the number of tradeable opportunities:"
)
two_col_table(
    ["Direction","Long Leg","Short Leg","Spread Formula","Entry Price Used"],
    [
        ["buy_a_sell_b","Exchange A (Binance)","Exchange B (Bybit)","(bid_b - ask_a) / ask_a","ask_a + bid_b"],
        ["buy_b_sell_a","Exchange B (Bybit)","Exchange A (Binance)","(bid_a - ask_b) / ask_b","ask_b + bid_a"],
    ],
    col_widths=[1.6, 1.6, 1.4, 2.0, 1.4]
)
body(
    "The direction with the larger positive spread is selected. Negative spread in one direction "
    "always means the other direction has a positive spread of equal magnitude. The selected "
    "direction is stored in the database and used throughout the trade lifecycle."
)

h2("5.3 _evaluate_entry() — Entry Logic")
body("Called for every active pair on every strategy tick. Returns a signal dict or None.")
code_block([
    "# Step 1: Fetch bid/ask from both exchanges (executable prices, not mid)",
    "ask_a = ticker_a['ask']   # price to buy on exchange A",
    "bid_a = ticker_a['bid']   # price to sell on exchange A",
    "ask_b = ticker_b['ask']   # price to buy on exchange B",
    "bid_b = ticker_b['bid']   # price to sell on exchange B",
    "",
    "# Step 2: Calculate both directions",
    "spread_ab = (bid_b - ask_a) / ask_a   # buy A, sell B",
    "spread_ba = (bid_a - ask_b) / ask_b   # buy B, sell A",
    "",
    "# Step 3: Pick best direction",
    "if spread_ab >= spread_ba:",
    "    spread, direction = spread_ab, 'buy_a_sell_b'",
    "    price_a, price_b = ask_a, bid_b",
    "else:",
    "    spread, direction = spread_ba, 'buy_b_sell_a'",
    "    price_a, price_b = ask_b, bid_a",
    "",
    "# Step 4: Gate against threshold",
    "if spread < effective_threshold:  return None   # 0.11% normally, 0.055% in funding window",
    "",
    "# Step 5: Correlation filter",
    "if correlation < 0.85:  return None",
    "",
    "# Step 6: Calculate notional",
    "notional = tradeable * leverage * deploy_pct / concurrent_positions",
    "",
    "# Step 7: Return signal",
    "return { 'spread': spread, 'notional': notional, 'leverage': leverage,",
    "         'price_a': price_a, 'price_b': price_b, 'direction': direction, ... }",
])

h2("5.4 _enter_position() — Order Execution")
body(
    "Executes both legs sequentially. If Leg A fills but Leg B fails, Leg A is immediately "
    "closed at market to restore delta-neutral state. Uses limit orders for maker fee rates. "
    "A 10-second fill timeout polls order status every second before falling back to market."
)
two_col_table(
    ["Step","Action","Failure Handling"],
    [
        ["1","Set leverage on both exchanges to signal['leverage']","Continue (non-fatal)"],
        ["2","Determine sides: buy_a_sell_b = (buy A, sell B); buy_b_sell_a = (sell A, buy B)","N/A"],
        ["3","Place limit order on Leg A at signal['price_a']","Cancel and skip trade"],
        ["4","Wait up to 10s for Leg A to fill (poll every 1s)","Cancel Leg A, skip trade"],
        ["5","Place limit order on Leg B at signal['price_b']","Market-close Leg A immediately"],
        ["6","Wait up to 10s for Leg B to fill","Cancel Leg B, market-close Leg A"],
        ["7","Log trade to DB with all fields including direction","N/A"],
        ["8","Store position in _open_positions dict in-memory","N/A"],
    ],
    col_widths=[0.4, 3.4, 2.2]
)

h2("5.5 _manage_open_positions() — Exit Logic")
body("Called every tick for all open trades. Checks multiple exit conditions in priority order:")
two_col_table(
    ["Condition","Check","Action"],
    [
        ["Max hold time","hold_hours >= 12","Force close — free capital for better opportunities"],
        ["Spread collapsed","current_spread < 0.01%","Close — target reached"],
        ["Trailing exit","spread bounced 0.02% above min but below entry threshold","Close — optimal capture"],
        ["Adverse funding","funding rate turned negative (we pay, not receive)","Close — cost exceeds benefit"],
        ["Position stop-loss","unrealised P&L < -8% of account equity","Emergency close"],
        ["Funding hold","spread ready to exit AND settlement < 15min away","HOLD — collect funding first"],
    ],
    col_widths=[1.8, 2.6, 2.6]
)

h2("5.6 _close_position() — P&L Calculation")
body(
    "This is the most critical calculation in the bot. It uses actual fill prices "
    "stored at entry time. The formula differs by direction because the price variable "
    "naming follows the signal dict order, not exchange order:"
)
info_box("Variable Naming Convention:",
    "For buy_b_sell_a: entry_price_a = ask_b (price paid on Bybit, the long leg) and "
    "entry_price_b = bid_a (price received on Binance, the short leg). "
    "The 'a/b' suffix refers to which signal dict slot was used, NOT which exchange.")
code_block([
    "# DIRECTION: buy_a_sell_b",
    "# Long on Exchange A (entry_price_a = ask_a), Short on Exchange B (entry_price_b = bid_b)",
    "# exit_price_a = bid_a (sell long A), exit_price_b = ask_b (cover short B)",
    "gross = (exit_price_a - entry_price_a) * amount_a   # Long A P&L",
    "      + (entry_price_b - exit_price_b) * amount_b   # Short B P&L",
    "",
    "# DIRECTION: buy_b_sell_a",
    "# Long on Exchange B (entry_price_a = ask_b), Short on Exchange A (entry_price_b = bid_a)",
    "# exit_price_b = bid_b (sell long B), exit_price_a = ask_a (cover short A)",
    "gross = (exit_price_b - entry_price_a) * amount_a   # Long B P&L",
    "      + (entry_price_b - exit_price_a) * amount_b   # Short A P&L",
])

h2("5.7 Fee Calculation")
body("Fees are calculated per-trade using the per-exchange rates from the exchange_fees config block:")
code_block([
    "fee_a = exchange_fees[leg_a_exchange]   # e.g. binance = 0.0002",
    "fee_b = exchange_fees[leg_b_exchange]   # e.g. bybit   = 0.0002",
    "",
    "# 4 orders total: open A + open B + close A + close B",
    "FEE_RATE = (fee_a + fee_b) * 2",
    "",
    "fees = notional * FEE_RATE",
    "",
    "# Example: $10,000 notional, Binance+Bybit",
    "# FEE_RATE = (0.0002 + 0.0002) * 2 = 0.0008  (0.08%)",
    "# fees = $10,000 * 0.0008 = $8.00",
])
info_box("Break-Even Spread:",
    "For Binance+Bybit: fee = 0.08%. Exit threshold = 0.01%. "
    "Break-even entry = 0.08% + 0.01% = 0.09%. "
    "Bot uses 0.11% minimum entry for a 0.02% profit margin per trade. "
    "On $10,000 notional: gross = $11, fees = $8, net = $3 minimum per trade.")

h2("5.8 Net P&L Formula")
code_block([
    "net = gross + funding_collected - fees",
    "",
    "# funding_collected = funding_rate * notional * floor(hold_hours / 8)",
    "# Each 8h period (00:00, 08:00, 16:00 UTC) is one settlement",
    "# funding_rate is captured at entry time from exchange API",
    "",
    "# Example: held 17h, funding rate 0.01%/8h, notional $10,000",
    "# funding_settlements = floor(17/8) = 2",
    "# funding_collected = 0.0001 * 10000 * 2 = $2.00",
])

h2("5.9 Notional Sizing Formula")
code_block([
    "tradeable   = (total_usdt - stablecoin_reserve) * (1 - free_margin_buffer)",
    "            = ($4,000 - $600) * (1 - 0.15)  =  $2,890",
    "",
    "concurrent  = min(current_open_trades + 1, max_simultaneous_positions)",
    "",
    "notional    = tradeable * leverage * deploy_pct / concurrent",
    "",
    "# Example: 5 open positions, spread=0.15% (7x lev, 60% deploy)",
    "# concurrent = min(6, 20) = 6",
    "# notional = $2,890 * 7 * 0.60 / 6 = $2,023",
])

h2("5.10 Funding Harvest Sub-Strategy")
body(
    "_scan_funding_harvest() runs on every tick but only acts within 15 minutes of a "
    "funding settlement (00:00, 08:00, 16:00 UTC). If the funding rate on a pair is >= 0.03%/8h, "
    "the bot enters a conservatively-sized position (30% deploy) regardless of whether the "
    "spread meets the normal 0.11% threshold. Revenue is the funding payment received 8 hours later."
)
body(
    "_should_hold_for_funding() prevents exiting an existing spread trade when settlement "
    "is within 15 minutes and the funding rate is positive — the position holds to collect "
    "the payment before closing."
)

h2("5.11 ATR Spike Detection")
body(
    "_get_atr_pct() fetches the last 24 hourly candles and calculates average true range "
    "as a percentage of price. If ATR > 5%, position sizes are halved for that asset. "
    "Results are cached for 10 minutes to avoid redundant API calls."
)

h2("5.12 Rolling Correlation Filter")
body(
    "_get_rolling_correlation() fetches the last 60 one-minute candles from both exchanges "
    "and computes the Pearson correlation between closing prices. If correlation < 0.85, "
    "the entry is skipped. This prevents entering when two venues are temporarily decorrelated "
    "(e.g., one exchange having technical issues), which would break the delta-neutral assumption."
)

h2("5.13 Position Rehydration")
body(
    "_rehydrate_open_positions() runs at startup. It reads all trades with status='open' "
    "from the database and rebuilds the in-memory _open_positions dictionary, restoring "
    "entry_price_a, entry_price_b, direction, close_side_a, close_side_b, and amounts. "
    "This allows the bot to correctly manage positions that were open before a restart."
)

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 6. SECONDARY STRATEGY — BASIS CAPTURE
# ══════════════════════════════════════════════════════════════════════════════
h1("6. Secondary Strategy: Basis Capture (strategies/basis_capture.py)")
body(
    "BasisCaptureStrategy exploits the premium (basis) that perpetual futures trade at "
    "relative to spot price on the SAME exchange. When perp > spot, the bot buys spot "
    "and shorts the perp. Revenue comes from two sources: (1) the basis converging back "
    "to zero, and (2) funding payments received every 8 hours (longs pay shorts when "
    "perpetual trades above spot)."
)
info_box("Status:", "INACTIVE by default. All basis_pairs have active: false in config.yaml. "
    "Requires paper-testing before activation. Uses Binance Spot + Binance Futures.")

h2("6.1 Basis Formula")
code_block([
    "basis = (perp_price - spot_price) / spot_price",
    "",
    "# Entry condition: basis >= 0.10% (min_basis_entry)",
    "# Exit condition:  basis <= 0.02% (min_basis_exit) OR hold >= 12h",
])

h2("6.2 Entry (_enter_basis)")
two_col_table(
    ["Leg","Exchange","Order Type","Rationale"],
    [
        ["Buy spot","binance_spot","Market (taker)","Needs immediate fill — spot price is the reference"],
        ["Short perp","binance","Limit (maker)","Maker fee rate; perp is less time-sensitive"],
    ],
    col_widths=[1.2, 1.6, 1.8, 2.4]
)
body("Size = 20% of tradeable capital (basis_size_pct). No leverage on spot leg.")

h2("6.3 Exit (_close_basis)")
two_col_table(
    ["Leg","Order Type","Exit Condition"],
    [
        ["Sell spot","Market","When basis <= 0.02% or max hold time reached"],
        ["Cover perp","Limit (market fallback)","Same trigger as spot"],
    ],
    col_widths=[1.2, 1.8, 4.0]
)

h2("6.4 Basis Fee Calculation")
code_block([
    "FEE_RATE_BASIS = 0.0024  # 0.24% round-trip",
    "",
    "# Entry:  spot market taker 0.10% + perp limit maker 0.02% = 0.12%",
    "# Exit:   spot market taker 0.10% + perp limit maker 0.02% = 0.12%",
    "# Total:  0.24%",
    "",
    "fees = notional * FEE_RATE_BASIS",
    "net  = (entry_basis - exit_basis) * notional + funding_collected - fees",
])

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 7. RISK MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════
h1("7. Risk Management (risk/risk_manager.py)")
body(
    "RiskManager implements a six-layer defence framework. It runs every 60 seconds "
    "via the scheduler. Each layer addresses a different failure mode."
)

h2("Layer 1: Account Stop-Loss (_check_account_stop_loss)")
body(
    "Fetches total unrealised P&L across all exchanges using fetch_positions(). "
    "If unrealised loss exceeds 8% of total equity, ALL open positions are closed "
    "via market orders and post-drawdown mode is entered for 72 hours."
)

h2("Layer 2: High-Water Mark Floor (_check_high_water_mark)")
body(
    "Tracks the account peak balance in the high_water_marks table. If the current "
    "total equity drops 15% below the all-time peak (HWM), the bot halts entirely and "
    "sends an alert. This requires a MANUAL restart — the bot will not resume automatically. "
    "Starting HWM is $4,000."
)

h2("Layer 3: Margin Usage Monitor (_check_margin_usage)")
body(
    "Fetches balance from each exchange and calculates used margin as a percentage of total. "
    "At 70% margin usage: warning alert. At 75%: critical alert. Does not halt trading — "
    "this is informational, letting the operator reduce positions manually."
)

h2("Layer 4: Daily Loss Cap (_check_daily_loss_limit)")
body(
    "Queries the database for the sum of net_profit for all closed trades today. "
    "If the total is below -$120, trading is halted for 24 hours. This prevents a "
    "bad session from spiralling into account destruction."
)

h2("Layer 5: ATR Spike Filter (_check_atr_spikes)")
body(
    "Runs across all active pairs every minute. If any asset's 24-hour ATR exceeds 5% "
    "of price, an alert is sent. The strategy layer also checks ATR independently and "
    "halves position sizes when the spike threshold is breached."
)

h2("Layer 6: Post-Drawdown Mode")
body(
    "After a stop-loss trigger, the bot enters post-drawdown mode for 72 hours. "
    "During this period, position sizing is capped at 40% (post_drawdown_deploy) "
    "regardless of spread size. This forces conservative behaviour while assessing "
    "whether market conditions have normalised."
)

h2("7.1 Emergency Kill Switch (core/kill_switch.py)")
body(
    "Running python bot.py --kill activates the kill switch. It builds live exchange "
    "clients, fetches all open trades from the database, and closes every position "
    "using market orders (no limit, no timeout). Converts notional USD to contract "
    "quantity using current last price. Logs a kill_switch risk event to the database."
)

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 8. EXCHANGE LAYER
# ══════════════════════════════════════════════════════════════════════════════
h1("8. Exchange Layer (utils/exchange.py)")
body(
    "ExchangeClient wraps a ccxt exchange instance with paper trading simulation, "
    "unified error handling, and WebSocket cache integration. All strategy code "
    "interacts exclusively with ExchangeClient — never with ccxt directly."
)

h2("8.1 Paper Trading Mode")
body(
    "When paper_mode=True, no real API calls are made for order placement or balance "
    "queries. The simulation is designed to be as realistic as possible:"
)
two_col_table(
    ["Operation","Paper Behaviour","Notes"],
    [
        ["fetch_balance()","Returns configured paper_balance allocation","Binance=$2,400, Bybit=$1,600"],
        ["place_order() limit","Fills at the specified limit price","Accurate — no slippage simulation"],
        ["place_order() market","Fills at last price from fetch_ticker()","Conservative estimate"],
        ["fetch_ticker()","Real REST API call to exchange","Live prices even in paper mode"],
        ["fetch_positions()","Returns empty list","No simulated position tracking"],
        ["set_leverage()","No-op","Silently ignored"],
        ["cancel_order()","No-op","Silently ignored"],
        ["close_position()","Simulated fill at bid/ask","Same as place_order simulation"],
    ],
    col_widths=[2.0, 2.6, 2.4]
)

h2("8.2 build_exchange_clients()")
body(
    "Factory function that reads the exchanges config block and instantiates only the "
    "exchanges actually used by active pairs (avoids unnecessary API connections). "
    "Calculates per-exchange paper balance from the capital allocation config "
    "(binance_allocation * total_usdt, bybit_allocation * total_usdt, etc.). "
    "Sets defaultType (future/swap) from exchange config. Loads markets on startup."
)

h2("8.3 Key Methods")
two_col_table(
    ["Method","Parameters","Returns","Notes"],
    [
        ["fetch_ticker(symbol)","symbol: str","dict with last/bid/ask/quoteVolume","Checks WS cache first, falls back to REST"],
        ["fetch_balance()","—","dict with free/used/total keys","Paper: returns configured allocation"],
        ["place_order(sym,side,qty)","symbol,side,amount,order_type,price","order dict with id/status","Limit default, market fallback"],
        ["close_position(sym,side,qty)","symbol,side,amount,order_type,price","order dict","Calls place_order with reduce_only=True"],
        ["fetch_funding_rate(sym)","symbol: str","dict with fundingRate key","Used for harvest and adverse rate check"],
        ["fetch_ohlcv(sym,tf,limit)","symbol,timeframe,limit","list of OHLCV candles","Used for ATR and correlation calc"],
        ["set_leverage(sym,lev)","symbol,leverage","None","Sets cross-margin leverage on exchange"],
        ["get_total_equity()","—","float (USDT)","free + unrealised P&L from balance"],
    ],
    col_widths=[2.0, 1.8, 1.6, 1.6]
)

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 9. WEBSOCKET PRICE FEEDS
# ══════════════════════════════════════════════════════════════════════════════
h1("9. WebSocket Price Feeds (utils/ws_feeds.py)")
body(
    "WSPriceCache runs ccxt.pro WebSocket subscriptions in a dedicated daemon thread "
    "with its own asyncio event loop. Strategy code reads from an in-memory cache "
    "instead of making REST API calls — eliminating polling latency and exchange "
    "rate-limit pressure."
)

h2("9.1 Cache Data Structure")
code_block([
    "_cache: dict[(exchange_name, symbol)] = {",
    "    'last':        float,   # last traded price",
    "    'bid':         float,   # current best bid (executable sell price)",
    "    'ask':         float,   # current best ask (executable buy price)",
    "    'quoteVolume': float,   # 24h quote volume",
    "    'ts':          float,   # unix timestamp of last update",
    "}",
    "",
    "# Cache entry is considered STALE after 10 seconds (MAX_CACHE_AGE)",
    "# If stale or missing, callers fall back to REST fetch_ticker()",
])

h2("9.2 Lifecycle")
two_col_table(
    ["Phase","Action"],
    [
        ["Registration","ws.register('binance', 'ETH/USDT:USDT') — called for each active pair"],
        ["Start","ws.start() — creates new event loop, starts daemon thread _run_loop()"],
        ["Streaming","_watch_symbol() loops: watch_ticker() -> update cache -> sleep on error"],
        ["Error recovery","Exponential backoff: wait = min(5 * consecutive_errors, 60) seconds"],
        ["Shutdown","stop() cancels all async tasks via asyncio.all_tasks(), then stops loop"],
        ["Fallback","If ccxt.pro not installed or WS unavailable, REST polling used transparently"],
    ],
    col_widths=[1.6, 5.4]
)

h2("9.3 Exchange Mapping")
code_block([
    "_EXCHANGE_MAP = {",
    "    'binance':      'binanceusdm',  # Binance USD-M Futures",
    "    'bybit':        'bybit',         # Bybit V5 (unified)",
    "    'okx':          'myokx',         # OKX EEA (my.okx.com)",
    "    'gate':         'gate',          # Gate.io Futures",
    "    'bitget':       'bitget',        # Bitget Futures",
    "    'mexc':         'mexc',          # MEXC Futures (0% maker)",
    "    'binance_spot': 'binance',       # Binance Spot (basis trading)",
    "}",
])

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 10. DATABASE
# ══════════════════════════════════════════════════════════════════════════════
h1("10. Database (utils/database.py)")
body(
    "All persistent state lives in a single SQLite database at data/bot.db. "
    "The database is initialised on startup via init_db(). "
    "Backward-compatible column additions use ALTER TABLE migrations wrapped in "
    "try/except so existing databases are upgraded transparently."
)

h2("10.1 Table: trades")
body("Central table — one row per trade, open or closed.")
two_col_table(
    ["Column","Type","Description"],
    [
        ["id","INTEGER PK","Auto-increment trade ID"],
        ["timestamp","TEXT","ISO 8601 UTC timestamp of entry"],
        ["pair","TEXT","e.g. 'ETH/USDT:USDT_ETH/USDT:USDT'"],
        ["leg_a_exchange","TEXT","Exchange name for leg A (e.g. 'binance')"],
        ["leg_b_exchange","TEXT","Exchange name for leg B (e.g. 'bybit')"],
        ["leg_a_symbol","TEXT","Symbol on leg A (e.g. 'ETH/USDT:USDT')"],
        ["leg_b_symbol","TEXT","Symbol on leg B"],
        ["notional_usd","REAL","Trade size in USD (both legs same notional)"],
        ["spread_pct","REAL","Entry spread as decimal (e.g. 0.0023 = 0.23%)"],
        ["gross_profit","REAL","Pre-fee profit in USD (NULL while open)"],
        ["fees","REAL","Total fees paid in USD (NULL while open)"],
        ["net_profit","REAL","After-fee profit: gross + funding - fees (NULL while open)"],
        ["funding_income","REAL","Funding rate captured at entry (per 8h per unit)"],
        ["status","TEXT","'open' or 'closed'"],
        ["paper_mode","INTEGER","1 = paper trade, 0 = live"],
        ["notes","TEXT","Free-text: entry spread, exit reason, order IDs"],
        ["amount_a","REAL","Quantity purchased/sold on leg A"],
        ["amount_b","REAL","Quantity purchased/sold on leg B"],
        ["entry_price_a","REAL","Fill price on leg A at entry"],
        ["entry_price_b","REAL","Fill price on leg B at entry"],
        ["direction","TEXT","'buy_a_sell_b' or 'buy_b_sell_a'"],
    ],
    col_widths=[2.0, 1.2, 3.8]
)

h2("10.2 Other Tables")
two_col_table(
    ["Table","Purpose","Key Columns"],
    [
        ["high_water_marks","Track account equity peaks for HWM floor check","timestamp, balance"],
        ["risk_events","Log all risk triggers for audit trail","timestamp, event_type, description, balance, action"],
        ["daily_reports","Daily performance snapshots","report_date, trades_count, gross_profit, fees, net_profit, account_balance"],
        ["withdrawals","Record all profit extractions","timestamp, withdrawal_type, amount_usd, destination"],
        ["alerts_log","Record all sent alerts","timestamp, alert_type, message, sent_sms, sent_email"],
    ],
    col_widths=[1.8, 2.8, 2.4]
)

h2("10.3 Key Query Functions")
two_col_table(
    ["Function","Query","Returns"],
    [
        ["get_open_trades()","SELECT * WHERE status='open'","List of open trade dicts"],
        ["get_daily_net_profit()","SUM(net_profit) WHERE DATE(timestamp)=today","float"],
        ["get_weekly_net_profit()","SUM(net_profit) last 7 days","float"],
        ["get_monthly_net_profit()","SUM(net_profit) current month","float"],
        ["get_high_water_mark()","MAX(balance) FROM high_water_marks","float"],
        ["log_trade(data)","INSERT INTO trades","Returns new trade ID (int)"],
        ["update_trade(id, fields)","UPDATE trades SET ... WHERE id=?","None"],
    ],
    col_widths=[2.4, 2.4, 2.2]
)

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 11. ALERTS
# ══════════════════════════════════════════════════════════════════════════════
h1("11. Alert System (alerts/alert_manager.py)")
body(
    "AlertManager sends notifications via SMS (Twilio) and Email (SMTP). "
    "A 30-minute cooldown per alert type prevents spam during sustained "
    "event conditions. All alerts are logged to the alerts_log table."
)

h2("11.1 Alert Types")
two_col_table(
    ["Alert Type","Trigger","Severity"],
    [
        ["account_stop_loss","Unrealised P&L < -8% of equity","CRITICAL"],
        ["hwm_floor","Balance dropped 15% from peak","CRITICAL"],
        ["margin_critical","Margin usage >= 75%","CRITICAL"],
        ["margin_alert","Margin usage >= 70%","WARNING"],
        ["max_daily_loss","Daily P&L < -$120","WARNING"],
        ["atr_spike","Asset ATR > 5% in 24h","WARNING"],
        ["trading_halt","Any halt_trading() call","INFO"],
        ["weekly_withdrawal","Weekly profit extraction fired","INFO"],
        ["monthly_allocation","Monthly profit split completed","INFO"],
        ["inactivity","No trades for 4+ hours","WARNING"],
    ],
    col_widths=[2.2, 2.8, 2.0]
)

h2("11.2 Channel Configuration")
body("Credentials are read from environment variables (never hardcoded in config):")
two_col_table(
    ["Channel","Required Env Vars","Notes"],
    [
        ["SMS (Twilio)","TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, ALERT_PHONE_NUMBER","Skipped if any var missing or contains 'your_'"],
        ["Email (SMTP)","SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, ALERT_EMAIL","Uses STARTTLS on port 587"],
    ],
    col_widths=[1.6, 3.4, 2.0]
)

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 12. REPORTING & PROFIT EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════
h1("12. Reporting & Profit Extraction")

h2("12.1 reporter.py")
h3("save_todays_report()")
body(
    "Called daily at 23:55 UTC. Queries the trades table for today's closed trades "
    "and aggregates: trade count, gross profit, fees, net profit, funding income, "
    "average spread. Also fetches balance from all exchanges to record account_balance "
    "and margin_usage_pct. Saves the result to the daily_reports table."
)

h3("generate_daily_report()")
body(
    "Called by python bot.py --report. Fetches last 30 days of daily_reports and "
    "prints a formatted table showing date, trade count, gross, fees, net, and balance "
    "for each day, with totals at the bottom."
)

h3("compound_capital()")
body(
    "Called on the last day of each month at 22:05 AWST. Reads monthly net profit "
    "from the database. Takes 40% (monthly_reinvest_pct) and adds it to total_usdt "
    "both in memory and by rewriting config.yaml. This means notional calculations "
    "on the next trade will use the larger capital base."
)
code_block([
    "reinvest_amt = get_monthly_net_profit() * 0.40",
    "cfg['capital']['total_usdt'] += reinvest_amt",
    "# Also writes new value to config/config.yaml via yaml.dump()",
])

h2("12.2 profit_extractor.py")
h3("weekly_withdrawal() — Every Friday 18:00 AWST")
body(
    "Calculates 35% of the week's net profit. Logs a withdrawal record to the database "
    "with the cold wallet address (from COLD_WALLET_ADDRESS env var). Sends an alert. "
    "In paper mode this is a log entry only. In live mode the operator must execute "
    "the actual exchange withdrawal (requires pre-approved withdrawal whitelist)."
)

h3("monthly_tasks() — Last Day of Month 22:00 AWST")
body("Splits the month's net profit into three allocations and logs each as a withdrawal record:")
two_col_table(
    ["Allocation","Percentage","Purpose"],
    [
        ["AUD Tax Reserve","25%","Set aside for Australian Tax Office (ATO) obligations"],
        ["Margin Reinvest","40%","Returned to trading capital via compound_capital()"],
        ["USDT Liquidity","35%","Kept as spot USDT buffer for opportunities or emergencies"],
    ],
    col_widths=[2.0, 1.2, 3.8]
)

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 13. ACTIVE TRADING PAIRS
# ══════════════════════════════════════════════════════════════════════════════
h1("13. Active Trading Pairs")
body(
    "15 pairs are currently active on Binance Futures vs Bybit. Pairs were selected "
    "based on 90-day backtest results prioritising spread frequency, win rate, and "
    "monthly net profit. BTC and BNB are excluded — their spreads are too tight to "
    "overcome fees consistently."
)

h2("13.1 Active Pairs (Binance vs Bybit)")
two_col_table(
    ["Symbol","Rationale"],
    [
        ["ETH/USDT:USDT","Highest volume — consistent micro-spreads multiple times/day"],
        ["DOGE/USDT:USDT","2-4x wider spreads than ETH due to retail-driven volatility"],
        ["SOL/USDT:USDT","High activity during network events"],
        ["AVAX/USDT:USDT","Good liquidity, consistent spread opportunities"],
        ["ARB/USDT:USDT","Backtest: +$8/mo, 95% win rate"],
        ["OP/USDT:USDT","Backtest: +$7/mo, 95% win rate"],
        ["NEAR/USDT:USDT","Backtest: +$6/mo, 91% win rate"],
        ["DOT/USDT:USDT","Backtest: +$4/mo, 93% win rate"],
        ["ATOM/USDT:USDT","Backtest: +$2/mo, 90% win rate"],
        ["WIF/USDT:USDT","Meme token — high volatility, wide spreads"],
        ["LINK/USDT:USDT","Oracle token — spreads spike during DeFi activity"],
        ["LTC/USDT:USDT","Older chain — wider spreads than BTC/ETH"],
        ["INJ/USDT:USDT","Injective — shallower order books = wider spreads"],
        ["FIL/USDT:USDT","Filecoin — volatile, historically good opportunities"],
        ["TIA/USDT:USDT","Celestia — newest token, widest spreads of the set"],
    ],
    col_widths=[2.2, 4.8]
)

h2("13.2 Inactive Pairs (Pending API Keys / Testing)")
two_col_table(
    ["Exchange Pair","Best Projected Pairs","Notes"],
    [
        ["Binance vs OKX","NEAR (+$18/mo), ARB (+$11/mo), DOT (+$11/mo)","Enable after OKX API key setup"],
        ["Gate vs Bitget","ARB (+$41/mo), OP (+$32/mo)","Enable after 2-day paper test"],
        ["Binance vs Gate","NEAR (+$29/mo), DOT (+$22/mo), ARB (+$21/mo)","Enable after 2-day paper test"],
        ["Binance vs Bitget","OP (+$27/mo), ARB (+$26/mo), DOT (+$25/mo)","Enable after 2-day paper test"],
        ["Binance vs MEXC","ETH, SOL, ARB, DOGE — lower threshold 0.10%","Enable after MEXC API key setup"],
    ],
    col_widths=[1.8, 3.0, 2.2]
)

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 14. UTILITY SCRIPTS
# ══════════════════════════════════════════════════════════════════════════════
h1("14. Utility Scripts")

h2("14.1 backtest.py")
body(
    "Standalone historical backtest using CCXT public endpoints (no API keys). "
    "Fetches OHLCV candles from both Binance Futures and Bybit for all active pairs, "
    "aligns candles by timestamp, and replays the entry/exit logic."
)
two_col_table(
    ["CLI Flag","Default","Purpose"],
    [
        ["--days","90","Lookback window in days"],
        ["--tf","1h","Candle timeframe (1h for history, 15m for detail)"],
        ["--pair","all active","Filter to a single symbol (e.g. DOGE/USDT)"],
        ["--csv","off","Export trade log to data/backtest_trades.csv"],
    ],
    col_widths=[1.4, 1.4, 4.2]
)
body(
    "Output includes: total trades, trades/day, win rate, average spread, average hold time, "
    "total P&L, max drawdown, monthly projection vs target, exit reason breakdown, per-pair "
    "summary, and what-if scenarios at different spread levels."
)
info_box("Limitation:",
    "Bybit REST API caps at ~1,999 candles regardless of requested window. "
    "At 5m resolution that's ~7 days. Use --tf 1h for up to 200 days of history. "
    "Close-price comparison also understates intra-candle convergence — "
    "paper mode (live prices every 10s) remains the most accurate forward test.")

h2("14.2 scan_pairs.py")
body(
    "Scans potential trading pairs across exchange combinations to identify the most "
    "profitable pairs to add. Fetches tickers from multiple exchanges simultaneously "
    "and ranks pairs by historical spread frequency and magnitude. Used to populate "
    "the inactive pairs list in config.yaml with projected monthly income figures."
)

h2("14.3 progress.py")
body(
    "CLI dashboard showing real-time performance. Queries the database directly and displays:"
)
bullet("Current month net profit with progress bar toward $600 target")
bullet("Daily and weekly P&L")
bullet("Total trades, average spread, average net per trade")
bullet("Open positions with unrealised P&L estimates")
bullet("Withdrawal history")
bullet("Monthly projection extrapolated from current daily average")

h2("14.4 bot.py CLI Flags")
two_col_table(
    ["Flag","Action"],
    [
        ["(no flags)","Live trading mode — real API orders, real funds"],
        ["--paper","Paper trading mode — real prices, simulated fills"],
        ["--kill","Emergency kill switch — close all positions immediately"],
        ["--report","Print 30-day performance report and exit"],
        ["--status","Print current open positions and bot status and exit"],
        ["--config PATH","Use alternative config file path"],
    ],
    col_widths=[2.0, 5.0]
)

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 15. WEB DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
h1("15. Web Dashboard (dashboard/app.py)")
body(
    "A lightweight Flask application serving a read-only web dashboard on localhost:5000. "
    "Runs in a background thread. All endpoints are JSON REST APIs consumed by the "
    "HTML/JS frontend in dashboard/templates/index.html."
)

two_col_table(
    ["Endpoint","Method","Returns"],
    [
        ["/","GET","Renders index.html (dashboard UI)"],
        ["/api/summary","GET","HWM, 30d net/gross, trade count, open positions, latest balance"],
        ["/api/reports","GET","Last 30 daily report rows"],
        ["/api/positions","GET","All currently open trades"],
        ["/api/risk_events","GET","Last 50 risk events (stop-losses, halts, ATR spikes)"],
        ["/api/withdrawals","GET","Last 100 withdrawal/allocation records"],
        ["/api/alerts","GET","Last 50 sent alerts"],
    ],
    col_widths=[2.4, 1.0, 3.6]
)

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 16. FEE CALCULATION — COMPLETE REFERENCE
# ══════════════════════════════════════════════════════════════════════════════
h1("16. Fee Calculation — Complete Reference")

h2("16.1 Spread Capture Strategy Fees")
code_block([
    "# 4 orders per round trip:",
    "# Order 1: Open Leg A (limit, maker rate)",
    "# Order 2: Open Leg B (limit, maker rate)",
    "# Order 3: Close Leg A (limit, maker rate — market fallback if timeout)",
    "# Order 4: Close Leg B (limit, maker rate — market fallback if timeout)",
    "",
    "fee_a    = exchange_fees[leg_a_exchange]   # from config",
    "fee_b    = exchange_fees[leg_b_exchange]   # from config",
    "FEE_RATE = (fee_a + fee_b) * 2",
    "fees     = notional_usd * FEE_RATE",
])

h2("16.2 Fee Rates by Exchange Combination")
two_col_table(
    ["Exchange A","Exchange B","Fee A","Fee B","FEE_RATE","Break-Even Entry"],
    [
        ["Binance","Bybit","0.020%","0.020%","0.080%","0.09% (use 0.11% for margin)"],
        ["Binance","Gate","0.020%","0.015%","0.070%","0.08% (use 0.10%)"],
        ["Binance","Bitget","0.020%","0.020%","0.080%","0.09% (use 0.11%)"],
        ["Binance","MEXC","0.020%","0.000%","0.040%","0.05% (use 0.10% per config)"],
        ["Binance","OKX","0.020%","0.020%","0.080%","0.09% (use 0.11%)"],
        ["Gate","Bitget","0.015%","0.020%","0.070%","0.08% (use 0.10%)"],
    ],
    col_widths=[1.2, 1.2, 0.9, 0.9, 1.2, 2.6]
)

h2("16.3 Basis Strategy Fees")
code_block([
    "# 4 orders per round trip (different rates — spot uses taker):",
    "# Order 1: Buy spot (market, TAKER 0.10%)",
    "# Order 2: Short perp (limit, MAKER 0.02%)",
    "# Order 3: Sell spot (market, TAKER 0.10%)",
    "# Order 4: Cover perp (limit, MAKER 0.02%)",
    "",
    "FEE_RATE_BASIS = 0.0024   # 0.24% total round-trip",
    "fees = notional * FEE_RATE_BASIS",
])

h2("16.4 Break-Even Analysis")
two_col_table(
    ["Notional","Fee Rate","Fees","Min Gross to Profit","Min Spread (7x lev, 60% deploy)"],
    [
        ["$2,000","0.08%","$1.60","$1.60+","0.11% entry, 0.01% exit = $2.00 gross"],
        ["$5,000","0.08%","$4.00","$4.00+","Same threshold — larger notional amplifies profit"],
        ["$10,000","0.08%","$8.00","$8.00+","0.11% entry = $11 gross, $3 net minimum"],
        ["$10,000","0.04% (MEXC)","$4.00","$4.00+","0.10% entry = $10 gross, $6 net minimum"],
    ],
    col_widths=[1.4, 1.2, 1.0, 2.0, 3.4]
)

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 17. END-TO-END TRADE WALKTHROUGH
# ══════════════════════════════════════════════════════════════════════════════
h1("17. End-to-End Trade Walkthrough")
body("A complete trace of one profitable trade from detection to database record:")

h2("Step 1 — Scheduler fires _strategy_tick()")
body("APScheduler calls BotEngine._strategy_tick() every 10 seconds.")

h2("Step 2 — _scan_for_entries() iterates pairs")
body("For pair TIA/USDT:USDT (Binance vs Bybit):")
code_block([
    "ticker_binance = {'ask': 6.420, 'bid': 6.418, 'last': 6.419}",
    "ticker_bybit   = {'ask': 6.430, 'bid': 6.435, 'last': 6.432}",
    "",
    "spread_ab = (6.435 - 6.420) / 6.420 = 0.002336  (0.234%)",
    "spread_ba = (6.418 - 6.430) / 6.430 = -0.001866  (negative)",
    "",
    "Best direction: buy_a_sell_b  (0.234% > -0.187%)",
    "spread = 0.002336  >=  min_spread_entry 0.0011  --> PROCEED",
])

h2("Step 3 — Sizing")
code_block([
    "tradeable = ($4,000 - $600) * (1 - 0.15) = $2,890",
    "concurrent = min(3 + 1, 20) = 4  (3 positions already open)",
    "deploy_pct = 0.60  (medium spread: 0.08% <= 0.234% < 0.20%)",
    "leverage   = 7x    (spread < medium_threshold 0.20%)",
    "notional   = $2,890 * 7 * 0.60 / 4 = $3,031",
])

h2("Step 4 — Order Execution")
code_block([
    "amount_a = $3,031 / 6.420 = 472.1 TIA  (buy on Binance at ask)",
    "amount_b = $3,031 / 6.435 = 471.0 TIA  (short on Bybit at bid)",
    "",
    "Leg A: place_order(Binance, 'buy',  472.1, limit, 6.420)  --> filled",
    "Leg B: place_order(Bybit,   'sell', 471.0, limit, 6.435)  --> filled",
    "",
    "entry_price_a = 6.420  (Binance ask)",
    "entry_price_b = 6.435  (Bybit bid)",
    "direction     = 'buy_a_sell_b'",
])

h2("Step 5 — Trade Logged to DB")
code_block([
    "log_trade({",
    "    'pair': 'TIA/USDT:USDT_TIA/USDT:USDT',",
    "    'notional_usd': 3031.0,",
    "    'spread_pct': 0.002336,",
    "    'direction': 'buy_a_sell_b',",
    "    'entry_price_a': 6.420,",
    "    'entry_price_b': 6.435,",
    "    'amount_a': 472.1,",
    "    'amount_b': 471.0,",
    "    'status': 'open',",
    "})",
])

h2("Step 6 — Spread Compresses (2 hours later)")
code_block([
    "ticker_binance = {'bid': 6.428}  # price moved up",
    "ticker_bybit   = {'ask': 6.430}  # prices converged",
    "",
    "current_spread = (6.428 - 6.430) / 6.430 = -0.000311  < min_spread_exit 0.0001",
    "--> EXIT: spread_collapse",
])

h2("Step 7 — Close Orders")
code_block([
    "exit_price_a = 6.428  (Binance bid — sell long)",
    "exit_price_b = 6.430  (Bybit ask  — cover short)",
    "",
    "close_a: place_order(Binance, sell,  472.1, limit, 6.428) --> filled",
    "close_b: place_order(Bybit,   buy,   471.0, limit, 6.430) --> filled",
])

h2("Step 8 — P&L Calculation")
code_block([
    "# buy_a_sell_b formula:",
    "gross = (exit_price_a - entry_price_a) * amount_a   # Long Binance P&L",
    "      + (entry_price_b - exit_price_b) * amount_b   # Short Bybit P&L",
    "",
    "gross = (6.428 - 6.420) * 472.1  +  (6.435 - 6.430) * 471.0",
    "      = 0.008 * 472.1            +  0.005 * 471.0",
    "      = 3.777                    +  2.355",
    "      = $6.132",
    "",
    "FEE_RATE = (0.0002 + 0.0002) * 2 = 0.0008",
    "fees     = $3,031 * 0.0008 = $2.425",
    "",
    "funding_collected = 0  (held < 8h)",
    "",
    "net = $6.132 + $0.00 - $2.425 = $3.71",
])

h2("Step 9 — DB Updated")
code_block([
    "update_trade(id, {",
    "    'status':       'closed',",
    "    'gross_profit':  6.132,",
    "    'fees':          2.425,",
    "    'net_profit':    3.707,",
    "    'notes':         'closed:spread_collapse entry=0.234% exit=-0.031%'",
    "})",
])

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 18. INCOME PROJECTION
# ══════════════════════════════════════════════════════════════════════════════
h1("18. Income Projections")

h2("18.1 Conservative Estimate (Binance + Bybit only, current)")
two_col_table(
    ["Metric","Value","Assumption"],
    [
        ["Trades per day","8-12","15 active pairs, 10s scan, 0.11% threshold"],
        ["Average net per trade","$2.50 - $4.50","Spread 0.11-0.20%, notional ~$2,000-3,000"],
        ["Daily net","$20 - $50","10 trades x $2-5 average"],
        ["Monthly net (30 days)","$600 - $1,500","Target is $600+"],
        ["Win rate (backtest)","90-95%","Spread capture is directional-risk-free"],
    ],
    col_widths=[2.4, 1.8, 2.8]
)

h2("18.2 Expanded Estimate (All 5 exchanges active)")
two_col_table(
    ["Exchange Pair","Best Pairs","Projected Monthly"],
    [
        ["Binance + Bybit (active)","15 pairs","$600 - $1,500"],
        ["Binance + OKX","NEAR, ARB, DOT, ATOM","$56/mo from top 4"],
        ["Gate + Bitget","ARB, OP","$73/mo from top 2"],
        ["Binance + Gate","NEAR, DOT, ARB, ATOM","$90/mo from top 4"],
        ["Binance + Bitget","OP, ARB, DOT, WIF","$101/mo from top 4"],
        ["Binance + MEXC","ETH, SOL, ARB, DOGE","Halved fees — higher margin"],
        ["TOTAL (all active)","~40+ pairs","$1,000 - $2,500/month est."],
    ],
    col_widths=[2.4, 2.2, 2.4]
)

h2("18.3 Compounding Effect")
two_col_table(
    ["Month","Capital (40% reinvest)","Est. Monthly Income"],
    [
        ["Month 1","$4,000","$600"],
        ["Month 2","$4,240 (+$240)","$636"],
        ["Month 3","$4,494 (+$254)","$674"],
        ["Month 6","$5,338","$801"],
        ["Month 12","$7,562","$1,134"],
    ],
    col_widths=[1.6, 2.4, 3.0]
)

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 19. ENVIRONMENT SETUP
# ══════════════════════════════════════════════════════════════════════════════
h1("19. Environment Setup (.env Reference)")
body("All secrets are loaded from a .env file in the project root via python-dotenv.")
code_block([
    "# Exchange API Keys",
    "BINANCE_API_KEY=your_binance_api_key",
    "BINANCE_API_SECRET=your_binance_api_secret",
    "BYBIT_API_KEY=your_bybit_api_key",
    "BYBIT_API_SECRET=your_bybit_api_secret",
    "",
    "# Future exchanges (set when activating)",
    "OKX_API_KEY=",
    "OKX_API_SECRET=",
    "OKX_PASSPHRASE=",
    "GATE_API_KEY=",
    "GATE_API_SECRET=",
    "BITGET_API_KEY=",
    "BITGET_API_SECRET=",
    "MEXC_API_KEY=",
    "MEXC_API_SECRET=",
    "",
    "# Alerts — SMS",
    "TWILIO_ACCOUNT_SID=",
    "TWILIO_AUTH_TOKEN=",
    "TWILIO_FROM_NUMBER=",
    "ALERT_PHONE_NUMBER=",
    "",
    "# Alerts — Email",
    "SMTP_HOST=smtp.gmail.com",
    "SMTP_PORT=587",
    "SMTP_USER=your_email@gmail.com",
    "SMTP_PASS=your_app_password",
    "ALERT_EMAIL=destination@email.com",
    "",
    "# Profit Extraction",
    "COLD_WALLET_ADDRESS=your_usdt_trc20_address",
])

page_break()

# ══════════════════════════════════════════════════════════════════════════════
# 20. KNOWN LIMITATIONS & IMPORTANT NOTES
# ══════════════════════════════════════════════════════════════════════════════
h1("20. Known Limitations & Important Notes")

h2("20.1 Critical Engineering Notes")
bullet("entry_price_a/b naming: For buy_b_sell_a direction, entry_price_a stores the Bybit ask (long leg), "
    "and entry_price_b stores the Binance bid (short leg). The 'a/b' suffix follows the signal dict "
    "slot order, NOT the exchange name. This is intentional but requires care when reading the P&L code.")
bullet("Database migrations: New columns are added via ALTER TABLE in the _migrations list in database.py. "
    "These are wrapped in try/except so they silently succeed on fresh databases and silently skip on "
    "databases that already have the column.")
bullet("Paper balance allocation: In paper mode, each exchange returns only its share of total capital "
    "(binance=$2,400, bybit=$1,600 on a $4,000 account). Without this, get_total_equity() would "
    "sum all exchanges and return $8,000, showing a false high-water mark.")
bullet("WS cache bid/ask fallback: If bid or ask is not present in a WS ticker update, the code "
    "falls back to last price for both. This creates a zero computed spread. The REST fallback "
    "will provide proper bid/ask in this case.")

h2("20.2 Current Limitations")
bullet("No real withdrawal execution: weekly_withdrawal() logs the intent but does not call "
    "any exchange withdrawal API. The operator must manually execute withdrawals from the exchange.")
bullet("Funding rate accuracy: Funding income is estimated at entry using the current rate * notional * settlements. "
    "The actual payment depends on position size at settlement time, which may differ.")
bullet("Single-process: All strategies run in the same Python process. A crash will halt all trading. "
    "Use a process manager (PM2, systemd) in production.")
bullet("Bybit OHLCV cap: Bybit REST API returns max ~1,999 candles. Backtests using --tf 5m "
    "only cover ~7 days regardless of --days parameter. Use --tf 1h for longer history.")

h2("20.3 Recommended Pre-Live Checklist")
bullet("Run paper mode for minimum 7 days, verify P&L is consistently positive")
bullet("Confirm min_spread_entry (0.11%) is above break-even (fees + exit threshold = 0.09%)")
bullet("Set API key permissions: Futures trading only — NO withdrawal permissions on exchange")
bullet("Enable BNB fee discount on Binance (reduces fee from 0.020% to 0.018%)")
bullet("Park $600 reserve in Binance Simple Earn for yield (not used as margin)")
bullet("Configure cold wallet address in COLD_WALLET_ADDRESS env var before first Friday")
bullet("Test kill switch in paper mode: python bot.py --kill")

# ── Save ──────────────────────────────────────────────────────────────────────
out_path = r"C:\Users\VISAL\Desktop\Delta_Neutral_Bot_Technical_Report.docx"
doc.save(out_path)
print(f"Report saved to: {out_path}")
