"""Deployment contract: the shipped Docker CMDs run uvicorn with
``--proxy-headers`` (trust configurable via ``UVICORN_FORWARDED_ALLOW_IPS``).

The per-IP rate-limit bucket keys on ``request.client.host`` — without proxy
headers that is the reverse proxy's address, so all tenants behind one proxy
would share a single bucket. The shipped stacks only publish the proxy, so
honoring X-Forwarded-For from in-cluster peers is safe by default; operators
who publish the backend directly pin ``UVICORN_FORWARDED_ALLOW_IPS``.
"""

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def test_dockerfiles_enable_proxy_headers():
    for name in ("Dockerfile", "Dockerfile.prod"):
        text = (BACKEND_DIR / name).read_text(encoding="utf-8")
        assert "--proxy-headers" in text, f"{name} lost --proxy-headers"
        assert "--forwarded-allow-ips" in text, f"{name} lost --forwarded-allow-ips"
        assert "UVICORN_FORWARDED_ALLOW_IPS" in text, (
            f"{name} must keep the trust list operator-configurable"
        )
