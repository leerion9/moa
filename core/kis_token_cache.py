"""Persist KIS OAuth access token across processes (main.py + vi_collector)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_log = logging.getLogger("moa")

TOKEN_TTL_HOURS = 23
EXPIRE_BUFFER_MINUTES = 10


@dataclass(frozen=True)
class CachedKISToken:
    access_token: str
    expire_at: datetime
    mode: str
    app_key: str


def _cache_key(mode: str, app_key: str) -> str:
    return f"{mode}:{app_key}"


def load_token_cache(path: Path, *, mode: str, app_key: str) -> Optional[CachedKISToken]:
    if not path.is_file() or not app_key:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _log.warning("KIS token cache read failed: %s", exc)
        return None

    entry = data.get(_cache_key(mode, app_key))
    if not isinstance(entry, dict):
        return None

    token = str(entry.get("access_token", "") or "").strip()
    expire_raw = str(entry.get("expire_at", "") or "").strip()
    if not token or not expire_raw:
        return None

    try:
        expire_at = datetime.fromisoformat(expire_raw)
    except Exception:
        return None

    if entry.get("app_key") != app_key or entry.get("mode") != mode:
        return None

    if not is_token_usable(expire_at):
        return None

    return CachedKISToken(
        access_token=token,
        expire_at=expire_at,
        mode=mode,
        app_key=app_key,
    )


def save_token_cache(
    path: Path,
    *,
    mode: str,
    app_key: str,
    access_token: str,
    expire_at: datetime,
) -> None:
    if not access_token or not app_key:
        return
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}

    data[_cache_key(mode, app_key)] = {
        "mode": mode,
        "app_key": app_key,
        "access_token": access_token,
        "expire_at": expire_at.isoformat(timespec="seconds"),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_token_usable(expire_at: datetime, *, now: datetime | None = None) -> bool:
    ref = now or datetime.now()
    return ref < (expire_at - timedelta(minutes=EXPIRE_BUFFER_MINUTES))


def new_token_expiry(*, now: datetime | None = None) -> datetime:
    return (now or datetime.now()) + timedelta(hours=TOKEN_TTL_HOURS)
