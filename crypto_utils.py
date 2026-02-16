"""Encrypt/decrypt user data using Fernet (AES-128-CBC)."""

import os
import json
import base64
import hashlib
from cryptography.fernet import Fernet

USERS_FILE = os.path.join(os.path.dirname(__file__), "users.json")


def _get_fernet():
    """Create a Fernet instance from the ENCRYPTION_KEY env var."""
    key = os.environ.get("ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("ENCRYPTION_KEY environment variable not set")
    # Derive a valid 32-byte Fernet key from any passphrase
    derived = hashlib.sha256(key.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(derived)
    return Fernet(fernet_key)


def encrypt_data(data):
    """Encrypt a Python object to a base64 string."""
    f = _get_fernet()
    plaintext = json.dumps(data).encode()
    return f.encrypt(plaintext).decode()


def decrypt_data(token):
    """Decrypt a base64 string back to a Python object."""
    f = _get_fernet()
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
    """Encrypt and save the users list to users.json."""
    token = encrypt_data(users)
    with open(USERS_FILE, "w") as fh:
        fh.write(token)
