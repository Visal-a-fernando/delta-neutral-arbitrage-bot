"""
utils/ws_feeds.py -- WebSocket price cache for near-real-time spread detection.

Runs ccxt.pro WebSocket subscriptions in a background thread. The main bot
continues using synchronous APScheduler but reads from this cache instead of
making REST API calls for price data — eliminating the 20s polling latency
and rate-limit pressure.

Falls back silently to REST polling if ccxt.pro is not installed or WS
connection fails. Zero code changes needed in callers when WS is unavailable.

Usage (bot_engine.py):
    from utils.ws_feeds import WSPriceCache
    ws = WSPriceCache()
    ws.register("binance", "ETH/USDT:USDT")
    ws.register("bybit",   "ETH/USDT:USDT")
    ws.start()                         # non-blocking, runs in background thread
    ...
    ws.stop()

Usage (exchange.py):
    price = ws.get("binance", "ETH/USDT:USDT")  # None if unavailable/stale
"""

import asyncio
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import ccxt.pro as ccxtpro
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False
    logger.info("ccxt.pro not installed — WebSocket feeds disabled, using REST polling")

# Map logical exchange names (from config) to ccxt.pro class names
_EXCHANGE_MAP = {
    "binance": "binanceusdm",
    "bybit":   "bybit",
    "okx":     "myokx",          # EEA accounts use myokx (my.okx.com)
    "gate":         "gate",           # Gate.io perpetuals
    "bitget":       "bitget",         # Bitget perpetuals
    "mexc":         "mexc",           # MEXC perpetuals (0% maker fee)
    "binance_spot": "binance",        # Binance spot (for basis trading)
}

# Maximum age (seconds) before a cached price is considered stale
_MAX_CACHE_AGE = 10.0


class WSPriceCache:
    """
    Thread-safe WebSocket price cache.
    Runs async ccxt.pro subscriptions in a dedicated daemon thread.
    """

    def __init__(self):
        self._cache: dict[tuple, dict] = {}   # (exchange_name, symbol) -> {last, quoteVolume, ts}
        self._lock = threading.Lock()
        self._subscriptions: dict[str, list[str]] = {}   # exchange_name -> [symbols]
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False

    def register(self, exchange_name: str, symbol: str):
        """Register a symbol on an exchange for WebSocket streaming."""
        if exchange_name not in self._subscriptions:
            self._subscriptions[exchange_name] = []
        if symbol not in self._subscriptions[exchange_name]:
            self._subscriptions[exchange_name].append(symbol)

    def start(self) -> bool:
        """
        Start WebSocket subscriptions in a background thread.
        Returns True if WS started, False if unavailable (bot falls back to REST).
        """
        if not _WS_AVAILABLE:
            return False
        if not self._subscriptions:
            logger.warning("WSPriceCache.start() called with no registered symbols")
            return False

        self._running = True
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ws-price-feeds")
        self._thread.start()
        logger.info(
            f"WebSocket price feeds started for "
            f"{sum(len(v) for v in self._subscriptions.values())} symbols "
            f"across {len(self._subscriptions)} exchange(s)"
        )
        return True

    def stop(self):
        """Signal the background loop to stop."""
        self._running = False
        if self._loop and not self._loop.is_closed():
            # Cancel all pending tasks first, then stop the loop.
            # This prevents "Future completed after loop stop" warnings.
            def _cancel_and_stop():
                for task in asyncio.all_tasks(self._loop):
                    task.cancel()
                self._loop.stop()
            if self._loop.is_running():
                self._loop.call_soon_threadsafe(_cancel_and_stop)
            else:
                self._loop.stop()

    def get(self, exchange_name: str, symbol: str) -> Optional[dict]:
        """
        Return cached ticker dict {last, bid, ask, quoteVolume} or None if stale/missing.
        Callers should fall back to REST fetch_ticker() when None is returned.
        bid/ask are included so spread detection uses executable prices, not mid.
        """
        with self._lock:
            entry = self._cache.get((exchange_name, symbol))
        if entry is None:
            return None
        if (time.time() - entry["ts"]) > _MAX_CACHE_AGE:
            return None
        return {
            "last":        entry["last"],
            "bid":         entry.get("bid"),
            "ask":         entry.get("ask"),
            "quoteVolume": entry.get("quoteVolume"),
        }

    def cache_size(self) -> int:
        with self._lock:
            return len(self._cache)

    def is_fresh(self, exchange_name: str, symbol: str) -> bool:
        """True if we have a non-stale price for this symbol."""
        return self.get(exchange_name, symbol) is not None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run_loop(self):
        """Entry point for the background daemon thread."""
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._subscribe_all())
        except RuntimeError as e:
            # "Event loop stopped before Future completed" is expected during
            # graceful shutdown when stop() is called — not an error.
            if "stopped before" in str(e) or "Event loop" in str(e):
                logger.debug(f"WS feed loop stopped (shutdown): {e}")
            else:
                logger.error(f"WS feed loop RuntimeError: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"WS feed loop error: {e}", exc_info=True)

    async def _subscribe_all(self):
        """Launch one watcher coroutine per exchange."""
        tasks = [
            self._watch_exchange(name, symbols)
            for name, symbols in self._subscriptions.items()
            if name in _EXCHANGE_MAP
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _watch_exchange(self, exchange_name: str, symbols: list[str]):
        """Subscribe to all symbols on one exchange, reconnecting on error."""
        ccxt_id = _EXCHANGE_MAP.get(exchange_name)
        if not hasattr(ccxtpro, ccxt_id):
            logger.warning(f"ccxt.pro has no exchange '{ccxt_id}' — skipping WS for {exchange_name}")
            return

        exchange_class = getattr(ccxtpro, ccxt_id)
        exchange = exchange_class({"enableRateLimit": True})

        logger.info(f"WS: subscribing to {len(symbols)} symbols on {exchange_name}")

        # Launch one watcher per symbol concurrently
        tasks = [self._watch_symbol(exchange, exchange_name, sym) for sym in symbols]
        await asyncio.gather(*tasks, return_exceptions=True)

        try:
            await exchange.close()
        except Exception:
            pass

    async def _watch_symbol(self, exchange, exchange_name: str, symbol: str):
        """Continuously stream ticker updates for one symbol, with auto-reconnect."""
        consecutive_errors = 0
        while self._running:
            try:
                ticker = await exchange.watch_ticker(symbol)
                last = ticker.get("last")
                if last:
                    bid = ticker.get("bid") or ticker.get("bidPrice")
                    ask = ticker.get("ask") or ticker.get("askPrice")
                    with self._lock:
                        self._cache[(exchange_name, symbol)] = {
                            "last":         float(last),
                            "bid":          float(bid) if bid else float(last),
                            "ask":          float(ask) if ask else float(last),
                            "quoteVolume":  ticker.get("quoteVolume"),
                            "ts":           time.time(),
                        }
                consecutive_errors = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                consecutive_errors += 1
                wait = min(5 * consecutive_errors, 60)
                logger.debug(f"WS {exchange_name}/{symbol} error (#{consecutive_errors}): {e} — retry in {wait}s")
                await asyncio.sleep(wait)
