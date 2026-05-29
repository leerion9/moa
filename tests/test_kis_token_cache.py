"""Tests for KIS token file cache."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from core.kis_token_cache import (
    is_token_usable,
    load_token_cache,
    new_token_expiry,
    save_token_cache,
)


def test_save_and_load_token_cache(tmp_path: Path):
    path = tmp_path / "kis_token_cache.json"
    expire = datetime.now() + timedelta(hours=20)
    save_token_cache(
        path,
        mode="live",
        app_key="APP123",
        access_token="tok-abc",
        expire_at=expire,
    )
    loaded = load_token_cache(path, mode="live", app_key="APP123")
    assert loaded is not None
    assert loaded.access_token == "tok-abc"
    assert loaded.expire_at.replace(microsecond=0) == expire.replace(microsecond=0)


def test_load_rejects_expired_token(tmp_path: Path):
    path = tmp_path / "kis_token_cache.json"
    expire = datetime.now() - timedelta(minutes=1)
    save_token_cache(
        path,
        mode="live",
        app_key="APP123",
        access_token="tok-old",
        expire_at=expire,
    )
    assert load_token_cache(path, mode="live", app_key="APP123") is None


def test_load_rejects_mode_or_app_key_mismatch(tmp_path: Path):
    path = tmp_path / "kis_token_cache.json"
    expire = datetime.now() + timedelta(hours=10)
    save_token_cache(
        path,
        mode="live",
        app_key="APP123",
        access_token="tok-abc",
        expire_at=expire,
    )
    assert load_token_cache(path, mode="paper", app_key="APP123") is None
    assert load_token_cache(path, mode="live", app_key="OTHER") is None


def test_is_token_usable_buffer():
    now = datetime(2026, 5, 28, 15, 0, 0)
    assert is_token_usable(now + timedelta(minutes=30), now=now) is True
    assert is_token_usable(now + timedelta(minutes=5), now=now) is False


def test_new_token_expiry():
    now = datetime(2026, 5, 28, 8, 30, 0)
    exp = new_token_expiry(now=now)
    assert exp == now + timedelta(hours=23)
