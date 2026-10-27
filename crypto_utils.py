"""
Encryption helpers for storing sensitive alert credentials (SMTP
password, Slack webhook URL) at rest, instead of plain text.

Uses Fernet from the `cryptography` package — a well-established,
audited symmetric encryption scheme (AES-128 in CBC mode with HMAC
authentication under the hood). This is real encryption, not a toy
cipher.

How it works:
- A random encryption key is generated once and stored in secret.key
- alert_config.enc.json stores encrypted values instead of plain text
- Only someone with secret.key can decrypt the credentials

IMPORTANT: secret.key is what makes the encryption meaningful.
- Never commit it to version control
- Never share it alongside alert_config.enc.json
- If you lose it, the encrypted config becomes unreadable and you'll
  need to run setup_alerts.py again with fresh credentials
- Anyone who has BOTH secret.key AND alert_config.enc.json can read
  the credentials — the key is what needs protecting, not the file
"""

import os
from cryptography.fernet import Fernet

KEY_PATH = "secret.key"


def get_or_create_key() -> bytes:
    """Load the encryption key, generating one on first run."""
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "rb") as f:
            return f.read()

    key = Fernet.generate_key()
    with open(KEY_PATH, "wb") as f:
        f.write(key)
    print(f"[crypto_utils] Generated a new encryption key at {KEY_PATH}. "
          f"Keep this file private and don't lose it.")
    return key


def encrypt_value(plaintext: str) -> str:
    """Encrypt a string, returning a token safe to store in JSON."""
    key = get_or_create_key()
    fernet = Fernet(key)
    return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_value(token: str) -> str:
    """Decrypt a token produced by encrypt_value."""
    key = get_or_create_key()
    fernet = Fernet(key)
    return fernet.decrypt(token.encode("utf-8")).decode("utf-8")
