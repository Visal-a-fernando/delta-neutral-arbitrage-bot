"""reporting/profit_extractor.py — Weekly withdrawal and monthly allocation logic."""

import logging
import os
from datetime import datetime, timezone

from utils.database import (
    get_weekly_net_profit, get_monthly_net_profit, log_withdrawal, log_risk_event
)

logger = logging.getLogger(__name__)


class ProfitExtractor:
    def __init__(self, cfg: dict, exchanges: dict, alert_manager):
        self.cfg           = cfg
        self.exchanges     = exchanges
        self.alert_manager = alert_manager
        self.pe_cfg        = cfg["profit_extraction"]

    # ── Weekly withdrawal (Friday 18:00 AWST) ────────────────────────────────

    def weekly_withdrawal(self):
        """Withdraw 35% of weekly net profit to cold wallet."""
        weekly_profit = get_weekly_net_profit()
        if weekly_profit <= 0:
            logger.info("Weekly withdrawal: no profit to withdraw this week")
            return

        withdrawal_pct = self.pe_cfg["weekly_withdrawal_pct"]
        amount         = weekly_profit * withdrawal_pct
        cold_wallet    = os.environ.get(
            self.pe_cfg.get("cold_wallet_address_env", "COLD_WALLET_ADDRESS"), "NOT_CONFIGURED"
        )

        logger.info(f"WEEKLY WITHDRAWAL: ${amount:.2f} ({withdrawal_pct*100:.0f}% of ${weekly_profit:.2f})")
        logger.info(f"Destination: {cold_wallet}")

        # In live mode this would trigger an exchange withdrawal API call
        # We log it and alert — actual withdrawal requires pre-approved whitelist on exchange
        log_withdrawal({
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "withdrawal_type": "weekly",
            "amount_usd":      amount,
            "destination":     cold_wallet,
            "exchange":        "all",
            "notes":           f"Auto weekly withdrawal {withdrawal_pct*100:.0f}% of ${weekly_profit:.2f} weekly profit"
        })

        self.alert_manager.send_alert(
            "weekly_withdrawal",
            f"Weekly withdrawal: ${amount:.2f} → {cold_wallet[:12]}... "
            f"({withdrawal_pct*100:.0f}% of ${weekly_profit:.2f} profit)"
        )

    # ── Monthly tasks (last day of month) ─────────────────────────────────────

    def monthly_tasks(self):
        """Allocate monthly profit: 25% tax, 40% reinvest, 35% liquidity buffer."""
        monthly_profit = get_monthly_net_profit()
        if monthly_profit <= 0:
            logger.info("Monthly tasks: no profit to allocate")
            return

        tax_pct      = self.pe_cfg["monthly_tax_pct"]
        reinvest_pct = self.pe_cfg["monthly_reinvest_pct"]
        liquidity_pct = self.pe_cfg["monthly_liquidity_pct"]

        tax_amount       = monthly_profit * tax_pct
        reinvest_amount  = monthly_profit * reinvest_pct
        liquidity_amount = monthly_profit * liquidity_pct

        logger.info(f"MONTHLY ALLOCATION — Total profit: ${monthly_profit:.2f}")
        logger.info(f"  → AUD tax reserve:   ${tax_amount:.2f} ({tax_pct*100:.0f}%)")
        logger.info(f"  → Reinvest margin:   ${reinvest_amount:.2f} ({reinvest_pct*100:.0f}%)")
        logger.info(f"  → USDT liquidity:    ${liquidity_amount:.2f} ({liquidity_pct*100:.0f}%)")

        for label, amount, wtype in [
            ("ATO tax reserve", tax_amount,       "monthly_tax"),
            ("Margin reinvest", reinvest_amount,  "monthly_reinvest"),
            ("USDT liquidity",  liquidity_amount, "monthly_liquidity"),
        ]:
            log_withdrawal({
                "timestamp":       datetime.now(timezone.utc).isoformat(),
                "withdrawal_type": wtype,
                "amount_usd":      amount,
                "destination":     label,
                "exchange":        "all",
                "notes":           f"Monthly allocation: {label}"
            })

        log_risk_event(
            "monthly_allocation",
            f"Monthly profit ${monthly_profit:.2f} allocated: "
            f"tax=${tax_amount:.2f} reinvest=${reinvest_amount:.2f} liquidity=${liquidity_amount:.2f}",
            monthly_profit,
            "Allocation logged"
        )

        self.alert_manager.send_alert(
            "monthly_allocation",
            f"Monthly: profit=${monthly_profit:.2f} | "
            f"tax=${tax_amount:.2f} | reinvest=${reinvest_amount:.2f} | liquidity=${liquidity_amount:.2f}"
        )
