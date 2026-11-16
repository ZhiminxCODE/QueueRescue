"""Compare legacy full lists with summaries on identical local synthetic plans."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import time

from backend.benchmark import summarize
from backend.service import Store, canonical


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plans', type=int, default=5)
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--messages', type=int, default=10000)
    args = parser.parse_args()
    if not 1 <= args.plans <= 100 or not 1 <= args.runs <= 30 or not 1 <= args.messages <= 100000:
        parser.error('Use 1–100 plans, 1–30 runs and 1–100000 messages')
    with tempfile.TemporaryDirectory() as directory:
        store = Store(str(Path(directory) / 'plans.sqlite3'), args.messages)
        try:
            for _ in range(args.plans):
                with store.transaction():
                    store.create_plan({'name': 'benchmark-operator', 'role': 'Operator'}, {})
            identifiers = [row['id'] for row in store.read('SELECT id FROM plans ORDER BY created_at DESC,id DESC LIMIT 100')]
            timings = {'legacyFullListSeconds': [], 'summaryListSeconds': [], 'selectedDetailSeconds': []}
            sizes = {}
            for _ in range(args.runs):
                for name, load in (
                    ('legacyFullList', lambda: {'items': [store.plan(identifier) for identifier in identifiers]}),
                    ('summaryList', lambda: {'items': store.list_plans()}),
                    ('selectedDetail', lambda: store.plan(identifiers[0])),
                ):
                    start = time.perf_counter()
                    payload = canonical(load()).encode()
                    timings[name + 'Seconds'].append(time.perf_counter() - start)
                    sizes[name + 'Bytes'] = len(payload)
            originals = store.read('SELECT count(*) n FROM messages WHERE original_retained=1')[0]['n']
            sends = store.read('SELECT count(*) n FROM deliveries')[0]['n']
            jobs = store.read('SELECT count(*) n FROM jobs')[0]['n']
            if originals != args.messages or sends or jobs or len(store.list_plans()) != args.plans:
                raise RuntimeError('Plan benchmark violated its count/no-send invariant')
            print(json.dumps({'mode': 'local Store + JSON serialization; no HTTP, browser or AWS',
                'measuredAt': datetime.now(timezone.utc).isoformat(), 'plans': args.plans, 'messagesPerPlan': args.messages,
                'method': 'Same fixture; sequential legacy list, summary list and one selected detail; warm cache allowed',
                'summary': {key: summarize(values) for key, values in timings.items()}, 'sizes': sizes,
                'originalsRetained': originals, 'jobs': jobs, 'sends': sends}, indent=2))
        finally:
            store.db.close()


if __name__ == '__main__':
    main()
