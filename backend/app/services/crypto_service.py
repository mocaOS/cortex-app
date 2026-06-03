"""At-rest encryption for user-supplied secrets (git PATs, skill secret config).

Keyed by the ENCRYPTION_KEY setting: a comma-separated list of Fernet keys.
The first key encrypts; all keys decrypt (MultiFernet), enabling zero-downtime
rotation. Ciphertext is self-describing via the "enc:" prefix, so plaintext
values (pre-migration or encryption-disabled deployments) pass through reads
unchanged and the startup migration is idempotent.
"""

import logging
from typing import Dict, Optional, Set

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import get_settings

logger = logging.getLogger(__name__)

ENC_PREFIX = "enc:"


class CryptoError(Exception):
    """An encrypted value could not be decrypted (key missing/changed)."""


class CryptoService:
    """Fernet-based symmetric encryption with rotation support."""

    def __init__(self, key_env: Optional[str] = None):
        raw = key_env if key_env is not None else get_settings().encryption_key
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if not keys:
            self._mf: Optional[MultiFernet] = None
            self._primary: Optional[Fernet] = None
            return
        fernets = []
        for i, key in enumerate(keys):
            try:
                fernets.append(Fernet(key.encode()))
            except (ValueError, TypeError) as e:
                # Malformed key must fail loudly — never degrade to plaintext.
                raise RuntimeError(
                    f"ENCRYPTION_KEY entry {i + 1} of {len(keys)} is not a valid "
                    f"Fernet key (expected 32 url-safe base64-encoded bytes). "
                    f'Generate one with: python -c "from cryptography.fernet '
                    f'import Fernet; print(Fernet.generate_key().decode())"'
                ) from e
        self._mf = MultiFernet(fernets)
        self._primary = fernets[0]
        self._key_count = len(fernets)

    def is_enabled(self) -> bool:
        return self._mf is not None

    def encrypt(self, value: Optional[str]) -> Optional[str]:
        """Encrypt a plaintext value. Passthrough when disabled, empty, or
        already encrypted (idempotent)."""
        if not value or self._mf is None or value.startswith(ENC_PREFIX):
            return value
        return ENC_PREFIX + self._mf.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: Optional[str]) -> Optional[str]:
        """Decrypt an enc:-prefixed value. Plaintext values pass through.

        Raises CryptoError when an encrypted value is present but cannot be
        decrypted — never returns ciphertext as if it were the secret.
        """
        if not value or not value.startswith(ENC_PREFIX):
            return value
        if self._mf is None:
            raise CryptoError(
                "Encrypted value present but ENCRYPTION_KEY is not set"
            )
        try:
            return self._mf.decrypt(value[len(ENC_PREFIX):].encode("ascii")).decode("utf-8")
        except InvalidToken:
            raise CryptoError(
                "Cannot decrypt value (encryption key changed or removed)"
            )

    def needs_reencrypt(self, value: Optional[str]) -> bool:
        """True iff value is decryptable but not encrypted with the primary key
        (i.e. it was encrypted with a rotated-out key)."""
        if not value or not value.startswith(ENC_PREFIX) or self._mf is None:
            return False
        token = value[len(ENC_PREFIX):].encode("ascii")
        try:
            self._primary.decrypt(token)
            return False
        except InvalidToken:
            pass
        try:
            self._mf.decrypt(token)
            return True
        except InvalidToken:
            return False  # undecryptable by any key — re-encryption impossible

    def encrypt_fields(self, data: Dict[str, str], secret_names: Set[str]) -> Dict[str, str]:
        """Return a copy of `data` with the named string fields encrypted."""
        return {
            k: self.encrypt(v) if k in secret_names and isinstance(v, str) else v
            for k, v in data.items()
        }

    def decrypt_fields(self, data: Dict[str, str], secret_names: Set[str]) -> Dict[str, str]:
        """Return a copy of `data` with the named string fields decrypted."""
        return {
            k: self.decrypt(v) if k in secret_names and isinstance(v, str) else v
            for k, v in data.items()
        }

    def log_startup_status(self) -> None:
        if self.is_enabled():
            logger.info(
                f"Secret encryption ENABLED ({self._key_count} key(s) loaded; "
                f"primary key encrypts, all keys decrypt)"
            )
        else:
            logger.warning(
                "ENCRYPTION_KEY not set — user-supplied secrets (git PATs, skill "
                "secret config) are stored in PLAINTEXT. Set ENCRYPTION_KEY to "
                "enable at-rest encryption; existing secrets are migrated "
                "automatically on the next startup."
            )


def migrate_secrets_at_rest() -> None:
    """Idempotent startup migration: encrypt plaintext secrets and re-encrypt
    values produced by rotated-out keys. Never raises — failures are logged
    per item and startup continues (mirrors the schema-backfill pattern)."""
    crypto = get_crypto_service()
    if not crypto.is_enabled():
        return

    from app.services.neo4j_service import get_neo4j_service
    neo4j = get_neo4j_service()

    pats_encrypted = pats_rotated = 0
    try:
        for conn in neo4j.list_git_connections():
            conn_id = conn.get("id")
            raw = conn.get("pat")
            if not raw:
                continue
            try:
                if not raw.startswith(ENC_PREFIX):
                    updates = {"pat": crypto.encrypt(raw)}
                    if not conn.get("pat_last4"):
                        updates["pat_last4"] = raw[-4:]
                    neo4j.update_git_connection(conn_id, updates)
                    pats_encrypted += 1
                elif crypto.needs_reencrypt(raw):
                    plain = crypto.decrypt(raw)
                    neo4j.update_git_connection(conn_id, {"pat": crypto.encrypt(plain)})
                    pats_rotated += 1
            except CryptoError as e:
                logger.error(
                    f"Git connection '{conn_id}': stored PAT cannot be decrypted "
                    f"({e}); the PAT must be re-entered in the admin UI"
                )
    except Exception as e:
        logger.warning(f"Git PAT encryption migration failed: {e}")

    fields_encrypted = skills_touched = 0
    try:
        from app.services.skill_service import get_skill_service
        skill_service = get_skill_service()
        for skill in neo4j.export_all_skills():
            skill_id = skill.get("skill_id")
            if not skill_id:
                continue
            secret_names = skill_service._secret_field_names(skill_id)
            if not secret_names:
                continue
            cfg = skill_service.get_skill_config(skill_id)
            changed = False
            for name in secret_names:
                v = cfg.get(name)
                if not isinstance(v, str) or not v:
                    continue
                try:
                    if not v.startswith(ENC_PREFIX):
                        cfg[name] = crypto.encrypt(v)
                        changed = True
                        fields_encrypted += 1
                    elif crypto.needs_reencrypt(v):
                        cfg[name] = crypto.encrypt(crypto.decrypt(v))
                        changed = True
                        fields_encrypted += 1
                except CryptoError as e:
                    logger.error(
                        f"Skill '{skill_id}': secret '{name}' cannot be decrypted "
                        f"({e}); reconfigure it in the admin UI"
                    )
            if changed:
                skill_service.save_skill_config(skill_id, cfg)
                skills_touched += 1
    except Exception as e:
        logger.warning(f"Skill secret encryption migration failed: {e}")

    if pats_encrypted or pats_rotated or fields_encrypted:
        logger.info(
            f"Secret migration: encrypted {pats_encrypted} git PAT(s), "
            f"re-encrypted {pats_rotated} (key rotation), encrypted "
            f"{fields_encrypted} skill secret field(s) across {skills_touched} skill(s)"
        )


# =============================================================================
# Singleton
# =============================================================================

_crypto_service: Optional[CryptoService] = None


def get_crypto_service() -> CryptoService:
    global _crypto_service
    if _crypto_service is None:
        _crypto_service = CryptoService()
    return _crypto_service
