"""x402 payment gate — vendor-agnostic pay-per-query monetization.

Implements the x402 v2 protocol over its HTTP transport for the retrieval
endpoints. A monetized API key (one with `price_per_query`) stays the identity
and collection-scoping mechanism; this module adds the payment gate:

    no PAYMENT-SIGNATURE header  → 402 + PAYMENT-REQUIRED (requirements)
    PAYMENT-SIGNATURE present    → facilitator /verify → /settle → serve
                                   (+ PAYMENT-RESPONSE header via request.state)

Settlement happens BEFORE the handler runs ("settle-before-serve"): EIP-3009
nonce reuse is rejected on-chain, so a settled payload can never buy two
responses — no server-side nonce store needed — and SSE responses need the
PAYMENT-RESPONSE header before the first streamed byte anyway.

Vendor agnosticism: the x402 spec standardizes the facilitator REST interface
(POST /verify, POST /settle, GET /supported), so any compliant facilitator
works — the vendor is just a URL (plus optional static auth headers, stored
encrypted). No chain RPC, no signing keys, no vendor SDK.

Configuration lives on the X402Config Neo4j node (admin-edited at runtime,
gated by the X402_ENABLED env flag). A config is only *usable* once the
verification suite has passed against the exact payment-relevant field values
(bound via config_hash); any change to those fields invalidates verification
and the payment gate fails closed (503) until re-verified.
"""

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import Depends, HTTPException, Request

from app.config import get_settings
from app.models import (
    X402ConfigResponse,
    X402ConfigUpdate,
    X402EarningsResponse,
    X402KeyEarnings,
    X402VerifyCheck,
    X402VerifyResponse,
)
from app.services.auth_service import AuthResult, require_read_permission
from app.services.crypto_service import get_crypto_service
from app.services.neo4j_service import get_neo4j_service

logger = logging.getLogger(__name__)

X402_VERSION = 2
SCHEME_EXACT = "exact"

HEADER_PAYMENT_REQUIRED = "PAYMENT-REQUIRED"
HEADER_PAYMENT_SIGNATURE = "PAYMENT-SIGNATURE"
HEADER_PAYMENT_RESPONSE = "PAYMENT-RESPONSE"

# Payment-relevant config fields: changing any of these invalidates the
# verified state (the verification hash is computed over them). asset_name is
# NOT cosmetic: it becomes the EIP-3009 EIP-712 domain name wallets sign
# against — a mismatch with the token contract's name() makes every
# settlement revert on-chain (live-confirmed on Base mainnet, 2026-07-17:
# "USDC" vs the contract's "USD Coin").
_HASHED_FIELDS = (
    "pay_to",
    "facilitator_url",
    "network",
    "asset_address",
    "asset_name",
    "asset_decimals",
    "asset_eip712_version",
)


# =============================================================================
# Base64 JSON codecs (x402 HTTP transport carries JSON in base64 headers)
# =============================================================================

def encode_b64_json(obj: Any) -> str:
    return base64.b64encode(
        json.dumps(obj, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def decode_b64_json(value: str) -> Any:
    return json.loads(base64.b64decode(value, validate=True).decode("utf-8"))


# =============================================================================
# Amount handling (never float — the exact scheme requires exact atomic match)
# =============================================================================

def validate_price(price: str, decimals: int = 18) -> str:
    """Validate and normalize a human-unit price string.

    Returns the normalized string; raises ValueError with a client-safe
    message otherwise.
    """
    try:
        value = Decimal(str(price).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid price: {price!r} is not a decimal number")
    if not value.is_finite() or value <= 0:
        raise ValueError("Price must be a positive number")
    if value >= Decimal("1000000000"):
        raise ValueError("Price is implausibly large")
    exponent = value.normalize().as_tuple().exponent
    if isinstance(exponent, int) and -exponent > decimals:
        raise ValueError(
            f"Price has more than {decimals} decimal places "
            f"(the configured asset supports {decimals})"
        )
    return format(value.normalize(), "f")


def to_atomic(price: str, decimals: int) -> int:
    """Convert a human-unit price string to atomic token units."""
    value = Decimal(str(price).strip())
    atomic = value * (Decimal(10) ** decimals)
    if atomic != atomic.to_integral_value():
        raise ValueError(
            f"Price {price} does not convert exactly to {decimals}-decimal atomic units"
        )
    return int(atomic)


def format_atomic(amount: int, decimals: int) -> str:
    """Format atomic units back to a human-unit decimal string."""
    if decimals <= 0:
        return str(amount)
    quantum = Decimal(amount) / (Decimal(10) ** decimals)
    return format(quantum.normalize(), "f")


# Deep-research (agentic) queries consume an order of magnitude more inference
# than a standard ask, so monetized keys carry a price multiplier for them.
# Keys minted before the field existed read as the default.
DEFAULT_RESEARCH_MULTIPLIER = "10"


def validate_multiplier(raw: str) -> str:
    """Validate and normalize a research-price multiplier.

    '0' is legal and means "deep research forbidden on this key".
    Returns the normalized string; raises ValueError otherwise.
    """
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid multiplier: {raw!r} is not a decimal number")
    if not value.is_finite() or value < 0:
        raise ValueError("Research multiplier must be zero or a positive number")
    if value > 1000:
        raise ValueError("Research multiplier is implausibly large (max 1000)")
    return format(value.normalize(), "f")


def effective_price(price: str, multiplier: Optional[str]) -> str:
    """The human-unit price after applying the research multiplier."""
    factor = Decimal(multiplier if multiplier not in (None, "") else DEFAULT_RESEARCH_MULTIPLIER)
    value = Decimal(str(price).strip()) * factor
    return format(value.normalize(), "f")


# =============================================================================
# Address validation (config-time checks; the facilitator is authoritative at
# payment time)
# =============================================================================

# Pure-Python Keccak-256 (the pre-SHA3 padding variant Ethereum uses). Only
# runs during config validation — never on the request hot path — so speed is
# irrelevant. Correctness is pinned by test vectors in test_x402.py.

_KECCAK_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]

_KECCAK_ROT = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]

_U64 = (1 << 64) - 1


def _rotl64(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (64 - shift))) & _U64


def _keccak_f(a: List[List[int]]) -> None:
    for rc in _KECCAK_RC:
        # theta
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl64(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x][y] ^= d[x]
        # rho + pi
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rotl64(a[x][y], _KECCAK_ROT[x][y])
        # chi
        for x in range(5):
            for y in range(5):
                a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y] & _U64) & b[(x + 2) % 5][y])
        # iota
        a[0][0] ^= rc


def keccak256(data: bytes) -> bytes:
    rate = 136  # 1088-bit rate for 256-bit output
    # Original Keccak multi-rate padding (0x01 domain), not SHA3's 0x06.
    padded = bytearray(data)
    pad_len = rate - (len(padded) % rate)
    padded += b"\x00" * pad_len
    padded[len(data)] ^= 0x01
    padded[-1] ^= 0x80

    state = [[0] * 5 for _ in range(5)]
    for offset in range(0, len(padded), rate):
        block = padded[offset:offset + rate]
        for i in range(rate // 8):
            lane = int.from_bytes(block[8 * i:8 * i + 8], "little")
            state[i % 5][i // 5] ^= lane
        _keccak_f(state)

    out = bytearray()
    for i in range(4):  # 4 lanes = 32 bytes
        out += state[i % 5][i // 5].to_bytes(8, "little")
    return bytes(out)


def _eip55_checksum(address_hex: str) -> str:
    """Compute the EIP-55 mixed-case form of a lowercase 40-hex-char address."""
    digest = keccak256(address_hex.encode("ascii")).hex()
    return "".join(
        ch.upper() if ch.isalpha() and int(digest[i], 16) >= 8 else ch
        for i, ch in enumerate(address_hex)
    )


def validate_evm_address(address: str) -> Tuple[bool, str]:
    if not isinstance(address, str) or not address.startswith("0x") or len(address) != 42:
        return False, "Expected a 0x-prefixed 40-hex-character address"
    hex_part = address[2:]
    try:
        int(hex_part, 16)
    except ValueError:
        return False, "Address contains non-hexadecimal characters"
    if hex_part == hex_part.lower() or hex_part == hex_part.upper():
        # No checksum information encoded — format-valid.
        return True, "Valid address format (no EIP-55 checksum to verify)"
    if _eip55_checksum(hex_part.lower()) == hex_part:
        return True, "Valid EIP-55 checksummed address"
    return False, "EIP-55 checksum mismatch — the address contains a typo"


_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def validate_solana_address(address: str) -> Tuple[bool, str]:
    if not isinstance(address, str) or not (32 <= len(address) <= 44):
        return False, "Expected a base58 address of 32-44 characters"
    value = 0
    for ch in address:
        idx = _BASE58_ALPHABET.find(ch)
        if idx < 0:
            return False, f"Invalid base58 character: {ch!r}"
        value = value * 58 + idx
    n_leading = len(address) - len(address.lstrip("1"))
    decoded_len = n_leading + (value.bit_length() + 7) // 8
    if decoded_len != 32:
        return False, f"Address decodes to {decoded_len} bytes, expected 32"
    return True, "Valid base58 address (32 bytes)"


def validate_address_for_network(address: str, network: str) -> Tuple[bool, str]:
    """Format-validate a wallet/contract address for a CAIP-2 network."""
    namespace = network.split(":", 1)[0].lower() if network else ""
    if namespace == "eip155":
        return validate_evm_address(address)
    if namespace == "solana":
        return validate_solana_address(address)
    # Unknown namespace: can't format-check — accept, the facilitator's
    # /supported check still gates the network itself.
    return True, f"No format validator for network namespace '{namespace}' (skipped)"


# =============================================================================
# Config load/save (X402Config node; short in-process cache like API keys)
# =============================================================================

_config_cache: Optional[Tuple[float, Optional[dict]]] = None
_CONFIG_CACHE_TTL_SECONDS = 30.0


def invalidate_x402_config_cache() -> None:
    global _config_cache
    _config_cache = None


def config_hash(cfg: dict) -> str:
    """Hash of the payment-relevant fields (plus decrypted auth headers) —
    binds the verified state to the exact values that passed the checks."""
    material = {field: cfg.get(field) for field in _HASHED_FIELDS}
    try:
        material["facilitator_auth_headers"] = _decrypt_auth_headers(cfg) or {}
    except Exception:
        # Undecryptable headers (rotated-out ENCRYPTION_KEY): the hash can
        # never match a stored verified_hash → verification reads as invalid
        # and the payment gate fails closed instead of erroring.
        material["facilitator_auth_headers"] = "<undecryptable>"
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _decrypt_auth_headers(cfg: dict) -> Optional[Dict[str, str]]:
    raw = cfg.get("facilitator_auth_headers")
    if not raw:
        return None
    plain = get_crypto_service().decrypt(raw)
    return json.loads(plain) if plain else None


def load_x402_config(force: bool = False) -> Optional[dict]:
    """Read the stored config (cached ~30s; invalidated on save/verify)."""
    global _config_cache
    now = time.monotonic()
    if not force and _config_cache is not None and _config_cache[0] > now:
        return _config_cache[1]
    cfg = get_neo4j_service().get_x402_config()
    _config_cache = (now + _CONFIG_CACHE_TTL_SECONDS, cfg)
    return cfg


def config_complete(cfg: Optional[dict]) -> bool:
    if not cfg:
        return False
    return all(
        cfg.get(f) not in (None, "")
        for f in ("pay_to", "facilitator_url", "network", "asset_address")
    )


def is_config_verified(cfg: Optional[dict]) -> bool:
    if not config_complete(cfg):
        return False
    return bool(cfg.get("verified_hash")) and cfg["verified_hash"] == config_hash(cfg)


def _to_native_datetime(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    if hasattr(value, "to_native"):
        return value.to_native()
    return None


def build_config_response(cfg: Optional[dict]) -> X402ConfigResponse:
    settings = get_settings()
    if not cfg:
        return X402ConfigResponse(enabled=settings.x402_enabled, configured=False)
    return X402ConfigResponse(
        enabled=settings.x402_enabled,
        configured=config_complete(cfg),
        verified=is_config_verified(cfg),
        verified_at=_to_native_datetime(cfg.get("verified_at")),
        pay_to=cfg.get("pay_to"),
        facilitator_url=cfg.get("facilitator_url"),
        network=cfg.get("network"),
        asset_address=cfg.get("asset_address"),
        asset_name=cfg.get("asset_name"),
        asset_decimals=cfg.get("asset_decimals"),
        asset_eip712_version=cfg.get("asset_eip712_version"),
        max_timeout_seconds=cfg.get("max_timeout_seconds"),
        service_name=cfg.get("service_name"),
        facilitator_auth_headers_set=bool(cfg.get("facilitator_auth_headers")),
    )


def save_x402_config(update: X402ConfigUpdate) -> X402ConfigResponse:
    """Persist an admin config update; verification is invalidated whenever a
    payment-relevant field actually changed (hash comparison)."""
    existing = load_x402_config(force=True) or {}

    props: Dict[str, Any] = {
        "pay_to": update.pay_to.strip(),
        "facilitator_url": update.facilitator_url.strip().rstrip("/"),
        "network": update.network.strip(),
        "asset_address": update.asset_address.strip(),
        "asset_name": update.asset_name.strip(),
        "asset_decimals": update.asset_decimals,
        "asset_eip712_version": update.asset_eip712_version.strip(),
        "max_timeout_seconds": update.max_timeout_seconds,
        "service_name": (update.service_name or "").strip() or None,
    }

    # None = leave stored headers unchanged; {} = clear; dict = replace
    # (encrypted at rest, like git PATs / skill secrets).
    if update.facilitator_auth_headers is not None:
        if update.facilitator_auth_headers == {}:
            props["facilitator_auth_headers"] = None
        else:
            props["facilitator_auth_headers"] = get_crypto_service().encrypt(
                json.dumps(update.facilitator_auth_headers, sort_keys=True)
            )

    merged = {**existing, **props}
    changed = not existing or config_hash(merged) != config_hash(existing)
    if changed:
        # Payment-relevant change → previous verification no longer applies.
        props["verified_hash"] = None
        props["verified_at"] = None

    get_neo4j_service().save_x402_config(props)
    invalidate_x402_config_cache()
    return build_config_response(load_x402_config(force=True))


# =============================================================================
# Facilitator client (the vendor seam: any URL implementing the spec works)
# =============================================================================

class FacilitatorError(Exception):
    """Facilitator unreachable or returned a non-protocol response."""


class FacilitatorClient:
    def __init__(self, base_url: str, auth_headers: Optional[Dict[str, str]] = None,
                 read_timeout: float = 45.0):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json", **(auth_headers or {})}
        self.timeout = httpx.Timeout(connect=5.0, read=read_timeout, write=10.0, pool=5.0)

    async def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method, f"{self.base_url}{path}", json=body, headers=self.headers
                )
        except httpx.HTTPError as e:
            raise FacilitatorError(f"Facilitator request failed: {e}") from e
        if response.status_code >= 500:
            raise FacilitatorError(
                f"Facilitator returned HTTP {response.status_code} for {path}"
            )
        try:
            return response.json()
        except json.JSONDecodeError as e:
            raise FacilitatorError(
                f"Facilitator returned non-JSON response for {path} "
                f"(HTTP {response.status_code})"
            ) from e

    async def supported(self) -> dict:
        return await self._request("GET", "/supported")

    async def verify(self, payment_payload: dict, payment_requirements: dict) -> dict:
        return await self._request("POST", "/verify", {
            "x402Version": X402_VERSION,
            "paymentPayload": payment_payload,
            "paymentRequirements": payment_requirements,
        })

    async def settle(self, payment_payload: dict, payment_requirements: dict) -> dict:
        return await self._request("POST", "/settle", {
            "x402Version": X402_VERSION,
            "paymentPayload": payment_payload,
            "paymentRequirements": payment_requirements,
        })


def _facilitator_for(cfg: dict) -> FacilitatorClient:
    return FacilitatorClient(
        cfg["facilitator_url"],
        auth_headers=_decrypt_auth_headers(cfg),
        read_timeout=float(max(30, int(cfg.get("max_timeout_seconds") or 60))),
    )


# =============================================================================
# PaymentRequirements / PaymentRequired construction
# =============================================================================

def build_payment_requirements(cfg: dict, price_per_query: str) -> dict:
    return {
        "scheme": SCHEME_EXACT,
        "network": cfg["network"],
        "amount": str(to_atomic(price_per_query, int(cfg.get("asset_decimals") or 0))),
        "asset": cfg["asset_address"],
        "payTo": cfg["pay_to"],
        "maxTimeoutSeconds": int(cfg.get("max_timeout_seconds") or 60),
        "extra": {
            "name": cfg.get("asset_name") or "",
            "version": cfg.get("asset_eip712_version") or "2",
        },
    }


def build_payment_required(cfg: dict, requirements: dict, resource_url: str,
                           error: Optional[str] = None) -> dict:
    resource: Dict[str, Any] = {"url": resource_url, "mimeType": "application/json"}
    if cfg.get("service_name"):
        resource["serviceName"] = cfg["service_name"]
    payment_required: Dict[str, Any] = {
        "x402Version": X402_VERSION,
        "resource": resource,
        "accepts": [requirements],
    }
    if error:
        payment_required["error"] = error
    return payment_required


def _norm_addr(value: Any) -> str:
    return str(value).lower() if isinstance(value, str) else str(value)


def accepted_matches_requirements(accepted: dict, requirements: dict) -> bool:
    """Server-side parameter matching — never trust the client's `accepted`."""
    if not isinstance(accepted, dict):
        return False
    return (
        accepted.get("scheme") == requirements["scheme"]
        and accepted.get("network") == requirements["network"]
        and str(accepted.get("amount")) == requirements["amount"]
        and _norm_addr(accepted.get("asset")) == _norm_addr(requirements["asset"])
        and _norm_addr(accepted.get("payTo")) == _norm_addr(requirements["payTo"])
    )


# =============================================================================
# Verification suite (POST /api/admin/x402/verify)
# =============================================================================

async def run_verification(cfg: dict) -> X402VerifyResponse:
    """Run the config checks; on full pass, stamp verified_hash/verified_at."""
    checks: List[X402VerifyCheck] = []

    ok, detail = validate_address_for_network(cfg.get("pay_to") or "", cfg.get("network") or "")
    checks.append(X402VerifyCheck(
        check="payto_format", label="Recipient address format",
        passed=ok, detail=detail,
    ))

    ok, detail = validate_address_for_network(cfg.get("asset_address") or "", cfg.get("network") or "")
    checks.append(X402VerifyCheck(
        check="asset_format", label="Asset contract address format",
        passed=ok, detail=detail,
    ))

    supported: Optional[dict] = None
    try:
        supported = await _facilitator_for(cfg).supported()
        checks.append(X402VerifyCheck(
            check="facilitator_reachable", label="Facilitator reachable",
            passed=True,
            detail=f"GET /supported responded with {len(supported.get('kinds', []))} payment kind(s)",
        ))
    except FacilitatorError as e:
        checks.append(X402VerifyCheck(
            check="facilitator_reachable", label="Facilitator reachable",
            passed=False, detail=str(e),
        ))

    if supported is not None:
        kinds = supported.get("kinds") or []
        network = cfg.get("network")
        match = any(
            isinstance(k, dict)
            and k.get("scheme") == SCHEME_EXACT
            and k.get("network") == network
            for k in kinds
        )
        available = sorted({
            f"{k.get('scheme')}@{k.get('network')}" for k in kinds if isinstance(k, dict)
        })
        checks.append(X402VerifyCheck(
            check="scheme_network_supported", label="Scheme + network supported",
            passed=match,
            detail=(
                f"Facilitator supports '{SCHEME_EXACT}' on {network}" if match
                else f"'{SCHEME_EXACT}' on {network} not offered; facilitator supports: "
                     f"{', '.join(available) or 'none'}"
            ),
        ))
    else:
        checks.append(X402VerifyCheck(
            check="scheme_network_supported", label="Scheme + network supported",
            passed=False, detail="Skipped — facilitator unreachable",
        ))

    valid = all(c.passed for c in checks)
    verified_at: Optional[datetime] = None
    if valid:
        get_neo4j_service().mark_x402_verified(config_hash(cfg))
        invalidate_x402_config_cache()
        verified_at = datetime.utcnow()
        logger.info("x402 configuration verified (network=%s)", cfg.get("network"))
    return X402VerifyResponse(valid=valid, checks=checks, verified_at=verified_at)


# =============================================================================
# Earnings
# =============================================================================

def get_earnings() -> X402EarningsResponse:
    cfg = load_x402_config() or {}
    decimals = int(cfg.get("asset_decimals") or 0)
    data = get_neo4j_service().get_x402_earnings()
    return X402EarningsResponse(
        asset_name=cfg.get("asset_name"),
        payment_count=data["payment_count"],
        total_amount=format_atomic(data["total_atomic"], decimals),
        by_key=[
            X402KeyEarnings(
                key_id=row["key_id"],
                key_name=row.get("key_name") or "",
                payment_count=row["payment_count"],
                total_amount=format_atomic(row.get("total_atomic") or 0, decimals),
            )
            for row in data["by_key"]
        ],
    )


# =============================================================================
# The payment gate (FastAPI dependency on the retrieval endpoints)
# =============================================================================

_ASK_PATHS = frozenset({"/api/ask", "/api/ask/stream", "/api/ask/stream/thinking"})


async def _inspect_request_body(request: Request) -> Tuple[dict, bool]:
    """Read the JSON body (Starlette caches it — the endpoint's Pydantic
    parsing is unaffected) and decide whether this is a deep-research request.

    Money moves BEFORE the endpoint's own validation runs, so this also
    rejects requests that would fail validation anyway — a payer must never
    settle a payment for a guaranteed 422.
    """
    try:
        raw = await request.body()
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict):
            raise ValueError
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(
            status_code=400,
            detail="Request body must be a JSON object (no payment was taken)",
        )

    path = request.url.path
    required_field = "question" if path in _ASK_PATHS else "query"
    value = data.get(required_field)
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(
            status_code=422,
            detail=f"Field '{required_field}' is required (no payment was taken)",
        )

    # Mirror of main.py's agentic_requires_streaming guard: the non-streaming
    # /api/ask rejects use_agentic with 400 AFTER dependencies run — without
    # this pre-check a payer would settle (at the research rate!) for a
    # guaranteed error.
    if (
        path == "/api/ask"
        and data.get("use_agentic")
        and bool(getattr(get_settings(), "enable_agent_research", False))
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Agentic deep research is not supported on the non-streaming "
                "POST /api/ask endpoint — use POST /api/ask/stream "
                "(no payment was taken)"
            ),
        )

    # Mirrors the routing condition in main.py: agentic mode actually runs
    # only when requested, enabled on the instance, and not in fast-search.
    is_research = (
        path in _ASK_PATHS
        and data.get("use_agentic") is True
        and not data.get("use_fast_search")
        and bool(getattr(get_settings(), "enable_agentic_rag", False))
    )
    return data, is_research

def _payment_required_exc(cfg: dict, requirements: dict, request: Request,
                          error: str, status_code: int = 402) -> HTTPException:
    payment_required = build_payment_required(cfg, requirements, str(request.url), error)
    return HTTPException(
        status_code=status_code,
        detail=error,
        headers={HEADER_PAYMENT_REQUIRED: encode_b64_json(payment_required)},
    )


def _record_settlement(auth: AuthResult, requirements: dict, settlement: dict,
                       endpoint: str) -> None:
    """Best-effort accounting — a bookkeeping failure never fails a paid request."""
    try:
        get_neo4j_service().record_x402_payment(
            key_id=auth.key_id or "",
            key_name=auth.key_name or "",
            transaction=settlement.get("transaction") or "",
            network=settlement.get("network") or requirements["network"],
            payer=settlement.get("payer"),
            amount_atomic=int(requirements["amount"]),
            asset_address=requirements["asset"],
            endpoint=endpoint,
        )
    except Exception as e:
        logger.error(
            "Failed to record x402 payment (tx=%s key=%s): %s",
            settlement.get("transaction"), auth.key_id, e,
        )


async def enforce_x402_payment(
    request: Request,
    auth: AuthResult = Depends(require_read_permission),
) -> None:
    """402 gate for monetized keys. Free keys and admin pass through untouched.

    Flow: rebuild requirements server-side → require PAYMENT-SIGNATURE →
    match accepted against OUR requirements → facilitator verify → settle →
    stash PAYMENT-RESPONSE on request.state (emitted by the response
    middleware) → record the settlement. Fails closed on any infra error.
    """
    if not auth.is_monetized:
        return

    settings = get_settings()
    if not settings.x402_enabled:
        # A priced key exists but the deployment turned the feature off.
        raise HTTPException(
            status_code=403,
            detail="This API key requires x402 payments, which are disabled on this instance",
        )

    try:
        cfg = load_x402_config()
    except Exception as e:
        logger.error(f"x402 config unavailable: {e}")
        raise HTTPException(
            status_code=503,
            detail="Payment configuration temporarily unavailable",
            headers={"Retry-After": "5"},
        )
    if not is_config_verified(cfg):
        raise HTTPException(
            status_code=503,
            detail=(
                "x402 payments are not operational on this instance "
                "(configuration missing or unverified)"
            ),
            headers={"Retry-After": "60"},
        )

    # Deep-research pricing: agentic requests cost price × the key's research
    # multiplier ('0' = research not offered on this key). The multiplied
    # amount lands in the 402 challenge, so agents see the true cost of a
    # research query BEFORE signing anything.
    _, is_research = await _inspect_request_body(request)
    multiplier = auth.research_multiplier or DEFAULT_RESEARCH_MULTIPLIER
    if is_research and Decimal(multiplier) == 0:
        raise HTTPException(
            status_code=403,
            detail=(
                "This API key does not permit deep-research (agentic) queries. "
                "Retry without use_agentic, or use a key priced for research."
            ),
        )

    try:
        price = (
            effective_price(auth.price_per_query, multiplier)
            if is_research else auth.price_per_query
        )
        requirements = build_payment_requirements(cfg, price)
    except (ValueError, InvalidOperation) as e:
        logger.error(f"Invalid price on key {auth.key_id}: {e}")
        raise HTTPException(status_code=500, detail="Invalid price configuration for this API key")

    raw_header = request.headers.get(HEADER_PAYMENT_SIGNATURE)
    if not raw_header:
        raise _payment_required_exc(
            cfg, requirements, request, f"{HEADER_PAYMENT_SIGNATURE} header is required"
        )

    try:
        payload = decode_b64_json(raw_header)
        if not isinstance(payload, dict):
            raise ValueError("payment payload must be a JSON object")
    except (ValueError, binascii.Error, json.JSONDecodeError):
        raise _payment_required_exc(
            cfg, requirements, request,
            f"Malformed {HEADER_PAYMENT_SIGNATURE} header (invalid_payload)",
            status_code=400,
        )

    if payload.get("x402Version") != X402_VERSION:
        raise _payment_required_exc(
            cfg, requirements, request,
            f"Unsupported x402 version (invalid_x402_version); expected {X402_VERSION}",
            status_code=400,
        )

    if not accepted_matches_requirements(payload.get("accepted"), requirements):
        raise _payment_required_exc(
            cfg, requirements, request,
            "Payment payload does not match this resource's payment requirements",
        )

    facilitator = _facilitator_for(cfg)
    try:
        verification = await facilitator.verify(payload, requirements)
    except FacilitatorError as e:
        logger.error(f"x402 verify failed (facilitator): {e}")
        raise HTTPException(
            status_code=503,
            detail="Payment verification temporarily unavailable",
            headers={"Retry-After": "5"},
        )
    if not verification.get("isValid"):
        raise _payment_required_exc(
            cfg, requirements, request,
            f"Payment verification failed: {verification.get('invalidReason') or 'unknown reason'}",
        )

    # Settle BEFORE serving: on-chain nonce burn makes replays impossible and
    # the PAYMENT-RESPONSE header must exist before an SSE stream starts.
    try:
        settlement = await facilitator.settle(payload, requirements)
    except FacilitatorError as e:
        logger.error(f"x402 settle failed (facilitator): {e}")
        raise HTTPException(
            status_code=503,
            detail="Payment settlement temporarily unavailable",
            headers={"Retry-After": "5"},
        )
    if not settlement.get("success"):
        raise _payment_required_exc(
            cfg, requirements, request,
            f"Payment settlement failed: {settlement.get('errorReason') or 'unknown reason'}",
        )

    request.state.x402_payment_response = encode_b64_json(settlement)
    await asyncio.to_thread(
        _record_settlement, auth, requirements, settlement, request.url.path
    )
