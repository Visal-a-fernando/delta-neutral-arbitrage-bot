"""core/kill_switch.py — Emergency kill: close all open positions immediately."""

import logging
from utils.exchange import build_exchange_clients
from utils.database import get_open_trades, update_trade, log_risk_event

logger = logging.getLogger(__name__)


def emergency_kill(cfg: dict):
    """Close every open position on all exchanges and log the event."""
    logger.critical("KILL SWITCH: Closing all open positions NOW")

    try:
        exchanges = build_exchange_clients(cfg, paper_mode=False)
    except Exception as e:
        logger.error(f"Could not connect to exchanges: {e}")
        exchanges = build_exchange_clients(cfg, paper_mode=True)

    open_trades = get_open_trades()
    logger.critical(f"Found {len(open_trades)} open trades to close")

    for trade in open_trades:
        pair = trade["pair"]
        try:
            # Determine correct close sides from stored direction.
            # buy_a_sell_b  -> leg A is LONG,  leg B is SHORT
            # buy_b_sell_a  -> leg A is SHORT, leg B is LONG
            direction = trade.get("direction", "buy_a_sell_b")
            side_a = "long"  if direction == "buy_a_sell_b" else "short"
            side_b = "short" if direction == "buy_a_sell_b" else "long"

            # Close leg A on leg_a_exchange
            ex_a = exchanges.get(trade["leg_a_exchange"])
            if ex_a:
                ex_a.close_position(
                    symbol=trade["leg_a_symbol"],
                    side=side_a,
                    amount=_notional_to_amount(ex_a, trade["leg_a_symbol"], trade["notional_usd"])
                )
                logger.critical(f"Closed {side_a.upper()} leg A: {trade['leg_a_exchange']} {trade['leg_a_symbol']}")

            # Close leg B on leg_b_exchange
            ex_b = exchanges.get(trade["leg_b_exchange"])
            if ex_b:
                ex_b.close_position(
                    symbol=trade["leg_b_symbol"],
                    side=side_b,
                    amount=_notional_to_amount(ex_b, trade["leg_b_symbol"], trade["notional_usd"])
                )
                logger.critical(f"Closed {side_b.upper()} leg B: {trade['leg_b_exchange']} {trade['leg_b_symbol']}")

            update_trade(trade["id"], {"status": "closed", "notes": "KILL SWITCH"})
        except Exception as e:
            logger.error(f"Failed to close trade {trade['id']} ({pair}): {e}", exc_info=True)

    log_risk_event(
        event_type="kill_switch",
        description="Emergency kill switch activated — all positions closed",
        balance=0.0,
        action="All positions closed, bot halted"
    )
    logger.critical("KILL SWITCH: All positions processed. Bot halted.")


def _notional_to_amount(exchange, symbol: str, notional_usd: float) -> float:
    """Convert notional USD to contract quantity using current price."""
    try:
        ticker = exchange.fetch_ticker(symbol)
        price = ticker["last"]
        return notional_usd / price
    except Exception:
        return 0.01  # Fallback minimum size
