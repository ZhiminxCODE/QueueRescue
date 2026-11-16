"""Repeated local SQLite measurements; never an AWS throughput/cost claim."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
import platform
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path

from backend.service import Store, canonical


def summarize(values):
    ordered = sorted(values)
    return {"samples": len(values), "min": round(ordered[0], 6),
            "median": round(statistics.median(values), 6),
            "p95": round(ordered[math.ceil(.95 * len(ordered)) - 1], 6),
            "max": round(ordered[-1], 6)}


def run_once(messages):
    with tempfile.TemporaryDirectory() as directory:
        timings = {}
        started = time.perf_counter()
        store = Store(str(Path(directory) / "benchmark.sqlite3"), messages)
        timings['indexSeconds'] = time.perf_counter() - started
        try:
            started = time.perf_counter()
            groups = store.incidents({})
            timings['groupSeconds'] = time.perf_counter() - started
            started = time.perf_counter()
            overview = store.summary()
            timings['overviewSeconds'] = time.perf_counter() - started
            started = time.perf_counter()
            selected = store.selection({})
            page = [{"id": row['id'], "decision": store.classify(row)[0]} for row in selected[:25]]
            timings['firstPageSeconds'] = time.perf_counter() - started
            started = time.perf_counter()
            with store.transaction():
                plan = store.create_plan({"name": "benchmark-operator", "role": "Operator"}, {"rate": 100})
            timings['planSeconds'] = time.perf_counter() - started
            started = time.perf_counter()
            plan_bytes = len(canonical(plan).encode())
            timings['planSerializeSeconds'] = time.perf_counter() - started
            sends = store.read("SELECT count(*) n FROM deliveries")[0]["n"]
            jobs = store.read("SELECT count(*) n FROM jobs")[0]["n"]
            originals = store.read("SELECT count(*) n FROM messages WHERE original_retained=1")[0]["n"]
            if sum(plan['counts'].values()) != messages or overview['total'] != messages or originals != messages or sends or jobs:
                raise RuntimeError('Benchmark violated its dry-run/count invariant')
            return {**timings, "groups": len(groups), "pageRows": len(page), "decisions": plan['counts'],
                    "planBytes": plan_bytes, "sends": sends, "jobs": jobs, "originalsRetained": originals}
        finally:
            store.db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--messages', type=int, default=10000)
    parser.add_argument('--runs', type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.messages <= 100000 or not 1 <= args.runs <= 30:
        parser.error('Use 1–100000 messages and 1–30 runs')
    trials = [run_once(args.messages) for _ in range(args.runs)]
    print(json.dumps({"mode": "local SQLite and synthetic verifier; no AWS calls", "messages": args.messages,
        "measuredAt": datetime.now(timezone.utc).isoformat(),
        "environment": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                        "os": platform.system(), "architecture": platform.machine(), "logicalCpus": os.cpu_count()},
        "method": "Fresh temporary database each run; sequential phases; warm OS cache allowed; p95 uses nearest rank",
        "summary": {key: summarize([trial[key] for trial in trials]) for key in trials[0] if key.endswith('Seconds')},
        "trials": trials}, indent=2))


if __name__ == '__main__':
    main()
