"""
Cryptography utilities for UltraClaude

Provides encryption/decryption for sensitive data like API keys and tokens.
Uses Fernet symmetric encryption with a key derived from environment or generated.
"""
import os
import base64
import hashlib
from pathlib import Path
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken

# Key storage location
DATA_DIR = Path.home() / ".ultraclaude"
KEY_FILE = DATA_DIR / ".encryption_key"


class CredentialEncryption:
    """Handles encryption and decryption of sensitive credentials."""

    _instance: Optional['CredentialEncryption'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._key: Optional[bytes] = None
        self._fernet: Optional[Fernet] = None
        self._load_or_generate_key()
        self._initialized = True

    def _load_or_generate_key(self):
        """Load encryption key from environment, file, or generate new one."""
        # Priority 1: Environment variable
        env_key = os.environ.get('ULTRACLAUDE_ENCRYPTION_KEY')
        if env_key:
            try:
                # Try to use it as-is (base64 encoded)
                self._key = base64.urlsafe_b64decode(env_key)
                if len(self._key) != 32:
                    # If not 32 bytes, derive a key from it
                    self._key = hashlib.sha256(env_key.encode()).digest()
            except Exception:
                # Derive a key from the string
                self._key = hashlib.sha256(env_key.encode()).digest()
            self._fernet = Fernet(base64.urlsafe_b64encode(self._key))
            return

        # Priority 2: Key file
        if KEY_FILE.exists():
            try:
                key_data = KEY_FILE.read_bytes()
                self._key = base64.urlsafe_b64decode(key_data)
                self._fernet = Fernet(key_data)
                return
            except Exception:
                pass

        # Priority 3: Generate new key
        self._generate_new_key()

    def _generate_new_key(self):
        """Generate a new encryption key and save it."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        key = Fernet.generate_key()
        self._key = base64.urlsafe_b64decode(key)
        self._fernet = Fernet(key)

        # Save key with restrictive permissions
        KEY_FILE.write_bytes(key)
        os.chmod(KEY_FILE, 0o600)

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a plaintext string.

        Args:
            plaintext: The string to encrypt

        Returns:
            Base64-encoded encrypted string
        """
        if not plaintext:
            return ""
        if not self._fernet:
            raise RuntimeError("Encryption not initialized")

        encrypted = self._fernet.encrypt(plaintext.encode('utf-8'))
        return encrypted.decode('utf-8')

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt an encrypted string.

        Args:
            ciphertext: Base64-encoded encrypted string

        Returns:
            Decrypted plaintext string
        """
        if not ciphertext:
            return ""
        if not self._fernet:
            raise RuntimeError("Encryption not initialized")

        try:
            decrypted = self._fernet.decrypt(ciphertext.encode('utf-8'))
            return decrypted.decode('utf-8')
        except InvalidToken:
            # Return empty string for invalid tokens (legacy unencrypted data)
            return ""

    def is_encrypted(self, value: str) -> bool:
        """
        Check if a value appears to be encrypted.

        Encrypted values are base64-encoded Fernet tokens that start with 'gAAAAA'.
        """
        if not value:
            return False
        return value.startswith('gAAAAA')

    def encrypt_if_needed(self, value: str) -> str:
        """Encrypt a value only if it's not already encrypted."""
        if not value:
            return ""
        if self.is_encrypted(value):
            return value
        return self.encrypt(value)

    def decrypt_or_return(self, value: str) -> str:
        """Decrypt a value, or return as-is if not encrypted (legacy data)."""
        if not value:
            return ""
        if not self.is_encrypted(value):
            # Legacy unencrypted value - return as-is
            return value
        return self.decrypt(value)

    def rotate_key(self, new_key: Optional[str] = None) -> bytes:
        """
        Rotate the encryption key.

        WARNING: This will invalidate all previously encrypted data!
        You must re-encrypt all data after key rotation.

        Args:
            new_key: Optional new key string. If not provided, generates a new key.

        Returns:
            The old key (for backup purposes)
        """
        old_key = self._key

        if new_key:
            self._key = hashlib.sha256(new_key.encode()).digest()
            key_b64 = base64.urlsafe_b64encode(self._key)
            self._fernet = Fernet(key_b64)
            KEY_FILE.write_bytes(key_b64)
        else:
            self._generate_new_key()

        os.chmod(KEY_FILE, 0o600)
        return old_key or b''


# Singleton instance
encryption = CredentialEncryption()


def encrypt(value: str) -> str:
    """Convenience function to encrypt a value."""
    return encryption.encrypt(value)


def decrypt(value: str) -> str:
    """Convenience function to decrypt a value."""
    return encryption.decrypt(value)


def encrypt_if_needed(value: str) -> str:
    """Convenience function to encrypt only if not already encrypted."""
    return encryption.encrypt_if_needed(value)


def decrypt_or_return(value: str) -> str:
    """Convenience function to decrypt or return legacy unencrypted value."""
    return encryption.decrypt_or_return(value)
