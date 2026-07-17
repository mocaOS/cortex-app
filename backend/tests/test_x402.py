"""x402 payment gate tests.

Covers the protocol primitives (codecs, amounts, address validation with
pinned Keccak/EIP-55 vectors), config hashing + verification invalidation,
the enforce_x402_payment dependency (every rejection path + the happy path
with a faked facilitator), the monetized-key auth hardening (endpoint
allowlist, MANAGE stripping), and the admin/key-CRUD endpoint guards.

No network: the facilitator is always monkeypatched.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.models import APIKeyPermission
from app.services import x402_service
from app.services.auth_service import (
    AuthResult,
    hash_api_key,
    require_read_permission,
    validate_api_key,
)
from app.services.x402_service import (
    FacilitatorClient,
    FacilitatorError,
    accepted_matches_requirements,
    build_payment_requirements,
    config_hash,
    decode_b64_json,
    effective_price,
    encode_b64_json,
    enforce_x402_payment,
    format_atomic,
    is_config_verified,
    keccak256,
    to_atomic,
    validate_evm_address,
    validate_multiplier,
    validate_price,
    validate_solana_address,
)

# EIP-55 spec example addresses (checksummed)
CHECKSUMMED = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
USDC_BASE_SEPOLIA = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"


@pytest.fixture(autouse=True)
def _reset_api_key_service(monkeypatch):
    """The APIKeyService singleton captures the Neo4j service at construction;
    reset it per test so it rebinds to this test's mock_neo4j."""
    monkeypatch.setattr("app.services.api_key_service._api_key_service", None)


def make_config(**overrides) -> dict:
    cfg = {
        "pay_to": CHECKSUMMED,
        "facilitator_url": "https://facilitator.example",
        "network": "eip155:84532",
        "asset_address": USDC_BASE_SEPOLIA,
        "asset_name": "USDC",
        "asset_decimals": 6,
        "asset_eip712_version": "2",
        "max_timeout_seconds": 60,
        "service_name": None,
        "facilitator_auth_headers": None,
    }
    cfg.update(overrides)
    return cfg


def verified_config(**overrides) -> dict:
    cfg = make_config(**overrides)
    cfg["verified_hash"] = config_hash(cfg)
    cfg["verified_at"] = datetime.utcnow()
    return cfg


def make_request(path: str = "/api/search", headers: dict | None = None,
                 body: dict | str | None = None) -> Request:
    """Build a Request with a readable body (the payment gate inspects it)."""
    if body is None:
        body = {"question": "hi"} if path.startswith("/api/ask") else {"query": "hi"}
    payload = (body if isinstance(body, str) else json.dumps(body)).encode()

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    raw_headers = [
        (k.lower().encode("ascii"), v.encode("ascii"))
        for k, v in (headers or {}).items()
    ]
    return Request(scope={
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": raw_headers,
        "query_string": b"",
        "server": ("test", 80),
        "scheme": "http",
    }, receive=receive)


def monetized_auth(price: str = "0.05", multiplier: str | None = None) -> AuthResult:
    return AuthResult(
        is_authenticated=True,
        permissions=[APIKeyPermission.READ],
        key_id="key_pub1",
        key_name="Public Paid",
        price_per_query=price,
        research_multiplier=multiplier,
    )


def make_payload(requirements: dict) -> dict:
    return {
        "x402Version": 2,
        "accepted": dict(requirements),
        "payload": {"signature": "0xsig", "authorization": {"nonce": "0xabc"}},
    }


# =============================================================================
# Protocol primitives
# =============================================================================

class TestCodecs:
    def test_roundtrip(self):
        obj = {"a": [1, 2], "b": "täxt"}
        assert decode_b64_json(encode_b64_json(obj)) == obj

    def test_decode_rejects_garbage(self):
        with pytest.raises(Exception):
            decode_b64_json("!!not-base64!!")


class TestAmounts:
    def test_to_atomic(self):
        assert to_atomic("0.05", 6) == 50_000
        assert to_atomic("1", 6) == 1_000_000
        assert to_atomic("0.000001", 6) == 1

    def test_to_atomic_rejects_sub_atomic(self):
        with pytest.raises(ValueError):
            to_atomic("0.0000001", 6)

    def test_format_atomic(self):
        assert format_atomic(50_000, 6) == "0.05"
        assert format_atomic(0, 6) == "0"
        assert format_atomic(1_000_000, 6) == "1"

    def test_validate_price(self):
        assert validate_price("0.05", 6) == "0.05"
        assert validate_price(" 1.50 ", 6) == "1.5"

    @pytest.mark.parametrize("bad", ["", "abc", "-1", "0", "1e12", "0.0000001"])
    def test_validate_price_rejects(self, bad):
        # 1e12 is a valid Decimal but above the plausibility cap
        with pytest.raises(ValueError):
            validate_price(bad, 6)


class TestKeccakAndAddresses:
    def test_keccak256_pinned_vectors(self):
        # Known Keccak-256 (pre-SHA3 padding) vectors — proves the pure-Python
        # implementation is the Ethereum variant, not SHA3-256.
        assert keccak256(b"").hex() == (
            "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
        )
        assert keccak256(b"abc").hex() == (
            "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
        )

    @pytest.mark.parametrize("addr", [
        # EIP-55 spec examples
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
        "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
        "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
    ])
    def test_eip55_valid(self, addr):
        ok, _ = validate_evm_address(addr)
        assert ok

    def test_eip55_typo_detected(self):
        # Flip the case of one letter → checksum mismatch
        bad = "0x5aaeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
        ok, detail = validate_evm_address(bad)
        assert not ok and "checksum" in detail.lower()

    def test_all_lowercase_accepted(self):
        ok, _ = validate_evm_address(CHECKSUMMED.lower())
        assert ok

    @pytest.mark.parametrize("bad", ["", "0x123", "5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed", "0x" + "g" * 40])
    def test_evm_format_rejects(self, bad):
        assert not validate_evm_address(bad)[0]

    def test_solana_valid(self):
        ok, _ = validate_solana_address("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
        assert ok

    @pytest.mark.parametrize("bad", ["", "abc", "0OIl" * 10, "E" * 50])
    def test_solana_rejects(self, bad):
        assert not validate_solana_address(bad)[0]


# =============================================================================
# Config hashing / verification binding
# =============================================================================

class TestConfigHash:
    def test_stable(self):
        assert config_hash(make_config()) == config_hash(make_config())

    @pytest.mark.parametrize("field,value", [
        ("pay_to", "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"),
        ("facilitator_url", "https://other.example"),
        ("network", "eip155:8453"),
        ("asset_decimals", 18),
        # asset_name is the EIP-712 domain name — payment-critical, not
        # cosmetic ("USDC" vs "USD Coin" reverts settlement on-chain).
        ("asset_name", "USD Coin"),
    ])
    def test_payment_field_changes_hash(self, field, value):
        assert config_hash(make_config(**{field: value})) != config_hash(make_config())

    def test_cosmetic_field_does_not_change_hash(self):
        assert config_hash(make_config(service_name="Shop")) == config_hash(make_config())

    def test_is_config_verified(self):
        assert is_config_verified(verified_config())
        assert not is_config_verified(make_config())          # never verified
        assert not is_config_verified(None)                   # no config
        tampered = verified_config()
        tampered["pay_to"] = "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"
        assert not is_config_verified(tampered)               # changed after verify


class TestAcceptedMatching:
    def test_exact_match(self):
        req = build_payment_requirements(make_config(), "0.05")
        assert accepted_matches_requirements(dict(req), req)

    def test_case_insensitive_addresses(self):
        req = build_payment_requirements(make_config(), "0.05")
        accepted = dict(req)
        accepted["payTo"] = accepted["payTo"].upper().replace("0X", "0x")
        accepted["asset"] = accepted["asset"].lower()
        assert accepted_matches_requirements(accepted, req)

    @pytest.mark.parametrize("field,value", [
        ("amount", "1"),
        ("network", "eip155:1"),
        ("scheme", "upto"),
        ("payTo", "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"),
    ])
    def test_mismatch_rejected(self, field, value):
        req = build_payment_requirements(make_config(), "0.05")
        accepted = dict(req)
        accepted[field] = value
        assert not accepted_matches_requirements(accepted, req)

    def test_non_dict_rejected(self):
        req = build_payment_requirements(make_config(), "0.05")
        assert not accepted_matches_requirements(None, req)


# =============================================================================
# enforce_x402_payment — the gate itself (faked facilitator, direct calls)
# =============================================================================

@pytest.fixture
def x402_on(_isolate_env, monkeypatch):
    """Enable the flag and install a verified config."""
    _isolate_env.x402_enabled = True
    cfg = verified_config()
    monkeypatch.setattr(x402_service, "load_x402_config", lambda force=False: cfg)
    return cfg


@pytest.fixture
def facilitator(monkeypatch):
    """Fake facilitator: valid verify + successful settle by default."""
    verify = AsyncMock(return_value={"isValid": True, "payer": "0xpayer"})
    settle = AsyncMock(return_value={
        "success": True, "transaction": "0xtx", "network": "eip155:84532",
        "payer": "0xpayer",
    })
    monkeypatch.setattr(FacilitatorClient, "verify", verify)
    monkeypatch.setattr(FacilitatorClient, "settle", settle)
    return MagicMock(verify=verify, settle=settle)


class TestEnforcePayment:
    async def test_free_key_passes_untouched(self, mock_neo4j):
        auth = AuthResult(is_authenticated=True, permissions=[APIKeyPermission.READ], key_id="k")
        await enforce_x402_payment(make_request(), auth)  # no exception

    async def test_disabled_flag_rejects_priced_key(self, mock_neo4j):
        with pytest.raises(HTTPException) as exc:
            await enforce_x402_payment(make_request(), monetized_auth())
        assert exc.value.status_code == 403

    async def test_unverified_config_fails_closed(self, _isolate_env, mock_neo4j, monkeypatch):
        _isolate_env.x402_enabled = True
        monkeypatch.setattr(x402_service, "load_x402_config", lambda force=False: make_config())
        with pytest.raises(HTTPException) as exc:
            await enforce_x402_payment(make_request(), monetized_auth())
        assert exc.value.status_code == 503

    async def test_missing_header_gets_402_with_requirements(self, x402_on, mock_neo4j):
        with pytest.raises(HTTPException) as exc:
            await enforce_x402_payment(make_request(), monetized_auth())
        assert exc.value.status_code == 402
        pr = decode_b64_json(exc.value.headers["PAYMENT-REQUIRED"])
        assert pr["x402Version"] == 2
        req = pr["accepts"][0]
        assert req["scheme"] == "exact"
        assert req["amount"] == "50000"  # 0.05 USDC @ 6 decimals
        assert req["payTo"] == CHECKSUMMED
        assert req["network"] == "eip155:84532"

    async def test_malformed_header_400(self, x402_on, mock_neo4j):
        request = make_request(headers={"PAYMENT-SIGNATURE": "!!!"})
        with pytest.raises(HTTPException) as exc:
            await enforce_x402_payment(request, monetized_auth())
        assert exc.value.status_code == 400

    async def test_wrong_version_400(self, x402_on, mock_neo4j):
        req = build_payment_requirements(x402_on, "0.05")
        payload = make_payload(req)
        payload["x402Version"] = 1
        request = make_request(headers={"PAYMENT-SIGNATURE": encode_b64_json(payload)})
        with pytest.raises(HTTPException) as exc:
            await enforce_x402_payment(request, monetized_auth())
        assert exc.value.status_code == 400

    async def test_accepted_mismatch_402(self, x402_on, mock_neo4j):
        req = build_payment_requirements(x402_on, "0.05")
        payload = make_payload(req)
        payload["accepted"]["amount"] = "1"  # client tries to underpay
        request = make_request(headers={"PAYMENT-SIGNATURE": encode_b64_json(payload)})
        with pytest.raises(HTTPException) as exc:
            await enforce_x402_payment(request, monetized_auth())
        assert exc.value.status_code == 402

    async def test_verify_invalid_402(self, x402_on, facilitator, mock_neo4j):
        facilitator.verify.return_value = {"isValid": False, "invalidReason": "insufficient_funds"}
        req = build_payment_requirements(x402_on, "0.05")
        request = make_request(headers={"PAYMENT-SIGNATURE": encode_b64_json(make_payload(req))})
        with pytest.raises(HTTPException) as exc:
            await enforce_x402_payment(request, monetized_auth())
        assert exc.value.status_code == 402
        assert "insufficient_funds" in exc.value.detail

    async def test_facilitator_down_503_fails_closed(self, x402_on, facilitator, mock_neo4j):
        facilitator.verify.side_effect = FacilitatorError("connection refused")
        req = build_payment_requirements(x402_on, "0.05")
        request = make_request(headers={"PAYMENT-SIGNATURE": encode_b64_json(make_payload(req))})
        with pytest.raises(HTTPException) as exc:
            await enforce_x402_payment(request, monetized_auth())
        assert exc.value.status_code == 503

    async def test_settle_failure_402(self, x402_on, facilitator, mock_neo4j):
        facilitator.settle.return_value = {"success": False, "errorReason": "invalid_transaction_state", "transaction": "", "network": "eip155:84532"}
        req = build_payment_requirements(x402_on, "0.05")
        request = make_request(headers={"PAYMENT-SIGNATURE": encode_b64_json(make_payload(req))})
        with pytest.raises(HTTPException) as exc:
            await enforce_x402_payment(request, monetized_auth())
        assert exc.value.status_code == 402
        assert "invalid_transaction_state" in exc.value.detail

    async def test_happy_path_settles_and_records(self, x402_on, facilitator, mock_neo4j):
        req = build_payment_requirements(x402_on, "0.05")
        request = make_request(headers={"PAYMENT-SIGNATURE": encode_b64_json(make_payload(req))})

        await enforce_x402_payment(request, monetized_auth())

        # verify then settle, both against OUR requirements
        facilitator.verify.assert_awaited_once()
        facilitator.settle.assert_awaited_once()
        assert facilitator.verify.await_args.args[1] == req

        # PAYMENT-RESPONSE stashed for the response middleware
        settlement = decode_b64_json(request.state.x402_payment_response)
        assert settlement["success"] is True and settlement["transaction"] == "0xtx"

        # settlement recorded for earnings/audit
        mock_neo4j.record_x402_payment.assert_called_once()
        kwargs = mock_neo4j.record_x402_payment.call_args.kwargs
        assert kwargs["key_id"] == "key_pub1"
        assert kwargs["amount_atomic"] == 50_000
        assert kwargs["transaction"] == "0xtx"

    async def test_recording_failure_does_not_fail_request(self, x402_on, facilitator, mock_neo4j):
        mock_neo4j.record_x402_payment.side_effect = RuntimeError("neo4j down")
        req = build_payment_requirements(x402_on, "0.05")
        request = make_request(headers={"PAYMENT-SIGNATURE": encode_b64_json(make_payload(req))})
        await enforce_x402_payment(request, monetized_auth())  # no exception
        assert request.state.x402_payment_response


# =============================================================================
# Deep-research pricing (per-key multiplier)
# =============================================================================

class TestMultiplierMath:
    def test_validate_multiplier(self):
        assert validate_multiplier("10") == "10"
        assert validate_multiplier("0") == "0"
        assert validate_multiplier(" 2.5 ") == "2.5"
        assert validate_multiplier("1") == "1"

    @pytest.mark.parametrize("bad", ["-1", "abc", "", "1e9", "1001"])
    def test_validate_multiplier_rejects(self, bad):
        with pytest.raises(ValueError):
            validate_multiplier(bad)

    def test_effective_price(self):
        assert effective_price("0.05", "10") == "0.5"
        assert effective_price("0.05", "1") == "0.05"
        assert effective_price("0.05", "2.5") == "0.125"
        # None/empty falls back to the default multiplier (10)
        assert effective_price("0.05", None) == "0.5"
        assert effective_price("0.05", "") == "0.5"


class TestResearchPricing:
    """Agentic requests are priced at price × multiplier, decided per request
    from the body — advertised in the 402 challenge before any signature."""

    def _challenge_amount(self, exc) -> str:
        pr = decode_b64_json(exc.value.headers["PAYMENT-REQUIRED"])
        return pr["accepts"][0]["amount"]

    async def _amount_for(self, x402_on, monkeypatch, body, auth=None):
        monkeypatch.setattr(x402_service.get_settings(), "enable_agentic_rag", True)
        request = make_request("/api/ask/stream", body=body)
        with pytest.raises(HTTPException) as exc:
            await enforce_x402_payment(request, auth or monetized_auth())
        assert exc.value.status_code == 402
        return self._challenge_amount(exc)

    async def test_agentic_charges_multiplied_price(self, x402_on, mock_neo4j, monkeypatch):
        amount = await self._amount_for(
            x402_on, monkeypatch, {"question": "deep", "use_agentic": True},
        )
        assert amount == "500000"  # 0.05 × 10 @ 6 decimals

    async def test_standard_ask_charges_base_price(self, x402_on, mock_neo4j, monkeypatch):
        amount = await self._amount_for(x402_on, monkeypatch, {"question": "quick"})
        assert amount == "50000"

    async def test_fast_search_never_research_priced(self, x402_on, mock_neo4j, monkeypatch):
        amount = await self._amount_for(
            x402_on, monkeypatch,
            {"question": "quick", "use_agentic": True, "use_fast_search": True},
        )
        assert amount == "50000"

    async def test_agentic_disabled_on_instance_charges_base(self, x402_on, mock_neo4j, monkeypatch):
        monkeypatch.setattr(x402_service.get_settings(), "enable_agentic_rag", False)
        request = make_request("/api/ask/stream", body={"question": "q", "use_agentic": True})
        with pytest.raises(HTTPException) as exc:
            await enforce_x402_payment(request, monetized_auth())
        assert self._challenge_amount(exc) == "50000"

    async def test_custom_multiplier(self, x402_on, mock_neo4j, monkeypatch):
        amount = await self._amount_for(
            x402_on, monkeypatch, {"question": "deep", "use_agentic": True},
            auth=monetized_auth(multiplier="2.5"),
        )
        assert amount == "125000"  # 0.05 × 2.5

    async def test_multiplier_zero_forbids_research(self, x402_on, mock_neo4j, monkeypatch):
        monkeypatch.setattr(x402_service.get_settings(), "enable_agentic_rag", True)
        request = make_request("/api/ask/stream", body={"question": "deep", "use_agentic": True})
        with pytest.raises(HTTPException) as exc:
            await enforce_x402_payment(request, monetized_auth(multiplier="0"))
        assert exc.value.status_code == 403
        assert "deep-research" in exc.value.detail

    async def test_multiplier_zero_still_allows_standard(self, x402_on, mock_neo4j):
        request = make_request("/api/ask/stream", body={"question": "quick"})
        with pytest.raises(HTTPException) as exc:
            await enforce_x402_payment(request, monetized_auth(multiplier="0"))
        assert exc.value.status_code == 402
        assert self._challenge_amount(exc) == "50000"

    async def test_search_never_research_priced(self, x402_on, mock_neo4j, monkeypatch):
        monkeypatch.setattr(x402_service.get_settings(), "enable_agentic_rag", True)
        request = make_request("/api/search", body={"query": "q", "use_agentic": True})
        with pytest.raises(HTTPException) as exc:
            await enforce_x402_payment(request, monetized_auth())
        assert self._challenge_amount(exc) == "50000"


class TestPrePaymentValidation:
    """Requests that would 422 anyway must be rejected BEFORE any payment —
    settle-then-422 would burn the payer's money for nothing."""

    async def test_missing_question_422_before_payment(self, x402_on, facilitator, mock_neo4j):
        request = make_request("/api/ask/stream", body={"use_agentic": True})
        with pytest.raises(HTTPException) as exc:
            await enforce_x402_payment(request, monetized_auth())
        assert exc.value.status_code == 422
        assert "no payment was taken" in exc.value.detail
        facilitator.verify.assert_not_awaited()
        facilitator.settle.assert_not_awaited()

    async def test_missing_query_422_before_payment(self, x402_on, facilitator, mock_neo4j):
        request = make_request("/api/search", body={})
        with pytest.raises(HTTPException) as exc:
            await enforce_x402_payment(request, monetized_auth())
        assert exc.value.status_code == 422
        facilitator.settle.assert_not_awaited()

    async def test_malformed_json_400_before_payment(self, x402_on, facilitator, mock_neo4j):
        request = make_request("/api/search", body="{not json")
        with pytest.raises(HTTPException) as exc:
            await enforce_x402_payment(request, monetized_auth())
        assert exc.value.status_code == 400
        facilitator.settle.assert_not_awaited()

    async def test_free_key_body_untouched(self, mock_neo4j):
        # Free keys bypass the gate entirely — even a bodyless request passes.
        auth = AuthResult(is_authenticated=True, permissions=[APIKeyPermission.READ], key_id="k")
        await enforce_x402_payment(make_request(body={}), auth)  # no exception


# =============================================================================
# Auth hardening: MANAGE strip + endpoint allowlist
# =============================================================================

def priced_key_record(key: str, permissions=None, price="0.05") -> dict:
    return {
        "id": "key_pub1",
        "name": "Public Paid",
        "key_prefix": key[:12],
        "key_hash": hash_api_key(key),
        "permissions": permissions or ["read"],
        "is_active": True,
        "created_at": datetime.utcnow(),
        "last_used_at": datetime.utcnow(),
        "created_by": "admin",
        "collection_scope": "all",
        "allowed_collections": [],
        "allowed_collection_names": [],
        "price_per_query": price,
    }


class TestMonetizedAuthHardening:
    async def test_manage_stripped_from_priced_key(self, mock_neo4j):
        key = "cortex_pub_" + "a" * 64
        mock_neo4j.get_api_key_by_prefix.return_value = [
            priced_key_record(key, permissions=["read", "manage"])
        ]
        auth = await validate_api_key(key)
        assert auth.is_authenticated and auth.is_monetized
        assert APIKeyPermission.MANAGE not in auth.permissions
        assert APIKeyPermission.READ in auth.permissions

    async def test_allowlist_blocks_other_read_endpoints(self, mock_neo4j):
        key = "cortex_pub_" + "b" * 64
        mock_neo4j.get_api_key_by_prefix.return_value = [priced_key_record(key)]
        with pytest.raises(HTTPException) as exc:
            await require_read_permission(make_request(path="/api/documents"), key)
        assert exc.value.status_code == 403

    async def test_allowlist_excludes_stats(self, mock_neo4j):
        # Deliberate product decision: /api/stats is internal data.
        key = "cortex_pub_" + "c" * 64
        mock_neo4j.get_api_key_by_prefix.return_value = [priced_key_record(key)]
        with pytest.raises(HTTPException) as exc:
            await require_read_permission(make_request(path="/api/stats"), key)
        assert exc.value.status_code == 403

    @pytest.mark.parametrize("path", [
        "/api/search", "/api/ask", "/api/ask/stream", "/api/ask/stream/thinking",
    ])
    async def test_allowlist_permits_retrieval(self, mock_neo4j, path):
        key = "cortex_pub_" + "d" * 64
        mock_neo4j.get_api_key_by_prefix.return_value = [priced_key_record(key)]
        auth = await require_read_permission(make_request(path=path), key)
        assert auth.is_monetized

    async def test_free_key_unaffected_by_allowlist(self, mock_neo4j):
        key = "cortex_ro_" + "e" * 64
        record = priced_key_record(key, price=None)
        mock_neo4j.get_api_key_by_prefix.return_value = [record]
        auth = await require_read_permission(make_request(path="/api/documents"), key)
        assert not auth.is_monetized


# =============================================================================
# Endpoint-level: the 402 surface on a real route (no auth bypass)
# =============================================================================

@pytest.fixture
def raw_client(mock_neo4j, mock_processors):
    """TestClient WITHOUT dependency overrides — real auth + payment gate."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestEndpointGate:
    def test_search_returns_402_for_priced_key(self, raw_client, mock_neo4j,
                                               _isolate_env, monkeypatch):
        _isolate_env.x402_enabled = True
        cfg = verified_config()
        monkeypatch.setattr(x402_service, "load_x402_config", lambda force=False: cfg)
        key = "cortex_pub_" + "f" * 64
        mock_neo4j.get_api_key_by_prefix.return_value = [priced_key_record(key)]

        response = raw_client.post(
            "/api/search", json={"query": "hello"}, headers={"X-API-Key": key},
        )
        assert response.status_code == 402
        pr = decode_b64_json(response.headers["PAYMENT-REQUIRED"])
        assert pr["accepts"][0]["amount"] == "50000"

        # A 402 challenge is the normal first leg of the x402 handshake —
        # usage tracking must NOT log it as an error.
        for call in mock_neo4j.record_api_key_usage.call_args_list:
            assert call.kwargs.get("is_error") is not True
            assert not any(a is True for a in call.args)

    def test_documents_403_for_priced_key(self, raw_client, mock_neo4j, _isolate_env):
        _isolate_env.x402_enabled = True
        key = "cortex_pub_" + "g" * 64
        mock_neo4j.get_api_key_by_prefix.return_value = [priced_key_record(key)]
        response = raw_client.get("/api/documents", headers={"X-API-Key": key})
        assert response.status_code == 403


# =============================================================================
# Admin endpoints + key-CRUD guards (client fixture = admin bypass)
# =============================================================================

class TestAdminEndpoints:
    def test_get_config_reports_disabled(self, client, mock_neo4j):
        mock_neo4j.get_x402_config.return_value = None
        response = client.get("/api/admin/x402/config")
        assert response.status_code == 200
        body = response.json()
        assert body["enabled"] is False and body["configured"] is False

    def test_put_config_rejected_while_disabled(self, client, mock_neo4j):
        response = client.put("/api/admin/x402/config", json={
            "pay_to": CHECKSUMMED,
            "facilitator_url": "https://facilitator.example",
            "network": "eip155:84532",
            "asset_address": USDC_BASE_SEPOLIA,
        })
        assert response.status_code == 400

    def test_put_config_saves_and_invalidates_verification(self, client, mock_neo4j,
                                                           _isolate_env):
        _isolate_env.x402_enabled = True
        mock_neo4j.get_x402_config.return_value = None
        response = client.put("/api/admin/x402/config", json={
            "pay_to": CHECKSUMMED,
            "facilitator_url": "https://facilitator.example/",
            "network": "eip155:84532",
            "asset_address": USDC_BASE_SEPOLIA,
        })
        assert response.status_code == 200
        saved_props = mock_neo4j.save_x402_config.call_args.kwargs.get("props") \
            or mock_neo4j.save_x402_config.call_args.args[0]
        assert saved_props["facilitator_url"] == "https://facilitator.example"  # trailing / stripped
        assert saved_props["verified_hash"] is None  # new config = unverified

    def test_verify_requires_complete_config(self, client, mock_neo4j, _isolate_env):
        _isolate_env.x402_enabled = True
        mock_neo4j.get_x402_config.return_value = None
        response = client.post("/api/admin/x402/verify")
        assert response.status_code == 400

    def test_verify_passes_and_stamps(self, client, mock_neo4j, _isolate_env, monkeypatch):
        _isolate_env.x402_enabled = True
        cfg = make_config()
        mock_neo4j.get_x402_config.return_value = cfg
        monkeypatch.setattr(
            FacilitatorClient, "supported",
            AsyncMock(return_value={"kinds": [
                {"x402Version": 2, "scheme": "exact", "network": "eip155:84532"},
            ]}),
        )
        response = client.post("/api/admin/x402/verify")
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is True
        assert all(c["passed"] for c in body["checks"])
        mock_neo4j.mark_x402_verified.assert_called_once_with(config_hash(cfg))

    def test_verify_fails_on_unsupported_network(self, client, mock_neo4j,
                                                 _isolate_env, monkeypatch):
        _isolate_env.x402_enabled = True
        mock_neo4j.get_x402_config.return_value = make_config()
        monkeypatch.setattr(
            FacilitatorClient, "supported",
            AsyncMock(return_value={"kinds": [
                {"x402Version": 2, "scheme": "exact", "network": "eip155:1"},
            ]}),
        )
        response = client.post("/api/admin/x402/verify")
        body = response.json()
        assert body["valid"] is False
        failed = {c["check"] for c in body["checks"] if not c["passed"]}
        assert failed == {"scheme_network_supported"}
        mock_neo4j.mark_x402_verified.assert_not_called()

    def test_earnings_formats_human_units(self, client, mock_neo4j):
        mock_neo4j.get_x402_config.return_value = make_config()
        mock_neo4j.get_x402_earnings.return_value = {
            "payment_count": 3,
            "total_atomic": 150_000,
            "by_key": [{
                "key_id": "key_pub1", "key_name": "Public Paid",
                "payment_count": 3, "total_atomic": 150_000,
            }],
        }
        response = client.get("/api/admin/x402/earnings")
        body = response.json()
        assert body["total_amount"] == "0.15" and body["asset_name"] == "USDC"
        assert body["by_key"][0]["total_amount"] == "0.15"


class TestKeyCrudGuards:
    def _enable_verified(self, _isolate_env, monkeypatch):
        _isolate_env.x402_enabled = True
        cfg = verified_config()
        monkeypatch.setattr(x402_service, "load_x402_config", lambda force=False: cfg)

    def test_create_priced_key_rejected_while_disabled(self, client):
        response = client.post("/api/admin/api-keys", json={
            "name": "Paid", "permissions": ["read"], "price_per_query": "0.05",
        })
        assert response.status_code == 400

    def test_create_priced_key_rejected_when_unverified(self, client, mock_neo4j,
                                                        _isolate_env, monkeypatch):
        _isolate_env.x402_enabled = True
        monkeypatch.setattr(x402_service, "load_x402_config", lambda force=False: make_config())
        response = client.post("/api/admin/api-keys", json={
            "name": "Paid", "permissions": ["read"], "price_per_query": "0.05",
        })
        assert response.status_code == 400
        assert "verified" in response.json()["detail"]

    def test_create_priced_key_with_manage_422(self, client, _isolate_env, monkeypatch):
        self._enable_verified(_isolate_env, monkeypatch)
        response = client.post("/api/admin/api-keys", json={
            "name": "Paid", "permissions": ["read", "manage"], "price_per_query": "0.05",
        })
        assert response.status_code == 422

    def test_create_priced_key_with_bad_price_422(self, client, _isolate_env, monkeypatch):
        self._enable_verified(_isolate_env, monkeypatch)
        response = client.post("/api/admin/api-keys", json={
            "name": "Paid", "permissions": ["read"], "price_per_query": "not-a-number",
        })
        assert response.status_code == 422

    def test_create_priced_key_ok(self, client, mock_neo4j, _isolate_env, monkeypatch):
        self._enable_verified(_isolate_env, monkeypatch)
        mock_neo4j.create_api_key.return_value = {
            "created_at": datetime.utcnow(),
            "allowed_collections": [],
            "price_per_query": "0.05",
        }
        response = client.post("/api/admin/api-keys", json={
            "name": "Paid", "permissions": ["read"], "price_per_query": "0.05",
        })
        assert response.status_code == 200
        body = response.json()
        assert body["key"].startswith("cortex_pub_")
        assert body["price_per_query"] == "0.05"
        assert mock_neo4j.create_api_key.call_args.kwargs["price_per_query"] == "0.05"

    def test_update_cannot_grant_manage_to_priced_key(self, client, mock_neo4j,
                                                      _isolate_env):
        _isolate_env.x402_enabled = True
        mock_neo4j.get_api_key_by_id.return_value = priced_key_record(
            "cortex_pub_" + "h" * 64
        )
        response = client.patch("/api/admin/api-keys/key_pub1", json={
            "permissions": ["read", "manage"],
        })
        assert response.status_code == 422

    def test_update_clear_price_then_manage_ok(self, client, mock_neo4j, _isolate_env):
        _isolate_env.x402_enabled = True
        record = priced_key_record("cortex_pub_" + "i" * 64)
        mock_neo4j.get_api_key_by_id.return_value = record
        cleared = dict(record, price_per_query=None, permissions=["read", "manage"])
        mock_neo4j.update_api_key.return_value = cleared
        response = client.patch("/api/admin/api-keys/key_pub1", json={
            "permissions": ["read", "manage"], "price_per_query": "",
        })
        assert response.status_code == 200
        assert mock_neo4j.update_api_key.call_args.kwargs["clear_price"] is True

    def test_update_add_price_to_manage_key_422(self, client, mock_neo4j,
                                                _isolate_env, monkeypatch):
        self._enable_verified(_isolate_env, monkeypatch)
        record = priced_key_record("cortex_rw_" + "j" * 64, permissions=["read", "manage"], price=None)
        mock_neo4j.get_api_key_by_id.return_value = record
        response = client.patch("/api/admin/api-keys/key_pub1", json={
            "price_per_query": "0.05",
        })
        assert response.status_code == 422

    def test_create_priced_key_stores_default_multiplier(self, client, mock_neo4j,
                                                         _isolate_env, monkeypatch):
        self._enable_verified(_isolate_env, monkeypatch)
        mock_neo4j.create_api_key.return_value = {
            "created_at": datetime.utcnow(), "allowed_collections": [],
            "price_per_query": "0.05", "research_multiplier": "10",
        }
        response = client.post("/api/admin/api-keys", json={
            "name": "Paid", "permissions": ["read"], "price_per_query": "0.05",
        })
        assert response.status_code == 200
        assert mock_neo4j.create_api_key.call_args.kwargs["research_multiplier"] == "10"

    def test_create_priced_key_with_custom_multiplier(self, client, mock_neo4j,
                                                      _isolate_env, monkeypatch):
        self._enable_verified(_isolate_env, monkeypatch)
        mock_neo4j.create_api_key.return_value = {
            "created_at": datetime.utcnow(), "allowed_collections": [],
            "price_per_query": "0.05", "research_multiplier": "0",
        }
        response = client.post("/api/admin/api-keys", json={
            "name": "Paid", "permissions": ["read"],
            "price_per_query": "0.05", "research_multiplier": "0",
        })
        assert response.status_code == 200
        assert mock_neo4j.create_api_key.call_args.kwargs["research_multiplier"] == "0"

    def test_multiplier_without_price_422(self, client, _isolate_env, monkeypatch):
        self._enable_verified(_isolate_env, monkeypatch)
        response = client.post("/api/admin/api-keys", json={
            "name": "Member", "permissions": ["read"], "research_multiplier": "10",
        })
        assert response.status_code == 422

    def test_update_multiplier_on_free_key_422(self, client, mock_neo4j, _isolate_env):
        _isolate_env.x402_enabled = True
        record = priced_key_record("cortex_ro_" + "k" * 64, price=None)
        mock_neo4j.get_api_key_by_id.return_value = record
        response = client.patch("/api/admin/api-keys/key_pub1", json={
            "research_multiplier": "5",
        })
        assert response.status_code == 422

    def test_bad_multiplier_422(self, client, _isolate_env, monkeypatch):
        self._enable_verified(_isolate_env, monkeypatch)
        response = client.post("/api/admin/api-keys", json={
            "name": "Paid", "permissions": ["read"],
            "price_per_query": "0.05", "research_multiplier": "-3",
        })
        assert response.status_code == 422
