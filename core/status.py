"""core/status.py — Print a human-readable bot status to the console."""

from utils.database import get_open_trades, get_daily_net_profit, get_high_water_mark


def print_status(cfg: dict):
    open_trades = get_open_trades()
    daily_pnl   = get_daily_net_profit()
    hwm         = get_high_water_mark()

    print("\n" + "=" * 55)
    print("  DELTA-NEUTRAL BOT — STATUS")
    print("=" * 55)
    print(f"  Open positions   : {len(open_trades)}")
    print(f"  Today's net P&L  : ${daily_pnl:,.2f}")
    print(f"  High-water mark  : ${hwm:,.2f}")
    print("=" * 55)

    if open_trades:
        print("\n  Open Trades:")
        for t in open_trades:
            print(f"    [{t['id']}] {t['pair']} | notional=${t['notional_usd']:,.0f} | {t['timestamp']}")
    print()
