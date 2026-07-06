"""retry_on_transient: retries transient driver errors, passes through the rest."""

import pytest
from neo4j.exceptions import ServiceUnavailable, TransientError

from app.services import neo4j_service as svc


class _Fake:
    def __init__(self, failures, exc):
        self.calls = 0
        self._failures = failures
        self._exc = exc

    @svc.retry_on_transient
    def op(self):
        self.calls += 1
        if self.calls <= self._failures:
            raise self._exc
        return "ok"


def test_recovers_after_transient_failures(monkeypatch):
    monkeypatch.setattr(svc.time, "sleep", lambda s: None)
    fake = _Fake(failures=2, exc=ServiceUnavailable("neo4j restarting"))
    assert fake.op() == "ok"
    assert fake.calls == 3


def test_gives_up_after_three_attempts(monkeypatch):
    monkeypatch.setattr(svc.time, "sleep", lambda s: None)
    fake = _Fake(failures=99, exc=TransientError("deadlock"))
    with pytest.raises(TransientError):
        fake.op()
    assert fake.calls == 3


def test_non_transient_errors_pass_through(monkeypatch):
    monkeypatch.setattr(svc.time, "sleep", lambda s: None)
    fake = _Fake(failures=99, exc=ValueError("bad input"))
    with pytest.raises(ValueError):
        fake.op()
    assert fake.calls == 1
