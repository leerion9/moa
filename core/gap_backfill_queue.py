"""Queue/state management for gradual gap strategy historical backfill."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from core.gap_collector_logic import GapCandidate, find_gap_candidate


def task_key(symbol: str, ymd: str) -> str:
    return f"{symbol}:{ymd}"


def parse_task_key(key: str) -> Tuple[str, str]:
    sym, ymd = str(key).split(":", maxsplit=1)
    return sym, ymd


@dataclass(frozen=True)
class BackfillTask:
    symbol: str
    ymd: str
    open_price: int
    prev_close: int
    gap_pct: float
    close_price: int

    @property
    def key(self) -> str:
        return task_key(self.symbol, self.ymd)


def trading_days_in_year(year: int, holidays: FrozenSet[str]) -> List[str]:
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    out: List[str] = []
    current = start
    while current <= end:
        ymd = current.strftime("%Y%m%d")
        if current.weekday() < 5 and ymd not in holidays:
            out.append(ymd)
        current += timedelta(days=1)
    return out


def plan_tasks_for_year(
    cache_bars: Dict[str, List[Dict[str, object]]],
    year: int,
    holidays: FrozenSet[str],
    *,
    gap_min_pct: float,
    gap_max_pct: float,
    skip_keys: Set[str],
) -> List[BackfillTask]:
    """Scan history_cache daily bars; no HTTP."""
    tasks: List[BackfillTask] = []
    for ymd in trading_days_in_year(year, holidays):
        for symbol, bars in sorted(cache_bars.items()):
            if not bars:
                continue
            key = task_key(symbol, ymd)
            if key in skip_keys:
                continue
            tagged = [{**b, "symbol": symbol} for b in bars]
            cand = find_gap_candidate(
                tagged,
                ymd,
                gap_min_pct=gap_min_pct,
                gap_max_pct=gap_max_pct,
            )
            if cand is None:
                continue
            tasks.append(
                BackfillTask(
                    symbol=symbol,
                    ymd=ymd,
                    open_price=cand.open_price,
                    prev_close=cand.prev_close,
                    gap_pct=cand.gap_pct,
                    close_price=cand.close_price,
                )
            )
    return tasks


def queue_path(base_dir: Path, year: int) -> Path:
    return base_dir / f"queue_{year}.json"


def state_path(base_dir: Path) -> Path:
    return base_dir / "state.json"


def _load_json(path: Path) -> object:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_state(base_dir: Path) -> Dict[str, object]:
    raw = _load_json(state_path(base_dir))
    if not isinstance(raw, dict):
        return {"done": [], "failed": {}}
    done = raw.get("done", [])
    failed = raw.get("failed", {})
    if not isinstance(done, list):
        done = []
    if not isinstance(failed, dict):
        failed = {}
    return {"done": done, "failed": failed}


def save_state(base_dir: Path, state: Dict[str, object]) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    state_path(base_dir).write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def done_keys(base_dir: Path) -> Set[str]:
    state = load_state(base_dir)
    done = state.get("done", [])
    if not isinstance(done, list):
        return set()
    return {str(k) for k in done}


def mark_done(base_dir: Path, key: str) -> None:
    state = load_state(base_dir)
    done = state.get("done", [])
    if not isinstance(done, list):
        done = []
    if key not in done:
        done.append(key)
    state["done"] = done
    save_state(base_dir, state)


def mark_failed(base_dir: Path, key: str, reason: str) -> None:
    state = load_state(base_dir)
    failed = state.get("failed", {})
    if not isinstance(failed, dict):
        failed = {}
    failed[key] = reason
    state["failed"] = failed
    save_state(base_dir, state)


def save_queue(base_dir: Path, year: int, tasks: Iterable[BackfillTask]) -> int:
    base_dir.mkdir(parents=True, exist_ok=True)
    payload = [asdict(t) for t in tasks]
    queue_path(base_dir, year).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return len(payload)


def load_queue(base_dir: Path, year: int) -> List[BackfillTask]:
    raw = _load_json(queue_path(base_dir, year))
    if not isinstance(raw, list):
        return []
    out: List[BackfillTask] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            out.append(
                BackfillTask(
                    symbol=str(row["symbol"]),
                    ymd=str(row["ymd"]),
                    open_price=int(row["open_price"]),
                    prev_close=int(row["prev_close"]),
                    gap_pct=float(row["gap_pct"]),
                    close_price=int(row["close_price"]),
                )
            )
        except Exception:
            continue
    return out


def filter_pending_tasks(
    tasks: List[BackfillTask],
    *,
    skip_keys: Set[str],
) -> List[BackfillTask]:
    return [t for t in tasks if t.key not in skip_keys]


def pop_tasks(tasks: List[BackfillTask], limit: int) -> Tuple[List[BackfillTask], List[BackfillTask]]:
    if limit <= 0:
        return [], tasks
    batch = tasks[:limit]
    rest = tasks[limit:]
    return batch, rest


def task_to_candidate(task: BackfillTask) -> GapCandidate:
    return GapCandidate(
        symbol=task.symbol,
        open_price=task.open_price,
        prev_close=task.prev_close,
        gap_pct=task.gap_pct,
        close_price=task.close_price,
    )


def queue_stats(base_dir: Path, year: int) -> Dict[str, int]:
    tasks = load_queue(base_dir, year)
    done = done_keys(base_dir)
    pending = [t for t in tasks if t.key not in done]
    return {
        "queued": len(tasks),
        "done": len([t for t in tasks if t.key in done]),
        "pending": len(pending),
    }
