"""Secret storage. Passwords never live in config.toml; it only holds a reference marker.

Primary backend: the system keyring (KWallet / GNOME Keyring / any Secret Service provider)
via the `keyring` package. Fallback when no keyring is reachable: a Fernet-encrypted vault
file in the state dir whose key file sits next to it (0600). The fallback protects against
casual reading and accidental sharing of config.toml, not against an attacker with access to
the same user account.

Markers in config: "@keyring:<key>" or "@vault:<key>". Anything else is treated as a plaintext
secret and migrated on the next load.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .config import state_dir

log = logging.getLogger(__name__)

SERVICE = "printcrastinator"
MARK_KEYRING = "@keyring:"
MARK_VAULT = "@vault:"


def _vault_paths() -> tuple[Path, Path]:
    d = state_dir()
    return d / "vault.key", d / "vault.json"


def _fernet():
    from cryptography.fernet import Fernet

    key_path, _ = _vault_paths()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if not key_path.exists():
        key_path.write_bytes(Fernet.generate_key())
        os.chmod(key_path, 0o600)
    return Fernet(key_path.read_bytes())


def _vault_read() -> dict[str, str]:
    _, vault = _vault_paths()
    if not vault.exists():
        return {}
    f = _fernet()
    data = json.loads(vault.read_text())
    return {k: f.decrypt(v.encode()).decode() for k, v in data.items()}


def _vault_write(entries: dict[str, str]) -> None:
    _, vault = _vault_paths()
    f = _fernet()
    vault.parent.mkdir(parents=True, exist_ok=True)
    tmp = vault.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: f.encrypt(v.encode()).decode() for k, v in entries.items()}))
    os.chmod(tmp, 0o600)
    tmp.replace(vault)


def _use_keyring() -> bool:
    forced = os.environ.get("PRINTCRASTINATOR_SECRETS", "")
    if forced == "vault":
        return False
    if forced == "keyring":
        return True
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring

        return not isinstance(keyring.get_keyring(), FailKeyring)
    except Exception:
        return False


def store(key: str, secret: str) -> str:
    """Store the secret, return the marker to put into the config."""
    if _use_keyring():
        try:
            import keyring

            keyring.set_password(SERVICE, key, secret)
            return MARK_KEYRING + key
        except Exception as exc:
            log.warning("keyring unavailable (%s), using encrypted vault file", exc)
    entries = _vault_read()
    entries[key] = secret
    _vault_write(entries)
    return MARK_VAULT + key


def load(marker: str) -> str:
    """Resolve a marker to the secret. Plain values are returned unchanged."""
    if marker.startswith(MARK_KEYRING):
        import keyring

        value = keyring.get_password(SERVICE, marker[len(MARK_KEYRING) :])
        if value is None:
            raise LookupError(f"secret {marker} not found in the keyring (wallet locked?)")
        return value
    if marker.startswith(MARK_VAULT):
        entries = _vault_read()
        key = marker[len(MARK_VAULT) :]
        if key not in entries:
            raise LookupError(f"secret {marker} not found in the vault")
        return entries[key]
    return marker


def delete(marker: str) -> None:
    try:
        if marker.startswith(MARK_KEYRING):
            import keyring

            keyring.delete_password(SERVICE, marker[len(MARK_KEYRING) :])
        elif marker.startswith(MARK_VAULT):
            entries = _vault_read()
            entries.pop(marker[len(MARK_VAULT) :], None)
            _vault_write(entries)
    except Exception as exc:
        log.debug("delete %s: %s", marker, exc)


def is_marker(value: str) -> bool:
    return value.startswith(MARK_KEYRING) or value.startswith(MARK_VAULT)


def backend_name() -> str:
    if _use_keyring():
        try:
            import keyring

            return type(keyring.get_keyring()).__module__.rsplit(".", 1)[-1] + " (system keyring)"
        except Exception:
            pass
    return "encrypted vault file"
