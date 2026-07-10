"""alerts/alert_manager.py - SMS (Twilio), email (SMTP), and Telegram alerts."""

import logging
import smtplib
import os
import urllib.request
import urllib.parse
import json
from datetime import datetime, timezone
from email.mime.text import MIMEText

from utils.database import log_alert

logger = logging.getLogger(__name__)

# Rate-limit alerts: don't send the same type more than once per 30 min
ALERT_COOLDOWN_MINUTES = 30


class AlertManager:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.alert_cfg = cfg.get("alerts", {})
        self._last_sent: dict[str, datetime] = {}

    def send_alert(self, alert_type: str, message: str):
        """Send SMS + email + Telegram alert, respecting cooldown."""
        # Cooldown check
        last = self._last_sent.get(alert_type)
        if last and (datetime.now(timezone.utc) - last).total_seconds() < ALERT_COOLDOWN_MINUTES * 60:
            logger.debug(f"Alert '{alert_type}' suppressed by cooldown")
            return

        self._last_sent[alert_type] = datetime.now(timezone.utc)
        full_msg = f"[DeltaBot] {alert_type.upper()}: {message}"
        logger.warning(f"ALERT -> {full_msg}")   # ASCII arrow - avoids cp1252 UnicodeEncodeError on Windows

        sms_sent   = self._send_sms(full_msg)
        email_sent = self._send_email(alert_type, full_msg)
        self.send_telegram(full_msg)

        log_alert(alert_type, message, sent_sms=sms_sent, sent_email=email_sent)

    def send_telegram(self, message: str) -> bool:
        token   = os.environ.get(
            self.alert_cfg.get("telegram_token_env", "TELEGRAM_BOT_TOKEN"), ""
        )
        chat_id = os.environ.get(
            self.alert_cfg.get("telegram_chat_id_env", "TELEGRAM_CHAT_ID"), ""
        )
        if not token or not chat_id:
            logger.debug("Telegram not configured - skipping")
            return False

        try:
            import ssl
            import certifi

            payload = json.dumps({
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
            }).encode("utf-8")

            url = f"https://api.telegram.org/bot{token}/sendMessage"

            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            context = ssl.create_default_context(cafile=certifi.where())

            with urllib.request.urlopen(req, timeout=10, context=context) as resp:
                ok = json.loads(resp.read()).get("ok", False)

            if ok:
                logger.debug("Telegram message sent")

            return ok

        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    # ── SMS via Twilio ────────────────────────────────────────────────────────

    def _send_sms(self, message: str) -> bool:
        try:
            from twilio.rest import Client
            sid   = os.environ.get(self.alert_cfg.get("twilio_sid_env",   "TWILIO_ACCOUNT_SID",   ))
            token = os.environ.get(self.alert_cfg.get("twilio_token_env", "TWILIO_AUTH_TOKEN",     ))
            from_ = os.environ.get(self.alert_cfg.get("twilio_from_env",  "TWILIO_FROM_NUMBER",    ))
            to    = os.environ.get(self.alert_cfg.get("alert_phone_env",  "ALERT_PHONE_NUMBER",    ))

            # Skip if credentials are missing or still contain placeholder text
            if not all([sid, token, from_, to]) or "your_" in (sid or ""):
                logger.debug("Twilio credentials not configured - SMS skipped")
                return False

            client = Client(sid, token)
            client.messages.create(body=message[:1600], from_=from_, to=to)
            logger.info(f"SMS sent to {to}")
            return True
        except ImportError:
            logger.debug("twilio package not installed - SMS skipped")
            return False
        except Exception as e:
            logger.error(f"SMS send failed: {e}")
            return False

    # ── Email via SMTP ────────────────────────────────────────────────────────

    def _send_email(self, subject: str, body: str) -> bool:
        try:
            host  = os.environ.get(self.alert_cfg.get("smtp_host_env",  "SMTP_HOST"))
            port  = int(os.environ.get(self.alert_cfg.get("smtp_port_env", "SMTP_PORT") or "587", 587))
            user  = os.environ.get(self.alert_cfg.get("smtp_user_env",  "SMTP_USER"))
            pwd   = os.environ.get(self.alert_cfg.get("smtp_pass_env",  "SMTP_PASS"))
            to    = os.environ.get(self.alert_cfg.get("alert_email_env", "ALERT_EMAIL"))

            if not all([host, user, pwd, to]):
                logger.debug("SMTP not configured - email skipped")
                return False

            msg = MIMEText(body)
            msg["Subject"] = f"[DeltaBot Alert] {subject}"
            msg["From"]    = user
            msg["To"]      = to

            with smtplib.SMTP(host, port, timeout=10) as server:
                server.ehlo()
                server.starttls()
                server.login(user, pwd)
                server.sendmail(user, [to], msg.as_string())

            logger.info(f"Alert email sent to {to}")
            return True
        except Exception as e:
            logger.error(f"Email send failed: {e}")
            return False
