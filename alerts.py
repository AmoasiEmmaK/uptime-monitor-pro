"""
Alerting module — sends email and/or Slack notifications when a
server's status changes (goes down, or recovers).

Configuration: prefers the encrypted alert_config.enc.json (created
by running setup_alerts.py), which stores SMTP password and Slack
webhook URL encrypted at rest rather than in plain text. Falls back
to the older plain-text alert_config.json if the encrypted one
doesn't exist, for backward compatibility.

If no config exists or alerts are disabled, alerting is silently
skipped so the monitor still works fine without it.

Only import/use functions from this file — don't put credentials
directly in monitor.py.
"""

import json
import os
import smtplib
import ssl
import urllib.request
from email.mime.text import MIMEText
from pathlib import Path

from crypto_utils import decrypt_value

CONFIG_DIR = Path(__file__).resolve().parent
ENCRYPTED_CONFIG_PATH = CONFIG_DIR / "alert_config.enc.json"
PLAINTEXT_CONFIG_PATH = CONFIG_DIR / "alert_config.json"  # legacy


def load_config():
    """
    Load alert config, preferring the encrypted file if present.
    Returns a dict with sensible "disabled" defaults if no config
    exists yet, so the monitor works fine without any alerting set up.
    """
    if os.path.exists(ENCRYPTED_CONFIG_PATH):
        try:
            with open(ENCRYPTED_CONFIG_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            print(f"[alerts] Warning: couldn't read {ENCRYPTED_CONFIG_PATH}, alerts disabled.")
            return {"enabled": False}

    if os.path.exists(PLAINTEXT_CONFIG_PATH):
        try:
            with open(PLAINTEXT_CONFIG_PATH, "r") as f:
                print("[alerts] Note: using legacy plain-text alert_config.json. "
                      "Run setup_alerts.py to switch to encrypted storage.")
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            print(f"[alerts] Warning: couldn't read {PLAINTEXT_CONFIG_PATH}, alerts disabled.")
            return {"enabled": False}

    return {"enabled": False}


def send_email(config: dict, subject: str, body: str):
    email_cfg = config.get("email", {})
    if not email_cfg.get("enabled"):
        return

    try:
        # Support both the new encrypted field and the old plain-text
        # field, so existing configs don't break.
        if "smtp_password_encrypted" in email_cfg:
            smtp_password = decrypt_value(email_cfg["smtp_password_encrypted"])
        else:
            smtp_password = email_cfg["smtp_password"]

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = email_cfg["from_address"]
        msg["To"] = ", ".join(email_cfg["to_addresses"])

        context = ssl.create_default_context()
        with smtplib.SMTP(email_cfg["smtp_host"], email_cfg.get("smtp_port", 587)) as server:
            server.starttls(context=context)
            server.login(email_cfg["smtp_username"], smtp_password)
            server.sendmail(
                email_cfg["from_address"],
                email_cfg["to_addresses"],
                msg.as_string(),
            )
        print(f"[alerts] Email sent: {subject}")
    except Exception as e:
        print(f"[alerts] Failed to send email: {e}")


def send_slack(config: dict, message: str):
    slack_cfg = config.get("slack", {})
    if not slack_cfg.get("enabled"):
        return

    if "webhook_url_encrypted" in slack_cfg:
        webhook_url = decrypt_value(slack_cfg["webhook_url_encrypted"])
    else:
        webhook_url = slack_cfg.get("webhook_url")

    if not webhook_url:
        return

    try:
        payload = json.dumps({"text": message}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
        print("[alerts] Slack message sent")
    except Exception as e:
        print(f"[alerts] Failed to send Slack message: {e}")


def send_webhooks(config: dict, message: str):
    """Send JSON notifications to configured generic webhook URLs."""
    for webhook_url in config.get("webhooks", []):
        if not webhook_url:
            continue
        try:
            payload = json.dumps({"message": message}).encode("utf-8")
            req = urllib.request.Request(
                webhook_url, data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5).close()
        except Exception as e:
            print(f"[alerts] Failed to send webhook: {e}")


def notify_status_change(name: str, address: str, is_up: bool):
    """
    Call this whenever a server's status changes (was up, now down,
    or vice versa). Sends via whichever channels are enabled in the
    alert config. No-ops safely if alerting isn't configured.
    """
    config = load_config()
    if not config.get("enabled"):
        return

    if is_up:
        subject = f"✅ RECOVERED: {name}"
        body = f"{name} ({address}) is back UP."
    else:
        subject = f"🔴 DOWN: {name}"
        body = f"{name} ({address}) is not responding."

    send_email(config, subject, body)
    send_slack(config, f"{subject}\n{body}")
    send_webhooks(config, f"{subject}\n{body}")
