"""generate_guide.py — Generate the Real-Money Setup Guide PDF using reportlab."""
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, black, white
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

# ── Colour palette ─────────────────────────────────────────────────────────────
DARK_BG    = HexColor('#0d1117')
ACCENT     = HexColor('#2ea043')   # green
RED        = HexColor('#f85149')
YELLOW     = HexColor('#d29922')
BLUE       = HexColor('#58a6ff')
LIGHT_GREY = HexColor('#8b949e')
BORDER     = HexColor('#30363d')
CARD_BG    = HexColor('#161b22')
WHITE      = HexColor('#e6edf3')
ORANGE     = HexColor('#e3b341')

W, H = A4   # 210 x 297 mm

# ── Styles ────────────────────────────────────────────────────────────────────
styles = getSampleStyleSheet()

def S(name, **kw):
    base = styles.get(name, styles['Normal'])
    return ParagraphStyle(name + str(id(kw)), parent=base, **kw)

cover_title   = S('Normal', fontSize=28, textColor=WHITE,  leading=36, alignment=TA_CENTER, fontName='Helvetica-Bold')
cover_sub     = S('Normal', fontSize=13, textColor=ACCENT, leading=18, alignment=TA_CENTER, fontName='Helvetica')
cover_meta    = S('Normal', fontSize=10, textColor=LIGHT_GREY, leading=14, alignment=TA_CENTER)

h1_style = S('Normal', fontSize=18, textColor=ACCENT,  leading=24, fontName='Helvetica-Bold', spaceBefore=14, spaceAfter=6)
h2_style = S('Normal', fontSize=13, textColor=BLUE,    leading=18, fontName='Helvetica-Bold', spaceBefore=10, spaceAfter=4)
h3_style = S('Normal', fontSize=11, textColor=ORANGE,  leading=15, fontName='Helvetica-Bold', spaceBefore=8,  spaceAfter=3)

body   = S('Normal', fontSize=9.5, textColor=WHITE,      leading=15, fontName='Helvetica',       spaceAfter=4)
body_s = S('Normal', fontSize=8.5, textColor=WHITE,      leading=13, fontName='Helvetica',       spaceAfter=3)
warn   = S('Normal', fontSize=9.5, textColor=RED,        leading=14, fontName='Helvetica-Bold',  spaceAfter=4)
tip    = S('Normal', fontSize=9.5, textColor=ACCENT,     leading=14, fontName='Helvetica',       spaceAfter=4)
code   = S('Normal', fontSize=8.5, textColor=ACCENT,     leading=13, fontName='Courier',         spaceAfter=3, backColor=CARD_BG, leftIndent=8, rightIndent=8)
label  = S('Normal', fontSize=9,   textColor=LIGHT_GREY, leading=13, fontName='Helvetica-Bold')
note   = S('Normal', fontSize=8.5, textColor=YELLOW,     leading=13, fontName='Helvetica',       spaceAfter=3)

def bullet(text, style=body, symbol='•'):
    return Paragraph(f'<font color="#2ea043">{symbol}</font>  {text}', style)

def check(text, done=True):
    sym = '✓' if done else '○'
    col = '#2ea043' if done else '#8b949e'
    return Paragraph(f'<font color="{col}">{sym}</font>  {text}', body)

def HR():
    return HRFlowable(width='100%', thickness=0.5, color=BORDER, spaceAfter=8, spaceBefore=4)

def section_header(num, title):
    return [
        Spacer(1, 6),
        Paragraph(f'PHASE {num} — {title}', h1_style),
        HR(),
    ]

def warning_box(text):
    data = [[Paragraph(f'⚠  {text}', S('Normal', fontSize=9, textColor=HexColor('#f0883e'), leading=13, fontName='Helvetica-Bold'))]]
    t = Table(data, colWidths=[W - 60*mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), HexColor('#272115')),
        ('BOX',        (0,0), (-1,-1), 0.8, HexColor('#e3b341')),
        ('TOPPADDING',    (0,0), (-1,-1), 7),
        ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ('LEFTPADDING',   (0,0), (-1,-1), 10),
    ]))
    return t

def success_box(text):
    data = [[Paragraph(f'✓  {text}', S('Normal', fontSize=9, textColor=ACCENT, leading=13, fontName='Helvetica-Bold'))]]
    t = Table(data, colWidths=[W - 60*mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), HexColor('#112218')),
        ('BOX',        (0,0), (-1,-1), 0.8, ACCENT),
        ('TOPPADDING',    (0,0), (-1,-1), 7),
        ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ('LEFTPADDING',   (0,0), (-1,-1), 10),
    ]))
    return t

def kv_table(rows_data, col1=70*mm, col2=None):
    col2 = col2 or (W - 60*mm - col1)
    tdata = [[Paragraph(k, label), Paragraph(v, body_s)] for k,v in rows_data]
    t = Table(tdata, colWidths=[col1, col2])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), CARD_BG),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [CARD_BG, HexColor('#1c2128')]),
        ('BOX',        (0,0), (-1,-1), 0.5, BORDER),
        ('LINEBELOW',  (0,0), (-1,-2), 0.3, BORDER),
        ('TOPPADDING',    (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING',   (0,0), (-1,-1), 8),
    ]))
    return t

# ── Build document ────────────────────────────────────────────────────────────
out_path = Path('Delta_Neutral_Bot_Real_Money_Guide.pdf')
doc = SimpleDocTemplate(
    str(out_path), pagesize=A4,
    leftMargin=25*mm, rightMargin=25*mm,
    topMargin=20*mm,  bottomMargin=20*mm,
)

def on_page(canvas, doc):
    """Dark background + footer on every page."""
    canvas.saveState()
    canvas.setFillColor(DARK_BG)
    canvas.rect(0, 0, W, H, fill=1, stroke=0)
    # Footer
    canvas.setFillColor(LIGHT_GREY)
    canvas.setFont('Helvetica', 7)
    canvas.drawString(25*mm, 12*mm, 'Delta-Neutral Bot — Real Money Setup Guide — CONFIDENTIAL')
    canvas.drawRightString(W - 25*mm, 12*mm, f'Page {doc.page}')
    canvas.restoreState()

story = []

# ═══════════════════════════════════════════════════════════════════════════════
# COVER PAGE
# ═══════════════════════════════════════════════════════════════════════════════
story += [
    Spacer(1, 35*mm),
    Paragraph('DELTA-NEUTRAL BOT', cover_title),
    Spacer(1, 4*mm),
    Paragraph('Real Money Setup Guide', cover_sub),
    Spacer(1, 3*mm),
    Paragraph('Complete Step-by-Step — From Zero to Live Trading', cover_meta),
    Spacer(1, 12*mm),
    HR(),
    Spacer(1, 6*mm),
]

# Stats box on cover
cover_stats = [
    ['All-Time Net (Paper)', '$2,829',   'Win Rate', '61.5%'],
    ['Total Trades',         '2,670',    'Best Day',  '$1,042'],
    ['Active Pairs',         '18',       'Strategy',  'Spread Arb'],
]
ct = Table(cover_stats, colWidths=[45*mm, 35*mm, 45*mm, 35*mm])
ct.setStyle(TableStyle([
    ('BACKGROUND',    (0,0), (-1,-1), CARD_BG),
    ('TEXTCOLOR',     (0,0), (0,-1), LIGHT_GREY),
    ('TEXTCOLOR',     (2,0), (2,-1), LIGHT_GREY),
    ('TEXTCOLOR',     (1,0), (1,-1), ACCENT),
    ('TEXTCOLOR',     (3,0), (3,-1), ACCENT),
    ('FONTNAME',      (0,0), (-1,-1), 'Helvetica'),
    ('FONTNAME',      (1,0), (1,-1), 'Helvetica-Bold'),
    ('FONTNAME',      (3,0), (3,-1), 'Helvetica-Bold'),
    ('FONTSIZE',      (0,0), (-1,-1), 9),
    ('FONTSIZE',      (1,0), (1,-1), 11),
    ('FONTSIZE',      (3,0), (3,-1), 11),
    ('BOX',           (0,0), (-1,-1), 0.8, BORDER),
    ('INNERGRID',     (0,0), (-1,-1), 0.3, BORDER),
    ('TOPPADDING',    (0,0), (-1,-1), 8),
    ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ('LEFTPADDING',   (0,0), (-1,-1), 10),
    ('ALIGN',         (1,0), (1,-1), 'RIGHT'),
    ('ALIGN',         (3,0), (3,-1), 'RIGHT'),
]))
story.append(ct)
story += [
    Spacer(1, 8*mm),
    HR(),
    Spacer(1, 4*mm),
    Paragraph(f'Generated: {datetime.now().strftime("%B %d, %Y")}   |   Strategy: Binance / Bybit Perpetual Spread Capture   |   Capital: $4,000 USDT', cover_meta),
    Spacer(1, 3*mm),
    Paragraph('CONFIDENTIAL — Contains API credentials and trading configuration', S('Normal', fontSize=8, textColor=RED, alignment=TA_CENTER)),
    PageBreak(),
]

# ═══════════════════════════════════════════════════════════════════════════════
# TABLE OF CONTENTS
# ═══════════════════════════════════════════════════════════════════════════════
story += [
    Paragraph('Table of Contents', h1_style),
    HR(),
    Spacer(1, 3*mm),
]
toc = [
    ('Phase 1', 'Exchange Account Setup (Binance & Bybit)', '3'),
    ('Phase 2', 'Deposit Money to Exchanges', '4'),
    ('Phase 3', 'Configure Futures Wallets & Margin Mode', '5'),
    ('Phase 4', 'Create API Keys (Trade-Only)', '6'),
    ('Phase 5', 'VPS Server Setup', '7'),
    ('Phase 6', 'Install & Configure the Bot on VPS', '9'),
    ('Phase 7', 'Pre-Launch Safety Checklist', '11'),
    ('Phase 8', 'Go Live — Switch from Paper to Real', '12'),
    ('Phase 9', 'First 24 Hours — What to Watch', '13'),
    ('Phase 10', 'Ongoing Maintenance & Monitoring', '14'),
    ('Appendix A', 'Emergency Procedures & Kill Switch', '15'),
    ('Appendix B', 'Config Reference & Active Pairs', '16'),
    ('Appendix C', 'What Can Go Wrong & How to Fix It', '17'),
]
for phase, title, page in toc:
    row = [[
        Paragraph(f'<b>{phase}</b>', S('Normal', fontSize=9, textColor=ACCENT, fontName='Helvetica-Bold')),
        Paragraph(title, body_s),
        Paragraph(page, S('Normal', fontSize=9, textColor=LIGHT_GREY, alignment=TA_RIGHT)),
    ]]
    rt = Table(row, colWidths=[30*mm, 115*mm, 15*mm])
    rt.setStyle(TableStyle([
        ('LINEBELOW', (0,0), (-1,-1), 0.3, BORDER),
        ('TOPPADDING',    (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(rt)
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — EXCHANGE ACCOUNT SETUP
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header(1, 'EXCHANGE ACCOUNT SETUP')
story += [
    Paragraph('You need accounts on two exchanges: <b>Binance</b> (60% of capital) and <b>Bybit</b> (40% of capital). Both must be fully verified before you can trade futures.', body),
    Spacer(1, 4*mm),
    Paragraph('1.1  Create Binance Account', h2_style),
]
steps_b = [
    ('Step 1', 'Go to <b>binance.com</b> — use referral link if you have one (reduces fees 10–20%)'),
    ('Step 2', 'Click <b>Register</b> → use your email address → set a strong password (12+ chars, symbols)'),
    ('Step 3', 'Verify your email — click the link sent to your inbox'),
    ('Step 4', 'Enable <b>2FA immediately</b> — go to Security → Google Authenticator → scan QR code with the app'),
    ('Step 5', 'Complete <b>KYC (Identity Verification)</b> → Account → Identification → upload passport or national ID + selfie'),
    ('Step 6', 'Wait for KYC approval — usually 30 minutes to 24 hours'),
    ('Step 7', 'Go to <b>Derivatives → USDT-M Futures</b> → click "Open Now" to enable futures trading → agree to terms'),
]
for step, desc in steps_b:
    story.append(kv_table([(step, desc)], col1=18*mm))
    story.append(Spacer(1, 1*mm))

story += [
    Spacer(1, 4*mm),
    warning_box('Enable Google Authenticator 2FA — NOT SMS. SMS can be SIM-swapped. Google Auth is stored offline on your phone.'),
    Spacer(1, 4*mm),
    Paragraph('1.2  Create Bybit Account', h2_style),
]
steps_by = [
    ('Step 1', 'Go to <b>bybit.com</b> → Register with email'),
    ('Step 2', 'Verify email → set strong password → enable Google Authenticator 2FA'),
    ('Step 3', 'Complete <b>KYC</b> → Account & Security → Identity Verification → ID + selfie'),
    ('Step 4', 'Wait for KYC approval (usually faster than Binance, often 15–60 min)'),
    ('Step 5', 'Go to <b>Derivatives → USDT Perpetual</b> to confirm futures is available'),
]
for step, desc in steps_by:
    story.append(kv_table([(step, desc)], col1=18*mm))
    story.append(Spacer(1, 1*mm))

story += [
    Spacer(1, 3*mm),
    success_box('KYC is required on both exchanges before you can deposit or trade. Do this first — it can take up to 24 hours. Do not skip it.'),
    PageBreak(),
]

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — DEPOSIT MONEY
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header(2, 'DEPOSIT MONEY TO EXCHANGES')
story += [
    Paragraph('Target: <b>$2,400 USDT on Binance</b> and <b>$1,600 USDT on Bybit</b> (total $4,000). The bot needs USDT — the stablecoin pegged to the US dollar.', body),
    Spacer(1, 3*mm),
    Paragraph('2.1  Recommended Deposit Method — Buy USDT via P2P or Card', h2_style),
    Paragraph('If you are starting from cash (AUD/USD/EUR), the easiest path:', body),
    Spacer(1,2*mm),
]

methods = [
    ['Method', 'Fee', 'Speed', 'Best For'],
    ['Binance P2P (buy USDT peer-to-peer)', '0% trading fee\n(small price premium)', '15–30 min', 'AUD/USD — best rate'],
    ['Credit/Debit Card on Binance', '1.8–3%', 'Instant', 'Convenience, small amounts'],
    ['Bank Transfer (SWIFT/local)', '0–$15 flat fee', '1–3 business days', 'Large amounts'],
    ['Transfer from Coinbase/Kraken', 'Withdrawal fee\n(TRC20 = ~$1)', '5–30 min', 'Already have crypto'],
]
mt = Table(methods, colWidths=[55*mm, 35*mm, 28*mm, 42*mm])
mt.setStyle(TableStyle([
    ('BACKGROUND',    (0,0), (-1,0), HexColor('#1c2128')),
    ('TEXTCOLOR',     (0,0), (-1,0), ACCENT),
    ('FONTNAME',      (0,0), (-1,0), 'Helvetica-Bold'),
    ('FONTSIZE',      (0,0), (-1,-1), 8.5),
    ('ROWBACKGROUNDS',(0,1), (-1,-1), [CARD_BG, HexColor('#1c2128')]),
    ('TEXTCOLOR',     (0,1), (-1,-1), WHITE),
    ('BOX',           (0,0), (-1,-1), 0.5, BORDER),
    ('INNERGRID',     (0,0), (-1,-1), 0.3, BORDER),
    ('TOPPADDING',    (0,0), (-1,-1), 6),
    ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ('LEFTPADDING',   (0,0), (-1,-1), 7),
    ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
]))
story.append(mt)

story += [
    Spacer(1, 4*mm),
    Paragraph('2.2  Deposit USDT to Binance ($2,400)', h2_style),
    kv_table([
        ('Step 1', 'Log into Binance → click <b>Wallet</b> → <b>Deposit</b>'),
        ('Step 2', 'Select coin: <b>USDT</b> → Select network: <b>TRC20</b> (cheapest, ~$1 fee, 5 min)'),
        ('Step 3', 'Copy the deposit address — send your USDT here from wherever it is'),
        ('Step 4', 'Wait for 20+ confirmations — usually 5–15 minutes on TRC20'),
        ('Step 5', 'Check Spot wallet balance — should show $2,400 USDT'),
    ], col1=18*mm),
    Spacer(1, 3*mm),
    warning_box('Always send a small test amount first ($10–20) before sending the full $2,400. If the test arrives, send the rest.'),
    Spacer(1, 4*mm),
    Paragraph('2.3  Deposit USDT to Bybit ($1,600)', h2_style),
    kv_table([
        ('Step 1', 'Log into Bybit → Assets → Deposit'),
        ('Step 2', 'Select coin: <b>USDT</b> → Network: <b>TRC20</b>'),
        ('Step 3', 'Copy deposit address → send $1,600 USDT'),
        ('Step 4', 'Wait for confirmations — check Assets → Funding Account balance'),
    ], col1=18*mm),
    Spacer(1, 3*mm),
    Paragraph('Note: Bybit deposits land in the Funding Account — you must transfer to the Derivatives account (done in Phase 3).', note),
    PageBreak(),
]

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3 — FUTURES WALLET & MARGIN MODE
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header(3, 'CONFIGURE FUTURES WALLETS & MARGIN MODE')
story += [
    Paragraph('The bot trades perpetual futures contracts. Your USDT must be in the futures wallet on each exchange, and margin mode must be set to <b>Cross Margin</b>.', body),
    Spacer(1, 3*mm),
    Paragraph('3.1  Binance — Transfer Spot → USDT-M Futures', h2_style),
    kv_table([
        ('Step 1', 'Binance → Wallet → <b>Transfer</b>'),
        ('Step 2', 'From: <b>Spot</b>   →   To: <b>USD-M Futures</b>'),
        ('Step 3', 'Amount: <b>$2,400 USDT</b> → Confirm'),
        ('Step 4', 'Go to Derivatives → USDT-M Futures → verify balance shows $2,400'),
        ('Step 5', 'Set <b>Cross Margin</b>: In futures, click the margin mode button (top left of trading screen) → select Cross → confirm'),
    ], col1=18*mm),
    Spacer(1, 4*mm),
    Paragraph('3.2  Bybit — Transfer Funding → Derivatives', h2_style),
    kv_table([
        ('Step 1', 'Bybit → Assets → <b>Transfer</b>'),
        ('Step 2', 'From: <b>Funding</b>   →   To: <b>Derivatives</b>'),
        ('Step 3', 'Amount: <b>$1,600 USDT</b> → Confirm'),
        ('Step 4', 'Go to Derivatives → USDT Perpetual → verify balance'),
        ('Step 5', 'Set <b>Cross Margin</b>: In trading interface → Margin Mode → Cross → confirm'),
    ], col1=18*mm),
    Spacer(1, 4*mm),
    warning_box('CROSS MARGIN is critical. Do NOT use Isolated Margin. Cross margin shares the full balance across positions, which is required for the bot\'s capital efficiency and risk management to work correctly.'),
    Spacer(1, 4*mm),
    Paragraph('3.3  Verify Leverage Settings', h2_style),
    Paragraph('The bot sets leverage per-trade (7x, 8x, or 10x depending on spread). You do <b>not</b> need to manually set leverage on each pair — the bot handles it via the API. However, ensure your account has leverage enabled (not restricted to 1x by default on new accounts).', body),
    success_box('After Phase 3: Binance Futures shows $2,400 USDT | Bybit Derivatives shows $1,600 USDT | Both on Cross Margin mode.'),
    PageBreak(),
]

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4 — API KEYS
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header(4, 'CREATE API KEYS (TRADE-ONLY)')
story += [
    Paragraph('API keys allow the bot to place orders programmatically. You must create <b>trade-only</b> keys — no withdrawal permission, ever.', body),
    Spacer(1, 3*mm),
    Paragraph('4.1  Binance API Key', h2_style),
    kv_table([
        ('Step 1', 'Binance → Account (top right) → <b>API Management</b>'),
        ('Step 2', 'Click <b>Create API</b> → choose "System generated" → give it a name (e.g., "DeltaBot")'),
        ('Step 3', 'Complete 2FA verification'),
        ('Step 4', 'PERMISSIONS — enable ONLY: <b>✓ Enable Futures</b>'),
        ('Step 5', 'DISABLE everything else: ✗ Spot trading, ✗ Margin trading, ✗ Withdrawal, ✗ Transfer'),
        ('Step 6', 'IP Restriction → <b>Restrict to trusted IPs only</b> → add your VPS IP address (get from Phase 5)'),
        ('Step 7', 'Copy and save the <b>API Key</b> and <b>Secret Key</b> — the secret is shown ONCE only'),
        ('Step 8', 'Open the .env file on your VPS and paste:\n    BINANCE_API_KEY=your_key_here\n    BINANCE_API_SECRET=your_secret_here'),
    ], col1=18*mm),
    Spacer(1, 4*mm),
    Paragraph('4.2  Bybit API Key', h2_style),
    kv_table([
        ('Step 1', 'Bybit → Account → <b>API</b> → Create New Key'),
        ('Step 2', 'Key type: <b>API Transaction</b>'),
        ('Step 3', 'Permissions → <b>Contract</b>: enable Orders, Positions'),
        ('Step 4', 'DISABLE: Assets (no withdrawal), Spot, Wallet transfer'),
        ('Step 5', 'IP Access Restriction → enter your VPS IP'),
        ('Step 6', 'Save API Key and API Secret into .env:\n    BYBIT_API_KEY=your_key_here\n    BYBIT_API_SECRET=your_secret_here'),
    ], col1=18*mm),
    Spacer(1, 4*mm),
    warning_box('NEVER enable withdrawal permissions on API keys. If someone steals your API key, they cannot withdraw your funds without withdrawal permission. This is your most important security layer.'),
    Spacer(1, 3*mm),
    warning_box('NEVER share your API keys or .env file with anyone. Do not commit .env to GitHub or cloud storage.'),
    Spacer(1, 3*mm),
    success_box('After Phase 4: Two sets of API credentials (Binance + Bybit) saved. Both trade-only. Both IP-restricted to VPS.'),
    PageBreak(),
]

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5 — VPS SETUP
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header(5, 'VPS SERVER SETUP')
story += [
    Paragraph('5.1  Why You Need a VPS', h2_style),
    Paragraph('A VPS (Virtual Private Server) is a remote computer that runs 24/7. You need it because:', body),
]
for b in [
    'Your bot must run <b>24 hours a day</b> — your laptop sleeping = bot stops = open positions stuck',
    'A VPS near Singapore has <b>10–30ms latency</b> to Binance/Bybit vs 200–400ms from Australia',
    'Lower latency = orders fill at better prices = higher capture rate (estimated +2–5%)',
    'VPS uptime is 99.9% — far more reliable than a home PC',
]:
    story.append(bullet(b))
story.append(Spacer(1, 3*mm))

story += [
    Paragraph('5.2  Recommended VPS Providers', h2_style),
]
vps_data = [
    ['Provider', 'Plan', 'Cost/mo', 'Region', 'Recommendation'],
    ['Vultr',         '1 vCPU, 2GB RAM, 55GB SSD', '$6',  'Singapore',   '⭐ BEST — closest to exchanges'],
    ['DigitalOcean',  '1 vCPU, 2GB RAM, 50GB SSD', '$12', 'Singapore',   'Reliable, good support'],
    ['Contabo',       '4 vCPU, 8GB RAM, 200GB SSD', '$7', 'Singapore',   'Best specs per dollar'],
    ['AWS Lightsail', '1 vCPU, 2GB RAM, 60GB SSD', '$5',  'ap-southeast-1', 'Good if familiar with AWS'],
    ['Linode/Akamai', '1 vCPU, 2GB RAM, 50GB SSD', '$12', 'Singapore',   'Solid reliability'],
]
vt = Table(vps_data, colWidths=[28*mm, 50*mm, 18*mm, 38*mm, 46*mm])
vt.setStyle(TableStyle([
    ('BACKGROUND',    (0,0), (-1,0), HexColor('#1c2128')),
    ('TEXTCOLOR',     (0,0), (-1,0), ACCENT),
    ('FONTNAME',      (0,0), (-1,0), 'Helvetica-Bold'),
    ('FONTSIZE',      (0,0), (-1,-1), 8),
    ('ROWBACKGROUNDS',(0,1), (-1,-1), [CARD_BG, HexColor('#1c2128')]),
    ('TEXTCOLOR',     (0,1), (-1,-1), WHITE),
    ('TEXTCOLOR',     (4,1), (4,1), ACCENT),
    ('BOX',           (0,0), (-1,-1), 0.5, BORDER),
    ('INNERGRID',     (0,0), (-1,-1), 0.3, BORDER),
    ('TOPPADDING',    (0,0), (-1,-1), 5),
    ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ('LEFTPADDING',   (0,0), (-1,-1), 6),
]))
story.append(vt)

story += [
    Spacer(1, 4*mm),
    Paragraph('5.3  Create & Connect to VPS (Vultr Example)', h2_style),
    kv_table([
        ('Step 1', 'Go to vultr.com → Sign Up → add credit card'),
        ('Step 2', 'Deploy New Server → Cloud Compute → <b>Singapore</b> region'),
        ('Step 3', 'OS: <b>Ubuntu 22.04 LTS x64</b> (always use LTS — Long Term Support)'),
        ('Step 4', 'Plan: <b>Regular Cloud Compute — 1 vCPU, 2GB RAM</b> ($6/mo) — sufficient for the bot'),
        ('Step 5', 'Server hostname: "deltabot" → Deploy Now → wait 2–3 minutes'),
        ('Step 6', 'Go to Server Details → copy the <b>IP Address</b> (e.g., 45.77.123.45)'),
        ('Step 7', 'Note this IP — you need it for API key IP whitelisting in Phase 4'),
    ], col1=18*mm),
    Spacer(1, 4*mm),
    Paragraph('5.4  Connect to VPS via SSH', h2_style),
    Paragraph('From <b>Windows</b> (use Windows Terminal or PowerShell):', body),
    Paragraph('ssh root@YOUR_VPS_IP', code),
    Paragraph('Enter the password from Vultr dashboard when prompted.', body_s),
    Spacer(1, 3*mm),
    Paragraph('Run initial server setup:', body),
    Paragraph('apt update && apt upgrade -y', code),
    Paragraph('apt install python3 python3-pip python3-venv git screen htop nano -y', code),
    PageBreak(),
]

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 6 — INSTALL BOT ON VPS
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header(6, 'INSTALL & CONFIGURE THE BOT ON VPS')
story += [
    Paragraph('6.1  Upload Bot Files to VPS', h2_style),
    Paragraph('From your <b>Windows PC</b>, open a new terminal (not the SSH one):', body),
    Paragraph('# Upload entire bot folder to VPS (run from your PC)', code),
    Paragraph('scp -r "C:\\Users\\VISAL\\Desktop\\delta_neutral_bot_v2\\delta_neutral_bot" root@YOUR_VPS_IP:/root/', code),
    Spacer(1, 3*mm),
    Paragraph('Or use <b>WinSCP</b> (free GUI tool) for drag-and-drop file transfer:', body),
    bullet('Download WinSCP from winscp.net'),
    bullet('Connect: Protocol=SCP, Host=YOUR_VPS_IP, User=root, Password=VPS_PASSWORD'),
    bullet('Drag the delta_neutral_bot folder to /root/ on the VPS'),
    Spacer(1, 4*mm),
    Paragraph('6.2  Install Python Dependencies', h2_style),
    Paragraph('Back in your SSH terminal (connected to VPS):', body),
    Paragraph('cd /root/delta_neutral_bot', code),
    Paragraph('pip3 install -r requirements.txt', code),
    Paragraph('# This installs: ccxt, apscheduler, pyyaml, and all other dependencies', code),
    Spacer(1, 3*mm),
    Paragraph('If requirements.txt is missing, install manually:', body),
    Paragraph('pip3 install ccxt apscheduler pyyaml python-dotenv reportlab flask sqlite3', code),
    Spacer(1, 4*mm),
    Paragraph('6.3  Configure the .env File on VPS', h2_style),
    Paragraph('Create the .env file with your <b>real</b> API keys (not paper mode keys):', body),
    Paragraph('nano /root/delta_neutral_bot/.env', code),
    Paragraph('Paste the following (replace with your actual keys):', body),
]

env_content = """BINANCE_API_KEY=your_real_binance_api_key_here
BINANCE_API_SECRET=your_real_binance_api_secret_here

BYBIT_API_KEY=your_real_bybit_api_key_here
BYBIT_API_SECRET=your_real_bybit_api_secret_here

TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id

# Leave these blank for now (optional alerts)
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
ALERT_EMAIL="""

story.append(Paragraph(env_content, code))

story += [
    Paragraph('Save: Ctrl+X → Y → Enter', body_s),
    Spacer(1, 4*mm),
    Paragraph('6.4  Test Exchange Connectivity', h2_style),
    Paragraph('Before going live, verify the bot can connect to both exchanges:', body),
    Paragraph('cd /root/delta_neutral_bot', code),
    Paragraph('python3 bot.py --status', code),
    Paragraph('Expected output: Both exchanges connected, balance shown, 0 open positions.', tip),
    Spacer(1, 3*mm),
    warning_box('If you see "Invalid API key" errors: double-check the key was copied correctly, IP whitelist matches your VPS IP, and futures permission is enabled on the key.'),
    Spacer(1, 4*mm),
    Paragraph('6.5  Keep Bot Running After SSH Disconnect (screen)', h2_style),
    Paragraph('The <b>screen</b> tool keeps processes running even when you close SSH:', body),
    Paragraph('screen -S deltabot          # create a named session', code),
    Paragraph('python3 bot.py             # start the bot (LIVE mode)', code),
    Paragraph('# Press Ctrl+A then D to detach (bot keeps running)', code),
    Paragraph('screen -r deltabot          # reattach later to check logs', code),
    Spacer(1, 2*mm),
    Paragraph('To make the bot auto-restart on VPS reboot, add a cron job:', body),
    Paragraph('crontab -e', code),
    Paragraph('@reboot cd /root/delta_neutral_bot && screen -dmS deltabot python3 bot.py', code),
    PageBreak(),
]

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 7 — PRE-LAUNCH CHECKLIST
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header(7, 'PRE-LAUNCH SAFETY CHECKLIST')
story += [
    Paragraph('Go through every item below before switching to live. Do not skip any.', body),
    Spacer(1, 3*mm),
    Paragraph('7.1  Exchange Setup', h2_style),
]
exchange_checks = [
    (True,  'Binance account KYC approved'),
    (True,  'Bybit account KYC approved'),
    (True,  '$2,400 USDT in Binance USDT-M Futures wallet'),
    (True,  '$1,600 USDT in Bybit USDT Perpetual (Derivatives) wallet'),
    (True,  'Binance futures margin mode set to CROSS'),
    (True,  'Bybit derivatives margin mode set to CROSS'),
    (True,  'Google Authenticator 2FA enabled on both exchanges'),
    (False, 'NO withdrawal permission on any API key'),
    (True,  'VPS IP whitelisted on both API keys'),
]
for done, text in exchange_checks:
    story.append(check(text, done=True))  # all shown as todo (open circles = user must check)
story.append(Spacer(1, 4*mm))

story += [Paragraph('7.2  Bot Configuration', h2_style)]
bot_checks = [
    'SAGA disabled (active: false) — gross P&L negative',
    'FIL disabled (active: false) — below 0.18% floor',
    'CRV disabled (active: false) — 29% WR, fees exceed gross',
    'SNX disabled (active: false) — 30% WR, spread expands not compresses',
    'STG disabled (active: false) — 12% WR, gross negative',
    '1INCH disabled (active: false) — below 0.18% floor',
    'min_spread_entry: 0.0018 confirmed in config.yaml',
    'adaptive_spread floor: 0.0018 confirmed in config.yaml',
    'capital total_usdt: 4000 in config.yaml',
    'stablecoin_reserve: 600 in config.yaml',
    'max_daily_loss: 200 in config.yaml (5% of capital)',
    'Telegram bot token and chat_id in .env — test by running paper for 1 hour',
    'python3 bot.py --status returns both exchanges connected with correct balances',
]
for text in bot_checks:
    story.append(check(text, done=True))
story.append(Spacer(1, 4*mm))

story += [
    Paragraph('7.3  Mental Preparation', h2_style),
    bullet('Understand the first day in live may show lower profits than paper — slippage is real'),
    bullet('Do not panic if the first 2 hours show small losses — legacy positions from restart will clean up'),
    bullet('The daily loss limit is $200 — if hit, bot stops automatically for the day'),
    bullet('You should expect roughly 75–85% of paper profits in live mode'),
    Spacer(1, 3*mm),
    warning_box('Paper mode must have run for at least 3 full days before going live. You have completed 3+ days. Do NOT go live on day 1 of paper.'),
    PageBreak(),
]

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 8 — GO LIVE
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header(8, 'GO LIVE — SWITCH FROM PAPER TO REAL')
story += [
    Paragraph('8.1  Stop the Paper Bot', h2_style),
    Paragraph('On your current machine (Windows PC), stop the paper bot:', body),
    Paragraph('Press Ctrl+C in the terminal running python bot.py --paper', code),
    Paragraph('Verify all paper positions are closed before switching. Check with:', code),
    Paragraph('python3 snapshot.py      # should show "Open positions: 0"', code),
    Spacer(1, 4*mm),
    Paragraph('8.2  Start Live Bot on VPS', h2_style),
    Paragraph('SSH into your VPS and start the bot WITHOUT the --paper flag:', body),
    Paragraph('ssh root@YOUR_VPS_IP', code),
    Paragraph('screen -S deltabot', code),
    Paragraph('cd /root/delta_neutral_bot', code),
    Paragraph('python3 bot.py', code),
    Paragraph('The terminal will show:', body),
    Paragraph('WARNING: LIVE TRADING MODE — REAL FUNDS AT RISK', code),
    Paragraph('Waiting 3 seconds... (allows Ctrl+C cancellation)', code),
    Paragraph('Initialising bot components...', code),
    Paragraph('Bot engine running — press Ctrl+C to stop', code),
    Spacer(1, 3*mm),
    Paragraph('After confirming it started correctly:', body),
    Paragraph('# Ctrl+A then D to detach — bot runs in background', code),
    Spacer(1, 4*mm),
    Paragraph('8.3  Verify First Trade in Live', h2_style),
    kv_table([
        ('1 min', 'Check Binance Futures → Positions — should start showing entries'),
        ('5 min', 'Check Bybit Derivatives → Positions — should have matching short positions'),
        ('10 min', 'Run: python3 snapshot.py — should show growing open_positions count'),
        ('1 hour', 'Telegram message should arrive with first hourly progress report'),
    ], col1=15*mm),
    Spacer(1, 4*mm),
    success_box('If you see positions on both Binance AND Bybit simultaneously, the bot is working correctly in live mode.'),
    Spacer(1, 3*mm),
    warning_box('If you see positions on Binance but NOT Bybit (or vice versa), the hedge is broken — run python3 bot.py --kill IMMEDIATELY to close all positions.'),
    PageBreak(),
]

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 9 — FIRST 24 HOURS
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header(9, 'FIRST 24 HOURS — WHAT TO WATCH')
story += [
    Paragraph('The first 24 hours in live mode are critical for verifying the system works as expected.', body),
    Spacer(1, 3*mm),
]

timeline = [
    ['Time',         'What to Check',                                                       'Action if Wrong'],
    ['0–10 min',     'Positions appearing on both exchanges simultaneously',                'Kill switch if only one side'],
    ['30 min',       'No API errors in logs (screen -r deltabot)',                          'Check API key permissions'],
    ['1 hour',       'Telegram report received with positive or small negative P&L',        'Check Telegram bot config'],
    ['2–4 hours',    'python3 snapshot.py shows trades closing with net profit',            'Compare to paper — should be similar'],
    ['8 hours',      'Check margin usage on Binance + Bybit (target: <50% usage)',          'Reduce max_simultaneous_positions if >70%'],
    ['12 hours',     'Sub-floor entries should be 0 (no legacy from paper mode)',           'Verify min_spread_entry: 0.0018 in config'],
    ['24 hours',     'Calculate live capture rate: live_net / paper_net × 100',            'Target 75–85%. Below 60% = investigate'],
]
tt = Table(timeline, colWidths=[22*mm, 85*mm, 53*mm])
tt.setStyle(TableStyle([
    ('BACKGROUND',    (0,0), (-1,0), HexColor('#1c2128')),
    ('TEXTCOLOR',     (0,0), (-1,0), ACCENT),
    ('FONTNAME',      (0,0), (-1,0), 'Helvetica-Bold'),
    ('FONTSIZE',      (0,0), (-1,-1), 8),
    ('ROWBACKGROUNDS',(0,1), (-1,-1), [CARD_BG, HexColor('#1c2128')]),
    ('TEXTCOLOR',     (0,1), (-1,-1), WHITE),
    ('BOX',           (0,0), (-1,-1), 0.5, BORDER),
    ('INNERGRID',     (0,0), (-1,-1), 0.3, BORDER),
    ('TOPPADDING',    (0,0), (-1,-1), 5),
    ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ('LEFTPADDING',   (0,0), (-1,-1), 6),
    ('VALIGN',        (0,0), (-1,-1), 'TOP'),
]))
story.append(tt)

story += [
    Spacer(1, 4*mm),
    Paragraph('9.1  Live vs Paper Comparison', h2_style),
    Paragraph('After 24 hours, run this comparison to measure capture rate:', body),
    Paragraph('python3 snapshot.py', code),
    Paragraph('Compare the live net profit shown to what paper was making at the same time yesterday.', body),
    kv_table([
        ('Capture rate > 85%', 'Excellent — slippage is minimal, execution is clean'),
        ('Capture rate 75–85%', 'Good — expected range, no action needed'),
        ('Capture rate 60–75%', 'Acceptable — monitor for another day, may improve as market opens'),
        ('Capture rate < 60%', 'Investigate — check if orders are filling as limit or market, check for API rate limits'),
    ], col1=45*mm),
    PageBreak(),
]

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 10 — ONGOING MAINTENANCE
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header(10, 'ONGOING MAINTENANCE & MONITORING')
story += [
    Paragraph('10.1  Daily Routine (5 minutes)', h2_style),
]
for task in [
    'Morning: Check Telegram — did you receive overnight hourly reports? (No reports = bot crashed)',
    'Run python3 snapshot.py — review today\'s net P&L, WR, open positions',
    'Check both exchange UIs briefly — positions should be balanced (same # on Binance and Bybit)',
    'Review any alert messages — margin_critical or daily_loss alerts need immediate attention',
]:
    story.append(bullet(task))

story += [
    Spacer(1, 3*mm),
    Paragraph('10.2  Weekly Routine (15 minutes)', h2_style),
]
for task in [
    'Friday 18:00 AWST: automatic weekly withdrawal fires to your cold wallet — verify it completed',
    'Run python3 progress.py — review per-pair performance for the week',
    'Any pair with WR < 35% for 3+ consecutive days: consider disabling',
    'Any pair with WR > 80% consistently: consider increasing notional cap (edit max_notional_per_trade)',
    'Check VPS disk space: df -h — ensure < 80% full (logs can grow)',
    'Check VPS memory: free -h — ensure < 70% used',
]:
    story.append(bullet(task))

story += [
    Spacer(1, 3*mm),
    Paragraph('10.3  Monthly Routine (30 minutes)', h2_style),
]
for task in [
    'Last day of month: auto-compound fires at 22:00 AWST — 40% of profits reinvested into capital',
    'Update total_usdt in config.yaml to reflect new compounded capital',
    'Review all-time per-pair stats in progress.py — disable any long-term bleeders',
    'Check if new pairs should be added (run python3 scan_pairs.py)',
    'Rotate API keys every 3 months for security',
]:
    story.append(bullet(task))

story += [
    Spacer(1, 3*mm),
    Paragraph('10.4  Automated Jobs (Bot Handles These)', h2_style),
    kv_table([
        ('Every 10 sec',    'Strategy tick — scan all pairs for entry/exit signals'),
        ('Every 60 sec',    'Risk tick — check margin usage, daily loss limit, HWM'),
        ('Every 1 hour',    'Telegram progress report (P&L, WR, best/worst pair, open positions)'),
        ('Daily 23:55 AWST','Daily report saved to database'),
        ('Friday 18:00',    'Weekly profit withdrawal to cold wallet'),
        ('Last day/mo 22:00','Monthly tasks — fee reconciliation, stats reset'),
        ('Last day/mo 22:05','Auto-compound — reinvest 40% of monthly profit into capital'),
        ('07:30, 15:30, 23:30 UTC', 'Funding window boost — scan rate increases 30 min before settlement'),
    ], col1=45*mm),
    PageBreak(),
]

# ═══════════════════════════════════════════════════════════════════════════════
# APPENDIX A — EMERGENCY PROCEDURES
# ═══════════════════════════════════════════════════════════════════════════════
story += [
    Paragraph('APPENDIX A — EMERGENCY PROCEDURES & KILL SWITCH', h1_style),
    HR(),
    Spacer(1, 3*mm),
    Paragraph('A.1  Emergency Kill Switch', h2_style),
    Paragraph('If something goes seriously wrong (exchange outage, runaway losses, hedge broken), use the kill switch:', body),
    Paragraph('python3 bot.py --kill', code),
    Paragraph('This closes all open positions on both exchanges immediately and halts the bot. Use this if:', body),
]
for s in [
    'Daily loss exceeds $300+ (well beyond the $200 automatic limit)',
    'You see positions on one exchange but not the other (broken hedge)',
    'Exchange API is behaving erratically',
    'You need to do maintenance or update config',
]:
    story.append(bullet(s, symbol='⚡'))

story += [
    Spacer(1, 4*mm),
    Paragraph('A.2  Manual Position Close (Exchange UI)', h2_style),
    Paragraph('If the kill switch fails (API down), close manually:', body),
    kv_table([
        ('Binance', 'Futures → Positions tab → Close All (or close each pair individually)'),
        ('Bybit',   'Derivatives → Positions → Close All Positions'),
    ], col1=20*mm),
    Spacer(1, 4*mm),
    Paragraph('A.3  Alert Types You Will Receive', h2_style),
]
alerts = [
    ('margin_critical',     'Margin usage ≥ 75% — trading paused. Check if positions are stuck open.'),
    ('margin_alert',        'Margin usage ≥ 70% — warning. Normal if many positions open at once.'),
    ('daily_loss_limit',    'Bot hit max daily loss ($200). Stops trading until tomorrow 00:00.'),
    ('high_water_mark',     'Account dropped 15% below peak balance. Risk mode activated.'),
    ('position_loss_alert', 'A single position lost > 5% of notional. Investigate that pair.'),
    ('funding_rate_alert',  'Funding rate spiking adversely. Bot may pause entries on that pair.'),
]
for alert, desc in alerts:
    story.append(kv_table([(alert, desc)], col1=45*mm))
    story.append(Spacer(1, 1*mm))
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# APPENDIX B — CONFIG REFERENCE
# ═══════════════════════════════════════════════════════════════════════════════
story += [
    Paragraph('APPENDIX B — CONFIG REFERENCE & ACTIVE PAIRS', h1_style),
    HR(),
    Spacer(1, 3*mm),
    Paragraph('B.1  Key Configuration Values', h2_style),
]
config_ref = [
    ('total_usdt: 4000',              'Your starting capital. Update after compounding.'),
    ('binance_allocation: 0.60',      '$2,400 on Binance (60% of capital)'),
    ('bybit_allocation: 0.40',        '$1,600 on Bybit (40% of capital)'),
    ('stablecoin_reserve: 600',       '$600 kept as emergency reserve — never traded'),
    ('free_margin_buffer: 0.15',      '15% of tradeable capital always kept free'),
    ('min_spread_entry: 0.0018',      '0.18% minimum spread to enter — critical floor'),
    ('max_notional_per_trade: 2500',  '$2,500 max position size per trade per leg'),
    ('max_simultaneous_positions: 20','Maximum positions open at once'),
    ('max_hold_hours: 12',            'Force-close any position older than 12 hours'),
    ('max_daily_loss: 200',           '$200 max daily loss before bot pauses'),
    ('position_check_interval: 10',   'Scan all pairs every 10 seconds'),
    ('leverage default: 7',           '7x for spreads below 0.20%'),
    ('leverage medium: 8',            '8x for spreads 0.20%–0.30%'),
    ('leverage maximum: 10',          '10x for spreads ≥ 0.30%'),
]
for k, v in config_ref:
    story.append(kv_table([(k, v)], col1=65*mm))
    story.append(Spacer(1, 0.5*mm))

story += [
    Spacer(1, 4*mm),
    Paragraph('B.2  Active Pairs (as of April 2026)', h2_style),
]
active_pairs = [
    ['Pair', 'All-Time Net', 'WR', 'Avg Spread', 'Status'],
    ['PORTAL', '+$375', '63%', '0.262%', '✅ Active — Star performer'],
    ['DYDX',   '+$342', '77%', '0.198%', '✅ Active — Consistent'],
    ['GALA',   '+$242', '49%', '0.266%', '✅ Active — High volume'],
    ['ETH',    '+$236', '87%', '0.160%', '✅ Active — High WR'],
    ['ORDI',   '+$215', '73%', '0.212%', '✅ Active'],
    ['RUNE',   '+$167', '75%', '0.219%', '✅ Active'],
    ['COMP',   '+$161', '67%', '0.201%', '✅ Active'],
    ['ARB',    '+$158', '62%', '0.174%', '✅ Active'],
    ['KSM',    '+$98',  '85%', '0.201%', '✅ Active — 95% WR today'],
    ['AVAX',   '+$87',  '78%', '0.184%', '✅ Active'],
    ['DOT',    '+$73',  '59%', '0.150%', '✅ Active'],
    ['OP',     '+$71',  '62%', '0.177%', '✅ Active'],
    ['WIF',    '+$69',  '66%', '0.168%', '✅ Active'],
    ['NEAR',   '+$59',  '62%', '0.149%', '✅ Active'],
    ['SAGA',   '-$226', '16%', '0.286%', '❌ DISABLED — gross negative'],
    ['FIL',    '-$118', '43%', '0.144%', '❌ DISABLED — below floor'],
    ['CRV',    '-$36',  '29%', '0.342%', '❌ DISABLED — fees exceed gross'],
    ['SNX',    '-$18',  '30%', '0.307%', '❌ DISABLED — spread expands'],
    ['STG',    '-$16',  '12%', '0.217%', '❌ DISABLED — gross negative'],
    ['1INCH',  '-$12',  '48%', '0.142%', '❌ DISABLED — below floor'],
]
pt = Table(active_pairs, colWidths=[20*mm, 22*mm, 12*mm, 22*mm, 84*mm])
pt.setStyle(TableStyle([
    ('BACKGROUND',    (0,0), (-1,0), HexColor('#1c2128')),
    ('TEXTCOLOR',     (0,0), (-1,0), ACCENT),
    ('FONTNAME',      (0,0), (-1,0), 'Helvetica-Bold'),
    ('FONTSIZE',      (0,0), (-1,-1), 7.5),
    ('ROWBACKGROUNDS',(0,1), (-1,-1), [CARD_BG, HexColor('#1c2128')]),
    ('TEXTCOLOR',     (0,1), (-1,-1), WHITE),
    ('BOX',           (0,0), (-1,-1), 0.5, BORDER),
    ('INNERGRID',     (0,0), (-1,-1), 0.3, BORDER),
    ('TOPPADDING',    (0,0), (-1,-1), 4),
    ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ('LEFTPADDING',   (0,0), (-1,-1), 5),
]))
story.append(pt)
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# APPENDIX C — WHAT CAN GO WRONG
# ═══════════════════════════════════════════════════════════════════════════════
story += [
    Paragraph('APPENDIX C — WHAT CAN GO WRONG & HOW TO FIX IT', h1_style),
    HR(),
    Spacer(1, 3*mm),
]
problems = [
    ('Bot crashes / no Telegram reports',
     'SSH into VPS → screen -r deltabot → check error message\nRestart: screen -S deltabot && python3 bot.py'),
    ('API authentication error',
     'Check .env file has correct keys → check exchange API management for IP whitelist → ensure futures permission enabled'),
    ('Positions on Binance but not Bybit',
     'RUN KILL SWITCH IMMEDIATELY: python3 bot.py --kill\nManually close Binance positions via exchange UI\nInvestigate Bybit API connection before restarting'),
    ('Daily loss limit hit ($200)',
     'Normal — bot stopped automatically and will resume tomorrow\nReview which pairs caused losses via python3 snapshot.py'),
    ('Margin critical alert (>75%)',
     'Too many positions open. Reduce max_simultaneous_positions from 20 to 12 in config. Restart bot.'),
    ('Low capture rate (<60%)',
     'Check if orders fill as limit or taker. Increase trailing_exit_buffer from 0.0002 to 0.0003 in config.'),
    ('VPS went offline / rebooted',
     'SSH back in → screen -r deltabot (if screen session survived) OR start new: python3 bot.py\nCheck if cron @reboot job fired automatically'),
    ('Exchange maintenance window',
     'Bot will log API errors and retry. Positions entered before maintenance will remain open until exchange is back.\nNo action needed — bot handles reconnection automatically.'),
    ('Funding rate goes very negative',
     'Bot has funding_harvest_min_rate check. If funding rate is too adverse it will not enter new positions on that pair. Normal behaviour.'),
]
for prob, fix in problems:
    story.append(Paragraph(prob, h3_style))
    story.append(Paragraph(fix.replace('\n', '<br/>'), body_s))
    story.append(Spacer(1, 3*mm))

# Final note
story += [
    HR(),
    Spacer(1, 3*mm),
    Paragraph('Important Reminders', h2_style),
    warning_box('Never leave withdrawal permission on API keys. Never share .env with anyone. Never commit .env to git.'),
    Spacer(1, 3*mm),
    warning_box('Never trade manually on an account the bot is managing. Manual trades will confuse the bot\'s position tracking.'),
    Spacer(1, 3*mm),
    success_box('The bot runs best with consistent VPS uptime, clean API keys, and a disciplined pair selection. Disable pairs that bleed. Let winners run.'),
    Spacer(1, 4*mm),
    Paragraph('Generated automatically from live bot data. All P&L figures are paper trading results.', cover_meta),
    Paragraph(f'Document generated: {datetime.now().strftime("%B %d, %Y at %H:%M AWST")}', cover_meta),
]

# ── Build PDF ──────────────────────────────────────────────────────────────────
doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
print(f'PDF saved: {out_path.resolve()}')
print(f'Pages: approx 17-18')
