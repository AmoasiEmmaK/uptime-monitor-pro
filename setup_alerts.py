"""
Interactive setup for alert credentials. Run this once (or whenever
you want to change your alert settings) to configure email/Slack
alerting. Credentials are encrypted before being saved to disk —
see crypto_utils.py for how.

Run with:
    python setup_alerts.py
"""

import json
import getpass

from crypto_utils import encrypt_value

CONFIG_PATH = "alert_config.enc.json"


def main():
    print("=== Uptime Monitor: Alert Setup ===")
    print("Credentials you enter here are encrypted before being saved.\n")

    config = {"enabled": False, "email": {"enabled": False}, "slack": {"enabled": False}}

    overall_enabled = input("Enable alerting overall? (y/n): ").strip().lower() == "y"
    config["enabled"] = overall_enabled

    if overall_enabled:
        setup_email = input("\nSet up email alerts? (y/n): ").strip().lower() == "y"
        if setup_email:
            print("\n-- Email setup (Gmail example) --")
            print("Use an App Password, not your real password:")
            print("https://myaccount.google.com/apppasswords\n")
            smtp_host = input("SMTP host [smtp.gmail.com]: ").strip() or "smtp.gmail.com"
            smtp_port = input("SMTP port [587]: ").strip() or "587"
            smtp_username = input("SMTP username / your email: ").strip()
            smtp_password = getpass.getpass("App password (hidden as you type): ").strip()
            from_address = input(f"From address [{smtp_username}]: ").strip() or smtp_username
            to_addresses_raw = input("Recipient email(s), comma-separated: ").strip()
            to_addresses = [a.strip() for a in to_addresses_raw.split(",") if a.strip()]

            config["email"] = {
                "enabled": True,
                "smtp_host": smtp_host,
                "smtp_port": int(smtp_port),
                "smtp_username": smtp_username,
                # Encrypted before storage — only readable with secret.key
                "smtp_password_encrypted": encrypt_value(smtp_password),
                "from_address": from_address,
                "to_addresses": to_addresses,
            }

        setup_slack = input("\nSet up Slack alerts? (y/n): ").strip().lower() == "y"
        if setup_slack:
            print("\n-- Slack setup --")
            print("Create an Incoming Webhook at https://api.slack.com/apps\n")
            webhook_url = getpass.getpass("Webhook URL (hidden as you type): ").strip()
            config["slack"] = {
                "enabled": True,
                # Encrypted before storage — only readable with secret.key
                "webhook_url_encrypted": encrypt_value(webhook_url),
            }

    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

    print(f"\nSaved encrypted config to {CONFIG_PATH}.")
    print("Restart monitor.py for changes to take effect.")


if __name__ == "__main__":
    main()
