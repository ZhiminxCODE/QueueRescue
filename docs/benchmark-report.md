# Local benchmark report

Measured 2026-11-16 on Apple M3 Pro (11 logical CPUs, 18 GiB RAM), macOS arm64, Python 3.10.0 / SQLite 3.35.5, Node 24.13.0 and Chrome 157. The production dashboard used a 1512 × 771 viewport. These are synthetic local SQLite/verifier results. No AWS deployment, SQS throughput, recovery send rate or billing was measured.

## Method

Backend: five sequential trials, each with a fresh temporary database and 10,000 synthetic events. Timings use a monotonic clock and include schema/seed work in indexing. Grouping, overview, first-page selection/classification, dry-run creation and serialization are measured separately. The page phase covers local store work, not full HTTP handling. OS caches may be warm; no cache flush or CPU/network throttling was applied.

Browser: the optional fixture loads the normal production assets and drives five scripted DOM action cycles. It waits for a matching API resource to finish, the expected visible result, and two animation frames. This avoids counting an immediate pagination label or an empty panel as loaded data. Search uses a single complete input value, including the app's 180 ms debounce. It is a local action-to-ready proxy, not native typing/click latency, INP, LCP, a cold-cache benchmark or an accessibility audit. Resource timing and Long Tasks are supplemental observations. The page must stay visible. The fixture adds controls/results around the same application; it does not access private React state or authentication tokens.

All summaries use nearest-rank p95. With only five samples, p95 equals the maximum and is not a statistically stable production tail estimate. Backend trials use independent databases; browser cycles share a database and retain one additional unapproved 10,000-decision plan per cycle. They are not independent identical workloads. Preliminary fixture-debugging runs are excluded.

## Backend results

| Phase | Median ms | Minimum ms | p95 / maximum ms |
| --- | ---: | ---: | ---: |
| Fresh database + 10,000-event index | 142.75 | 138.71 | 255.67 |
| Incident grouping | 92.48 | 91.38 | 96.49 |
| Overview | 92.93 | 92.47 | 109.94 |
| First-page selection/classification | 26.47 | 25.52 | 27.23 |
| Dry-run planning | 110.54 | 108.36 | 118.35 |
| Plan JSON serialization | 9.55 | 8.89 | 9.88 |

Every trial produced three groups and reconciled 7,500 eligible + 140 skipped + 740 review + 1,620 blocked decisions = 10,000. Each retained all originals with zero jobs and zero deliveries. One serialized plan was approximately 2.40 MB (decimal, uncompressed).

## Browser results

| Action | Median ms | Minimum ms | p95 / maximum ms |
| --- | ---: | ---: | ---: |
| Open inbox with 25 of 10,000 rows | 258.3 | 249.7 | 266.4 |
| Next page, after fresh response | 267.3 | 257.9 | 274.2 |
| Search for one message | 230.2 | 225.0 | 233.3 |
| Inspect masked event | 33.1 | 32.7 | 65.0 |
| Clear search | 268.1 | 258.8 | 285.9 |
| Open saved plans | 105.5 | 50.1 | 182.5 |
| Generate 10,000-event dry run | 325.0 | 259.0 | 365.8 |

One initial application-ready observation was 935.7 ms from navigation start. This checks authenticated local summary/navigation readiness, not the inbox's completed data load. Browser resource timing recorded 48 API requests with a longest duration of 192.6 ms. The supported Long Tasks observer reported no tasks of at least 50 ms during its buffered observation window; that does not prove absence of smaller stalls or guarantee responsiveness on another device.

An independent database check confirmed 10,000 original messages retained, exactly five unapproved READY plans, zero recovery jobs and zero deliveries. No plan was approved or executed. These results support a bounded local demonstration, not an AWS performance claim or production capacity target.

## Findings and limits

- Inbox actions include the existing 180 ms debounce. Pagination also uses this delay; separating immediate page navigation from debounced search is a possible local UX improvement.
- Saved-plan latency increased from 50.1 ms to 182.5 ms as the list grew from zero to four existing full plans. The local list endpoint returns complete decision documents; roughly 2.40 MB per plan makes response size grow with plan history. Returning summaries with a separate selected-plan fetch is the next performance improvement. This observation does not establish the sole cause of the timing change.
- The UI renders 25 message rows and an eight-decision plan sample. Bounded DOM size does not make backend grouping or list serialization constant-time. This test did not cover 100 saved plans, concurrent users, replay execution, a slow device or remote network.
- AWS plans remain limited to 25 snapshots. A cloud load test needs a separate cost-reviewed workload; local SQLite timings cannot be converted into AWS service throughput or charges.

## Reproduce

Install the pinned project dependencies using the README setup, then run the backend measurements:

```sh
mkdir -p .data
.venv/bin/python -m backend.benchmark --runs 5 --messages 10000 > .data/benchmark-backend.json
```

For the browser fixture, explicitly select a local build even if an ignored AWS environment file exists. Use a **new database filename** for every measured run; the fixture expects no saved plans. Choose an available loopback port.

```sh
VITE_QR_MODE=local npm run build
python3 tools/prepare_browser_benchmark.py
.venv/bin/python -m backend.server --port 8766 --db .data/benchmark-browser-new.sqlite3 --seed 10000 --consumer-disabled
```

Open `http://localhost:8766/browser-benchmark.html#overview` in Chrome and click **Run five local cycles**. Keep it visible until complete. The fixture refuses non-loopback hosts and an AWS-mode connection manifest. Expand **Raw benchmark JSON** to copy the results into ignored `.data/`. Record the browser version, viewport and any failed/missing measurements. Stop the server after inspection; a normal frontend rebuild removes the optional fixture from `dist/`.

Verify the local database independently after the run:

```sh
.venv/bin/python - <<'PY'
import sqlite3
with sqlite3.connect('.data/benchmark-browser-new.sqlite3') as db:
    for sql in (
        'SELECT count(*), sum(original_retained) FROM messages',
        "SELECT count(*) FROM plans WHERE state='READY' AND approved_by IS NULL",
        'SELECT count(*) FROM jobs',
        'SELECT count(*) FROM deliveries',
    ):
        print(db.execute(sql).fetchone())
PY
```

Expected: `(10000, 10000)`, `(5,)`, `(0,)`, `(0,)`. Planning is not replay; no approval, recovery job or send is part of this measurement. Raw JSON, databases, screenshots and build output remain ignored. The final offline security suite passed 170 backend tests, 26 frontend tests and 12 mocked Terraform plans; the benchmark run itself checks its count and no-send invariants.
