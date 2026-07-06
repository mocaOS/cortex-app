"""Free-disk guard: 507 when the uploads filesystem is nearly full."""

import shutil
from collections import namedtuple

import pytest
from fastapi import HTTPException

from app import main as app_main
from app.config import get_settings

Usage = namedtuple("Usage", "total used free")

GB = 1024 * 1024 * 1024
MB = 1024 * 1024


def _fake_disk(monkeypatch, free_bytes):
    monkeypatch.setattr(
        shutil, "disk_usage", lambda path: Usage(total=100 * GB, used=0, free=free_bytes)
    )


class TestEnsureDiskSpace:
    def test_plenty_of_space_passes(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "min_free_disk_mb", 500)
        _fake_disk(monkeypatch, 10 * GB)
        app_main._ensure_disk_space(50 * MB)  # must not raise

    def test_below_floor_rejected_with_507(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "min_free_disk_mb", 500)
        _fake_disk(monkeypatch, 400 * MB)
        with pytest.raises(HTTPException) as exc:
            app_main._ensure_disk_space()
        assert exc.value.status_code == 507

    def test_incoming_size_counts_against_headroom(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "min_free_disk_mb", 500)
        _fake_disk(monkeypatch, 600 * MB)
        app_main._ensure_disk_space(50 * MB)  # 600 > 500 + 50 → ok
        with pytest.raises(HTTPException) as exc:
            app_main._ensure_disk_space(200 * MB)  # 600 < 500 + 200 → 507
        assert exc.value.status_code == 507

    def test_zero_disables_guard(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "min_free_disk_mb", 0)
        _fake_disk(monkeypatch, 1)
        app_main._ensure_disk_space(10 * GB)  # must not raise

    def test_stat_failure_never_blocks(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "min_free_disk_mb", 500)

        def boom(path):
            raise OSError("no such filesystem")

        monkeypatch.setattr(shutil, "disk_usage", boom)
        app_main._ensure_disk_space(50 * MB)  # must not raise


class TestImportSessionGuard:
    def test_import_start_rejected_when_disk_low(self, client, monkeypatch):
        monkeypatch.setattr(get_settings(), "min_free_disk_mb", 500)
        _fake_disk(monkeypatch, 400 * MB)
        resp = client.post(
            "/api/admin/import/upload/start",
            json={"total_size": 10 * MB, "filename": "lib.zip"},
        )
        assert resp.status_code == 507
