from __future__ import annotations

import hashlib
import hmac
import secrets

from trade_order_bridge.config import settings


def generate_webhook_key() -> str:
    return f"tvk_{secrets.token_urlsafe(24)}"


def key_prefix(raw_key: str) -> str:
    return raw_key[:8]


def random_salt() -> str:
    return secrets.token_hex(16)


def hash_key(raw_key: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        raw_key.encode("utf-8"),
        salt.encode("utf-8"),
        settings.key_hash_iterations,
    )
    return digest.hex()


def verify_key(raw_key: str, salt: str, expected_hash: str) -> bool:
    calculated = hash_key(raw_key, salt)
    return hmac.compare_digest(calculated, expected_hash)
