"""Run gap_backfill execute batches until pending in range is zero."""
from __future__ import annotations

import subprocess
import sys
import time

YEAR = 2026
FROM_YMD = "20260101"
TO_YMD = "20260605"
LIMIT = 50
MAX_ROUNDS = 500


def pending_count() -> int:
    code = (
        "from config.settings import settings;"
        "from core.gap_backfill_queue import load_queue, done_keys, filter_pending_tasks, in_date_range, task_key;"
        "from core.gap_result_xlsx import read_existing_trade_keys;"
        f"queue=load_queue(settings.gap_backfill_dir, {YEAR});"
        "skip=set(done_keys(settings.gap_backfill_dir));"
        "skip.update(task_key(s,d) for s,d in read_existing_trade_keys(settings.gap_backfill_xlsx_path));"
        f"pending=[t for t in filter_pending_tasks(queue, skip_keys=skip) if in_date_range(t.ymd, from_ymd='{FROM_YMD}', to_ymd='{TO_YMD}')];"
        "print(len(pending))"
    )
    out = subprocess.check_output([sys.executable, "-c", code], text=True)
    return int(out.strip())


def main() -> int:
    for round_idx in range(1, MAX_ROUNDS + 1):
        pending = pending_count()
        print(f"[round {round_idx}] pending={pending}", flush=True)
        if pending <= 0:
            print("DONE", flush=True)
            return 0
        rc = subprocess.call(
            [
                sys.executable,
                "-m",
                "scripts.gap_backfill",
                "run",
                "--year",
                str(YEAR),
                "--from",
                FROM_YMD,
                "--to",
                TO_YMD,
                "--limit",
                str(LIMIT),
                "--execute",
            ]
        )
        if rc != 0:
            print(f"batch failed rc={rc}", flush=True)
            return rc
        time.sleep(1.0)
    print("MAX_ROUNDS reached", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
