"""Encrypt/decrypt user data using Fernet (AES-128-CBC) with PBKDF2 key derivation."""

import os
import json
import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

USERS_FILE = os.path.join(os.path.dirname(__file__), "users.json")

# Fixed salt for PBKDF2 — not secret, just prevents rainbow tables.
# Changing this would invalidate all existing encrypted data.
_PBKDF2_SALT = b"ucla_parking_v2_salt"
_PBKDF2_ITERATIONS = 600_000


def _get_raw_key():
    """Get the raw encryption key from environment."""
    key = os.environ.get("ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("ENCRYPTION_KEY environment variable not set")
    return key


def _derive_key_pbkdf2(raw_key):
    """Derive a Fernet key using PBKDF2 (strong)."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_PBKDF2_SALT,
        iterations=_PBKDF2_ITERATIONS,
    )
    derived = kdf.derive(raw_key.encode())
    return base64.urlsafe_b64encode(derived)


def _derive_key_legacy(raw_key):
    """Derive a Fernet key using SHA-256 (legacy, for migration only)."""
    derived = hashlib.sha256(raw_key.encode()).digest()
    return base64.urlsafe_b64encode(derived)


def _get_fernet():
    """Create a Fernet instance using PBKDF2-derived key."""
    return Fernet(_derive_key_pbkdf2(_get_raw_key()))


def encrypt_data(data):
    """Encrypt a Python object to a base64 string (always uses PBKDF2)."""
    f = _get_fernet()
    plaintext = json.dumps(data).encode()
    return f.encrypt(plaintext).decode()


def decrypt_data(token):
    """Decrypt a base64 string back to a Python object.

    Tries PBKDF2 key first, falls back to legacy SHA-256 for migration.
    """
    raw_key = _get_raw_key()

    # Try PBKDF2 first (current)
    try:
        f = Fernet(_derive_key_pbkdf2(raw_key))
        plaintext = f.decrypt(token.encode())
        return json.loads(plaintext)
    except InvalidToken:
        pass

    # Fall back to legacy SHA-256 (auto-migration)
    f = Fernet(_derive_key_legacy(raw_key))
    plaintext = f.decrypt(token.encode())
    return json.loads(plaintext)


def load_users():
    """Load and decrypt the users list from users.json. Returns [] if missing."""
    if not os.path.exists(USERS_FILE):
        return []
    with open(USERS_FILE, "r") as fh:
        token = fh.read().strip()
    if not token:
        return []
    return decrypt_data(token)


def save_users(users):
    """Encrypt and save the users list to users.json (always uses PBKDF2)."""
    token = encrypt_data(users)
    with open(USERS_FILE, "w") as fh:
        fh.write(token)
