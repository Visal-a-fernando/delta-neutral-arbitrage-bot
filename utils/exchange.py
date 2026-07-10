"""utils/exchange.py — CCXT exchange wrappers for Binance Futures & Bybit."""

import logging
import time
from typing import Optional, TYPE_CHECKING

import ccxt

from config.settings import get_env

if TYPE_CHECKING:
    from utils.ws_feeds import WSPriceCache

logger = logging.getLogger(__name__)

# Retry config
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds


def _retry(fn):
    """Decorator: retry up to MAX_RETRIES on network/exchange errors."""
    def wrapper(*args, **kwargs):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return fn(*args, **kwargs)
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as e:
                logger.warning(f"{fn.__name__} attempt {attempt}/{MAX_RETRIES} failed: {e}")
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(RETRY_DELAY * attempt)
    return wrapper


class ExchangeClient:
    """Thin wrapper around a CCXT exchange instance."""

    def __init__(self, name: str, exchange_cfg: dict, paper_mode: bool = False,
                 paper_balance: float = 4000.0):
        self.name = name
        self.paper_mode = paper_mode
        self._paper_balance = paper_balance   # per-exchange allocation in paper mode
        api_key    = get_env(exchange_cfg["api_key_env"],    required=not paper_mode)
        api_secret = get_env(exchange_cfg["api_secret_env"], required=not paper_mode)

        # OKX perpetual contracts are "swap" type; Binance/Bybit use "future"
        default_type = exchange_cfg.get("default_type", "future")

        # OKX requires a passphrase in addition to key/secret
        passphrase = None
        if exchange_cfg.get("api_passphrase_env"):
            passphrase = get_env(exchange_cfg["api_passphrase_env"], required=not paper_mode)

        init_params = {
            "apiKey":  api_key    or "paper_key",
            "secret":  api_secret or "paper_secret",
            "options": {"defaultType": default_type},
            "enableRateLimit": True,
        }
        if passphrase:
            init_params["password"] = passphrase

        exchange_class = getattr(ccxt, exchange_cfg["name"])
        self._ex = exchange_class(init_params)

        if exchange_cfg.get("testnet") and hasattr(self._ex, "set_sandbox_mode"):
            self._ex.set_sandbox_mode(True)
            logger.info(f"{name}: testnet/sandbox mode enabled")

        self._ws_cache: Optional["WSPriceCache"] = None
        logger.info(f"Exchange client ready: {name} ({'PAPER' if paper_mode else 'LIVE'})")

    def set_ws_cache(self, ws_cache: "WSPriceCache"):
        """Attach a WebSocket price cache. fetch_ticker() will use it first."""
        self._ws_cache = ws_cache

    # ── Market data ──────────────────────────────────────────────────────────

    def fetch_ticker(self, symbol: str) -> dict:
        """Return ticker dict. Uses WS cache first; falls back to REST."""
        if self._ws_cache:
            cached = self._ws_cache.get(self.name, symbol)
            if cached:
                return cached
        return self._fetch_ticker_rest(symbol)

    @_retry
    def _fetch_ticker_rest(self, symbol: str) -> dict:
        return self._ex.fetch_ticker(symbol)

    @_retry
    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 60) -> list:
        return self._ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    @_retry
    def fetch_funding_rate(self, symbol: str) -> dict:
        return self._ex.fetch_funding_rate(symbol)

    @_retry
    def fetch_balance(self) -> dict:
        if self.paper_mode:
            bal = self._paper_balance
            return {
                "USDT":  {"free": bal, "used": 0.0, "total": bal},
                "free":  {"USDT": bal},
                "used":  {"USDT": 0.0},
                "total": {"USDT": bal},
            }
        return self._ex.fetch_balance()

    @_retry
    def fetch_positions(self, symbols: Optional[list] = None) -> list:
        if self.paper_mode:
            return []   # Paper positions tracked in-memory by strategy, not exchange
        return self._ex.fetch_positions(symbols)

    # ── Order management ─────────────────────────────────────────────────────

    def place_order(self, symbol: str, side: str, amount: float,
                    order_type: str = "market", price: float = None) -> dict:
        if self.paper_mode:
            # Simulate order with current market price
            ticker = self.fetch_ticker(symbol)
            fill_price = price if (order_type == "limit" and price) else ticker["last"]
            order = {
                "id": f"paper_{int(time.time()*1000)}",
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "amount": amount,
                "price": fill_price,
                "filled": amount,
                "status": "closed",
                "paper": True,
            }
            logger.info(f"[PAPER] {self.name} {side.upper()} {amount} {symbol} @ {fill_price:.4f}")
            return order

        logger.info(f"[LIVE] {self.name} {side.upper()} {amount} {symbol}")
        if order_type == "market":
            return self._ex.create_market_order(symbol, side, amount)
        else:
            return self._ex.create_limit_order(symbol, side, amount, price)

    @_retry
    def fetch_order(self, order_id: str, symbol: str) -> dict:
        if self.paper_mode:
            return {"id": order_id, "status": "closed", "filled": 1, "paper": True}
        return self._ex.fetch_order(order_id, symbol)

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        if self.paper_mode:
            return {"id": order_id, "status": "cancelled", "paper": True}
        try:
            return self._ex.cancel_order(order_id, symbol)
        except Exception as e:
            # Order may have already filled — log and continue
            logger.debug(f"Cancel {order_id} on {self.name}: {e}")
            return {"id": order_id, "status": "cancel_attempted"}

    @_retry
    def close_position(self, symbol: str, side: str, amount: float,
                       order_type: str = "limit", price: float = None) -> dict:
        """Close a position by placing the opposite order."""
        close_side = "sell" if side == "long" else "buy"
        return self.place_order(symbol, close_side, amount,
                                order_type=order_type, price=price)

    def set_leverage(self, symbol: str, leverage: int):
        if self.paper_mode:
            logger.info(f"[PAPER] Set leverage {leverage}x on {symbol}")
            return
        try:
            self._ex.set_leverage(leverage, symbol)
            logger.info(f"Leverage set to {leverage}x on {self.name}/{symbol}")
        except Exception as e:
            logger.warning(f"Could not set leverage on {self.name}/{symbol}: {e}")

    # ── Account info ─────────────────────────────────────────────────────────

    def get_usdt_balance(self) -> float:
        bal = self.fetch_balance()
        return float(bal.get("USDT", {}).get("free", 0) or
                     bal.get("free", {}).get("USDT", 0))

    def get_total_equity(self) -> float:
        bal = self.fetch_balance()
        return float(bal.get("total", {}).get("USDT", 0) or
                     bal.get("USDT", {}).get("total", 0) or 0)


def build_exchange_clients(cfg: dict, paper_mode: bool) -> dict[str, ExchangeClient]:
    """
    Build and return {name: ExchangeClient} only for exchanges used by active pairs.
    Exchanges configured but not referenced by any active pair are skipped — this
    prevents spurious API-key errors for Gate/Bitget/OKX when those pairs are inactive.
    """
    # Collect exchange names actually needed by active pairs
    needed = set()
    for pair in cfg.get("pairs", []):
        if pair.get("active"):
            needed.add(pair["leg_a_exchange"])
            needed.add(pair["leg_b_exchange"])

    # Per-exchange paper balances from config allocation percentages.
    # This prevents double-counting when risk manager sums equity across all exchanges.
    total_capital = cfg["capital"]["total_usdt"]
    cap_cfg       = cfg["capital"]
    alloc_map = {
        "binance":      cap_cfg.get("binance_allocation", 0.60),
        "bybit":        cap_cfg.get("bybit_allocation",   0.40),
        "gate":         cap_cfg.get("gate_allocation",    0.40),
        "bitget":       cap_cfg.get("bitget_allocation",  0.40),
        "mexc":         cap_cfg.get("mexc_allocation",    0.40),
        "okx":          cap_cfg.get("okx_allocation",     0.40),
        "binance_spot": cap_cfg.get("binance_allocation", 0.60),
    }

    clients = {}
    for name, ex_cfg in cfg["exchanges"].items():
        if name not in needed:
            logger.info(f"Skipping exchange '{name}' — no active pairs use it")
            continue
        paper_bal = total_capital * alloc_map.get(name, 0.50)
        clients[name] = ExchangeClient(name, ex_cfg, paper_mode=paper_mode,
                                       paper_balance=paper_bal)
    return clients
