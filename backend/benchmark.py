"""Reproducible local index/plan benchmark, not an AWS throughput claim."""
import json
import tempfile
import time
from pathlib import Path

from backend.service import Store


def main():
    with tempfile.TemporaryDirectory() as directory:
        started = time.perf_counter()
        store = Store(str(Path(directory) / "benchmark.sqlite3"), 10000)
        indexed = time.perf_counter()
        groups = store.incidents({})
        grouped = time.perf_counter()
        with store.transaction():
            plan = store.create_plan({"name": "benchmark-operator", "role": "Operator"}, {"rate": 100})
        planned = time.perf_counter()
        results = {"mode": "local SQLite and synthetic verifier; no AWS calls", "messages": 10000,
                   "indexSeconds": round(indexed - started, 4), "groupSeconds": round(grouped - indexed, 4),
                   "planSeconds": round(planned - grouped, 4), "groups": len(groups),
                   "decisions": plan["counts"], "sends": store.read("SELECT count(*) n FROM deliveries")[0]["n"]}
        assert sum(plan["counts"].values()) == 10000
        assert results["sends"] == 0
        store.db.close()
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
