# Delta-Neutral Trading Bot

A complete Python implementation of the delta-neutral spread-capture strategy documented in the system guide. Trades perpetual futures across Binance and Bybit simultaneously to capture inter-exchange price spreads while remaining market-neutral.

---

## ⚠️ Risk Warning

This bot trades leveraged cryptocurrency derivatives. You can lose your entire deposit. Run in **paper mode for at least 7 days** before enabling live trading. This is not financial advice. Consult a licensed financial adviser before deploying real capital.

---

## Architecture

```
delta_neutral_bot/
├── bot.py                      # Entry point
├── config/
│   ├── config.yaml             # All parameters (no secrets)
│   ├── settings.py             # Config loader
│   └── logging_setup.py        # Rotating log files
├── core/
│   ├── bot_engine.py           # Scheduler + orchestration
│   ├── kill_switch.py          # Emergency position close
│   └── status.py               # CLI status printer
├── strategies/
│   └── spread_capture.py       # Core trading engine
├── risk/
│   └── risk_manager.py         # All 6 defence layers
├── reporting/
│   ├── reporter.py             # Daily P&L reports
│   └── profit_extractor.py     # Weekly/monthly withdrawals
├── alerts/
│   └── alert_manager.py        # SMS (Twilio) + Email (SMTP)
├── dashboard/
│   ├── app.py                  # Flask API
│   └── templates/index.html    # Chart.js dashboard
├── utils/
│   ├── database.py             # SQLite schema + helpers
│   └── exchange.py             # CCXT wrappers (Binance + Bybit)
├── tests/
│   └── test_paper_mode.py      # Unit tests
├── requirements.txt
└── .env.example
```

---

## Quick Start

### 1. Install dependencies

```bash
python3.11 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
# Edit .env with your API keys and alert settings
```

### 3. Set API key permissions

On **both Binance and Bybit**:
- Create a new API key
- Enable: **Futures trading** only
- Disable: Spot trading, Withdrawals, Read wallet
- Set an IP whitelist to your server's IP
- Pre-configure a withdrawal address whitelist on the exchange (for weekly profits)

### 4. Run unit tests

```bash
python -m pytest tests/ -v
```

### 5. Paper trade for 7 days (mandatory)

```bash
python bot.py --paper
```

Check the dashboard at `http://localhost:5000` to review performance.

### 6. Go live

Only after 7+ days of paper trading with satisfactory results:

```bash
python bot.py
```

---

## CLI Commands

| Command | Description |
|---|---|
| `python bot.py --paper` | Paper trading mode (no real orders) |
| `python bot.py` | Live trading |
| `python bot.py --kill` | **Emergency:** close all positions immediately |
| `python bot.py --report` | Print 30-day performance report |
| `python bot.py --status` | Print open positions and today's P&L |

---

## Configuration (`config/config.yaml`)

All strategy parameters are in `config.yaml`. Key values:

| Parameter | Default | Description |
|---|---|---|
| `strategy.min_spread_entry` | `0.003` (0.3%) | Minimum spread to enter a trade |
| `leverage.default` | `5x` | Default leverage (safer) |
| `leverage.maximum` | `10x` | Max leverage (high-spread only) |
| `risk.account_stop_loss_pct` | `0.08` (8%) | Close all if unrealised loss hits this |
| `risk.high_water_mark_floor` | `0.15` (15%) | Halt bot if balance drops 15% from peak |
| `profit_extraction.weekly_withdrawal_pct` | `0.35` (35%) | Friday auto-withdrawal |

---

## Six Risk Defence Layers

1. **Liquidation prevention** — 5x default leverage, 20% free margin buffer, 8% account stop-loss
2. **Exchange counterparty risk** — Split 60/40 across Binance and Bybit; weekly withdrawals
3. **Correlation detection** — Pauses all trades if 1h rolling correlation drops below 0.85
4. **Dynamic position sizing** — Deploys 100% / 60% / 0% based on spread quality
5. **Profit extraction** — 35% weekly withdrawal locked in cold wallet, never re-risked
6. **Operator accountability** — Hard daily loss cap ($100), ATR spike filter, news blackout

---

## Dashboard

Runs locally at `http://localhost:5000` while the bot is active. Shows:
- 30-day net P&L chart
- Account balance trend
- Daily reports table
- Risk events log
- Withdrawal history

The dashboard is **read-only** and bound to `127.0.0.1` only — never expose it externally.

---

## Tax (Australia)

The ATO treats systematic bot trading income as **assessable income**, not capital gains. The bot automatically calculates and logs a 25% tax reserve from monthly profits. Consult a tax accountant familiar with crypto trading.

---

## Extending the Bot

- **Add a new pair:** Set `active: true` on a pair in `config.yaml`
- **Change leverage:** Edit `leverage.default` in `config.yaml`
- **Adjust withdrawal %:** Edit `profit_extraction.weekly_withdrawal_pct`
- **Add a strategy:** Create a new file in `strategies/` and wire it into `bot_engine.py`

---

## Deployment (VPS)

For 24/7 operation, run on a VPS (e.g. DigitalOcean, Vultr) close to exchange servers:

```bash
# Install as a systemd service
sudo nano /etc/systemd/system/deltabot.service
```

```ini
[Unit]
Description=Delta-Neutral Trading Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/delta_neutral_bot
EnvironmentFile=/home/ubuntu/delta_neutral_bot/.env
ExecStart=/home/ubuntu/delta_neutral_bot/venv/bin/python bot.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable deltabot
sudo systemctl start deltabot
sudo journalctl -u deltabot -f   # follow logs
```

---

## Checklist Before Going Live

- [ ] Paper mode ran for >= 7 days with positive results
- [ ] API keys have **trade-only** permissions (no withdrawal access)
- [ ] Withdrawal whitelist pre-configured on exchanges
- [ ] `.env` file populated with real credentials
- [ ] Alerts tested (send a test SMS/email)
- [ ] Dashboard accessible at localhost:5000
- [ ] Stop-loss rules verified active in config
- [ ] Capital split confirmed: $1,200 Binance / $800 Bybit / $300 reserve
- [ ] Performance log spreadsheet created for manual tracking
- [ ] Tax wallet designated (separate account for ATO obligations)
