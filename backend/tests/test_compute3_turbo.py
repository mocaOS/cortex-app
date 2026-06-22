"""Regression tests for the Compute3 / Turbo Mode readiness path.

These lock the fix for the latent bug where the vLLM-readiness branch called a
non-existent ``Compute3Job.get_auth_token()`` and passed a 4th argument to
``set_turbo_mode_state`` (which accepts only 3). Both would crash the moment the
``wait_for_ready=True`` branch was ever exercised. See defect D-002.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import pytest

from app.services import llm_config
from app.services.compute3_service import Compute3Job, Compute3Service


def test_compute3job_has_no_get_auth_token_attr():
    """The token is fetched via the async Service.get_job_token(job_id), not a
    job method. Guards against re-introducing job.get_auth_token()."""
    job = Compute3Job({"job_id": "j1", "hostname": "h", "state": "running"})
    assert not hasattr(job, "get_auth_token")


def test_set_turbo_mode_state_arity_is_three():
    """set_turbo_mode_state must keep its 3-arg shape (active, base_url, api_key);
    the readiness branch relies on it."""
    params = list(inspect.signature(llm_config.set_turbo_mode_state).parameters)
    assert params == ["active", "base_url", "api_key"]


async def test_wait_for_vllm_ready_uses_async_job_token(monkeypatch):
    """The readiness loop must await Service.get_job_token for auth'd jobs and
    succeed without AttributeError (previously called job.get_auth_token())."""
    svc = Compute3Service()

    running_job = Compute3Job(
        {"job_id": "j1", "hostname": "host.compute3", "state": "running", "auth": True}
    )

    monkeypatch.setattr(svc, "get_job", AsyncMock(return_value=running_job))
    token_mock = AsyncMock(return_value="jwt-token")
    monkeypatch.setattr(svc, "get_job_token", token_mock)
    health_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(svc, "check_vllm_health", health_mock)

    ready = await svc.wait_for_vllm_ready(running_job, timeout=5, interval=1)

    assert ready is True
    token_mock.assert_awaited_once_with("j1")
    # health check received the fetched token, not a bound-method object
    health_mock.assert_awaited_once_with("https://host.compute3/v1", "jwt-token")


async def test_wait_for_vllm_ready_skips_token_when_no_auth(monkeypatch):
    """Jobs without auth must not call get_job_token (token stays None)."""
    svc = Compute3Service()
    job = Compute3Job(
        {"job_id": "j2", "hostname": "h2", "state": "running", "auth": False}
    )
    monkeypatch.setattr(svc, "get_job", AsyncMock(return_value=job))
    token_mock = AsyncMock(return_value="should-not-be-used")
    monkeypatch.setattr(svc, "get_job_token", token_mock)
    health_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(svc, "check_vllm_health", health_mock)

    ready = await svc.wait_for_vllm_ready(job, timeout=5, interval=1)

    assert ready is True
    token_mock.assert_not_awaited()
    health_mock.assert_awaited_once_with("https://h2/v1", None)
