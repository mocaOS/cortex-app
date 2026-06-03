"""Unit tests for the at-rest secret encryption layer (crypto_service).

CryptoService is constructed directly with an explicit `key_env` so these
tests never depend on the real ENCRYPTION_KEY / .env.
"""

import pytest
from cryptography.fernet import Fernet

from app.services.crypto_service import (
    ENC_PREFIX,
    CryptoError,
    CryptoService,
)


KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()


@pytest.fixture
def crypto():
    return CryptoService(key_env=KEY_A)


@pytest.fixture
def disabled():
    return CryptoService(key_env="")


# ---------------------------------------------------------------------------
# Round-trip & format
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_encrypt_decrypt_round_trip(self, crypto):
        secret = "ghp_supersecrettoken1234"
        token = crypto.encrypt(secret)
        assert token != secret
        assert token.startswith(ENC_PREFIX)
        assert crypto.decrypt(token) == secret

    def test_unicode_round_trip(self, crypto):
        secret = "pässwörd-日本語-🔑"
        assert crypto.decrypt(crypto.encrypt(secret)) == secret

    def test_empty_and_none_passthrough(self, crypto):
        assert crypto.encrypt("") == ""
        assert crypto.encrypt(None) is None
        assert crypto.decrypt("") == ""
        assert crypto.decrypt(None) is None

    def test_double_encrypt_is_idempotent(self, crypto):
        token = crypto.encrypt("secret")
        assert crypto.encrypt(token) == token

    def test_plaintext_decrypt_passthrough(self, crypto):
        # Pre-migration plaintext values pass through reads unchanged
        assert crypto.decrypt("plaintext-token") == "plaintext-token"


# ---------------------------------------------------------------------------
# Disabled mode (no ENCRYPTION_KEY)
# ---------------------------------------------------------------------------

class TestDisabled:
    def test_is_enabled(self, crypto, disabled):
        assert crypto.is_enabled()
        assert not disabled.is_enabled()

    def test_encrypt_passthrough_when_disabled(self, disabled):
        assert disabled.encrypt("secret") == "secret"

    def test_plaintext_decrypt_passthrough_when_disabled(self, disabled):
        assert disabled.decrypt("secret") == "secret"

    def test_encrypted_value_while_disabled_raises(self, crypto, disabled):
        token = crypto.encrypt("secret")
        with pytest.raises(CryptoError, match="not set"):
            disabled.decrypt(token)

    def test_whitespace_only_key_is_disabled(self):
        assert not CryptoService(key_env="  , ,").is_enabled()


# ---------------------------------------------------------------------------
# Malformed keys
# ---------------------------------------------------------------------------

class TestMalformedKey:
    def test_malformed_key_raises_runtime_error(self):
        with pytest.raises(RuntimeError, match="not a valid"):
            CryptoService(key_env="not-a-valid-fernet-key")

    def test_malformed_second_key_raises(self):
        with pytest.raises(RuntimeError, match="entry 2 of 2"):
            CryptoService(key_env=f"{KEY_A},bogus")


# ---------------------------------------------------------------------------
# Rotation (MultiFernet)
# ---------------------------------------------------------------------------

class TestRotation:
    def test_old_key_still_decrypts_after_rotation(self):
        old = CryptoService(key_env=KEY_A)
        token = old.encrypt("secret")
        rotated = CryptoService(key_env=f"{KEY_B},{KEY_A}")
        assert rotated.decrypt(token) == "secret"

    def test_needs_reencrypt_detects_rotated_out_key(self):
        old = CryptoService(key_env=KEY_A)
        token = old.encrypt("secret")
        rotated = CryptoService(key_env=f"{KEY_B},{KEY_A}")
        assert rotated.needs_reencrypt(token)
        fresh = rotated.encrypt(rotated.decrypt(token))
        assert not rotated.needs_reencrypt(fresh)
        assert rotated.decrypt(fresh) == "secret"

    def test_needs_reencrypt_false_for_plaintext_and_primary(self, crypto):
        assert not crypto.needs_reencrypt("plaintext")
        assert not crypto.needs_reencrypt(None)
        assert not crypto.needs_reencrypt(crypto.encrypt("secret"))

    def test_wrong_key_raises_crypto_error(self):
        token = CryptoService(key_env=KEY_A).encrypt("secret")
        other = CryptoService(key_env=KEY_B)
        with pytest.raises(CryptoError, match="changed or removed"):
            other.decrypt(token)
        # And never reports as re-encryptable (undecryptable by any key)
        assert not other.needs_reencrypt(token)


# ---------------------------------------------------------------------------
# Dict helpers
# ---------------------------------------------------------------------------

class TestDictHelpers:
    def test_encrypt_fields_touches_only_secret_names(self, crypto):
        data = {"api_key": "sk-123", "base_url": "https://api.example.com"}
        out = crypto.encrypt_fields(data, {"api_key"})
        assert out["api_key"].startswith(ENC_PREFIX)
        assert out["base_url"] == "https://api.example.com"
        # Original dict untouched
        assert data["api_key"] == "sk-123"

    def test_decrypt_fields_round_trip(self, crypto):
        data = {"api_key": "sk-123", "region": "eu"}
        enc = crypto.encrypt_fields(data, {"api_key"})
        dec = crypto.decrypt_fields(enc, {"api_key"})
        assert dec == data

    def test_non_string_values_passthrough(self, crypto):
        data = {"api_key": None, "retries": 3}
        out = crypto.encrypt_fields(data, {"api_key", "retries"})
        assert out == data

    def test_save_then_save_again_no_double_encryption(self, crypto):
        # Simulates PUT-merge preserving an already-encrypted value
        once = crypto.encrypt_fields({"api_key": "sk-123"}, {"api_key"})
        twice = crypto.encrypt_fields(once, {"api_key"})
        assert twice["api_key"] == once["api_key"]
        assert crypto.decrypt(twice["api_key"]) == "sk-123"
