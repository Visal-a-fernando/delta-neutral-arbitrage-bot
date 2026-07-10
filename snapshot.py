"""snapshot.py — Quick live performance snapshot from SQLite."""
import sys, sqlite3, re
from pathlib import Path
from datetime import date, datetime

sys.stdout.reconfigure(encoding='utf-8')

db = Path('data/bot.db')
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row

today = date.today().isoformat()
print(f'=== LIVE SNAPSHOT  {datetime.now().strftime("%H:%M:%S AWST")} ===')
print(f'Date filter     : {today}')

# ── Today's closed trades ─────────────────────────────────────────────────────
rows = con.execute(
    "SELECT net_profit, gross_profit, fees, spread_pct, leg_a_symbol, timestamp, notes "
    "FROM trades WHERE DATE(timestamp,'localtime')=? AND status='closed'",
    (today,)
).fetchall()

nets    = [r['net_profit']  or 0 for r in rows]
gross   = [r['gross_profit'] or 0 for r in rows]
fees    = [r['fees']        or 0 for r in rows]
spreads = [r['spread_pct']  or 0 for r in rows]
wins    = sum(1 for n in nets if n > 0)
n       = len(nets)

print(f'\nToday ({today}):')
print(f'  Closed trades  : {n}')
print(f'  Net P&L        : ${sum(nets):.2f}')
print(f'  Gross P&L      : ${sum(gross):.2f}')
print(f'  Total fees     : ${sum(fees):.2f}')
if n:
    print(f'  Win rate       : {wins/n*100:.1f}%')
    print(f'  Avg net/trade  : ${sum(nets)/n:.4f}')
    print(f'  Avg spread     : {sum(spreads)/n*100:.4f}%')
else:
    print('  Win rate       : N/A')

# Leverage tiers via spread (0.18-0.20%=7x, 0.20-0.30%=8x, >=0.30%=10x)
tiers = {7: [], 8: [], 10: []}
for r in rows:
    sp = r['spread_pct'] or 0
    if sp < 0.0020:
        tiers[7].append(r['net_profit'] or 0)
    elif sp < 0.0030:
        tiers[8].append(r['net_profit'] or 0)
    else:
        tiers[10].append(r['net_profit'] or 0)

for lev, lnets in tiers.items():
    if lnets:
        print(f'  {lev}x tier (~spread) : {len(lnets):3d}t  net=${sum(lnets):7.2f}  avg=${sum(lnets)/len(lnets):.4f}')

# Sub-floor check
sub_floor = [r for r in rows if (r['spread_pct'] or 0) < 0.0018]
print(f'  Sub-0.18% entries: {len(sub_floor)}  (should be 0 after restart)')

# ── Open positions ────────────────────────────────────────────────────────────
opens = con.execute("SELECT pair, leg_a_symbol FROM trades WHERE status='open'").fetchall()
print(f'\nOpen positions : {len(opens)}')
for o in opens[:15]:
    print(f'  {o["leg_a_symbol"]}')
if len(opens) > 15:
    print(f'  ... and {len(opens)-15} more')

# ── Per-pair breakdown today ──────────────────────────────────────────────────
print(f'\nPer-pair today (sorted by net):')
pairs: dict = {}
for r in rows:
    sym = r['leg_a_symbol'].replace('/USDT:USDT', '').replace('USDT', '')
    if sym not in pairs:
        pairs[sym] = {'nets': [], 'wins': 0}
    pairs[sym]['nets'].append(r['net_profit'] or 0)
    if (r['net_profit'] or 0) > 0:
        pairs[sym]['wins'] += 1

print(f'  {"Pair":<12} {"Trades":>6}  {"Net":>8}  {"Avg":>7}  {"WR":>5}')
print(f'  {"-"*12} {"-"*6}  {"-"*8}  {"-"*7}  {"-"*5}')
for sym, d in sorted(pairs.items(), key=lambda x: -sum(x[1]['nets'])):
    t   = len(d['nets'])
    net = sum(d['nets'])
    wr  = d['wins'] / t * 100
    avg = net / t
    print(f'  {sym:<12} {t:6d}  ${net:7.2f}  ${avg:6.4f}  {wr:4.0f}%')

# ── Monthly so far ────────────────────────────────────────────────────────────
month_start = date.today().replace(day=1).isoformat()
m_rows = con.execute(
    "SELECT net_profit FROM trades WHERE DATE(timestamp,'localtime')>=? AND status='closed'",
    (month_start,)
).fetchall()
m_net = sum(r['net_profit'] or 0 for r in m_rows)
days  = max(date.today().day, 1)
proj  = (m_net / days) * 30

print(f'\nMonth so far   : ${m_net:.2f}  ({len(m_rows)} trades)')
print(f'30-day project : ${proj:.0f}/mo  (at current daily rate)')

# ── All-time ──────────────────────────────────────────────────────────────────
all_rows = con.execute("SELECT net_profit FROM trades WHERE status='closed'").fetchall()
all_net  = sum(r['net_profit'] or 0 for r in all_rows)
print(f'All-time net   : ${all_net:.2f}  ({len(all_rows)} trades)')

con.close()
