#!/usr/bin/env python3
"""
Delta-Neutral Trading Bot — Main Entry Point
============================================
Usage:
  python bot.py              # Live trading mode
  python bot.py --paper      # Paper trading simulation
  python bot.py --kill       # Emergency: close all positions and halt
  python bot.py --report     # Print today's performance report
  python bot.py --status     # Print bot status and open positions
"""

import argparse
import logging
import signal
import sys
import time

# Load .env file if present (must happen before any config/settings import)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv optional; use shell env vars instead

from config.settings import load_config
from config.logging_setup import setup_logging
from core.bot_engine import BotEngine
from core.kill_switch import emergency_kill
from reporting.reporter import generate_daily_report
from utils.database import init_db


def parse_args():
    parser = argparse.ArgumentParser(description="Delta-Neutral Trading Bot")
    parser.add_argument("--paper",  action="store_true", help="Run in paper trading mode (no real orders)")
    parser.add_argument("--kill",   action="store_true", help="Emergency kill: close all positions and halt")
    parser.add_argument("--report", action="store_true", help="Print today's performance report and exit")
    parser.add_argument("--status", action="store_true", help="Print current bot status and exit")
    parser.add_argument("--config", type=str, default="config/config.yaml", help="Path to config file")
    return parser.parse_args()


def handle_sigterm(signum, frame):
    logging.getLogger(__name__).warning("SIGTERM received — initiating graceful shutdown")
    sys.exit(0)


def main():
    args = parse_args()
    setup_logging()
    logger = logging.getLogger(__name__)

    # Load config
    cfg = load_config(args.config)

    # Init DB
    init_db()

    # ── Emergency kill ────────────────────────────────────────────────────────
    if args.kill:
        logger.critical("EMERGENCY KILL SWITCH ACTIVATED")
        emergency_kill(cfg)
        sys.exit(0)

    # ── One-shot report ───────────────────────────────────────────────────────
    if args.report:
        generate_daily_report(print_to_console=True)
        sys.exit(0)

    # ── Status ────────────────────────────────────────────────────────────────
    if args.status:
        from core.status import print_status
        print_status(cfg)
        sys.exit(0)

    # ── Normal / paper run ────────────────────────────────────────────────────
    mode = "PAPER" if args.paper else "LIVE"
    logger.info(f"Starting Delta-Neutral Bot in {mode} mode")

    if not args.paper:
        logger.warning("=" * 60)
        logger.warning("  LIVE TRADING MODE — REAL FUNDS AT RISK")
        logger.warning("  Ensure paper mode ran for >= 7 days first")
        logger.warning("=" * 60)
        time.sleep(3)  # Brief pause to allow cancellation

    signal.signal(signal.SIGTERM, handle_sigterm)

    engine = BotEngine(cfg, paper_mode=args.paper)
    try:
        engine.run()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt — shutting down gracefully")
        engine.shutdown()
    except Exception as e:
        logger.critical(f"Unhandled exception in main loop: {e}", exc_info=True)
        engine.shutdown()
        sys.exit(1)


if __name__ == "__main__":
    main()
