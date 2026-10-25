"""API key storage in Windows Credential Manager (via keyring), so the packaged .exe never
needs an environment variable or a plaintext file.

Lookup order: ANTHROPIC_API_KEY env var -> Credential Manager -> None (the SDK may still find
an `ant auth login` profile on its own).
"""
from __future__ import annotations

import os

SERVICE = "Retrozashi"
USERNAME = "anthropic_api_key"


def _keyring():
    try:
        import keyring
        return keyring
    except Exception:  # keyring missing, or no usable backend on this OS
        return None


def get_api_key() -> str | None:
    if key := os.environ.get("ANTHROPIC_API_KEY"):
        return key
    kr = _keyring()
    if kr is None:
        return None
    try:
        return kr.get_password(SERVICE, USERNAME)
    except Exception:
        return None


def set_api_key(key: str) -> None:
    """Stores the key in Credential Manager (shows up as generic credential 'Retrozashi')."""
    kr = _keyring()
    if kr is None:
        raise RuntimeError("No credential store available (pip install keyring)")
    kr.set_password(SERVICE, USERNAME, key.strip())


def clear_api_key() -> None:
    kr = _keyring()
    if kr is None:
        return
    try:
        kr.delete_password(SERVICE, USERNAME)
    except Exception:
        pass


def looks_like_api_key(key: str) -> bool:
    key = key.strip()
    return key.startswith("sk-ant-") and len(key) > 20 and not any(c.isspace() for c in key)
