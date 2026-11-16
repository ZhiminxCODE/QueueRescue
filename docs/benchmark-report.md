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
- In the original run, saved-plan latency increased from 50.1 ms to 182.5 ms as the list grew from zero to four existing full plans. Its list endpoint returned complete decision documents; roughly 2.40 MB per plan made response size grow with history. The follow-up below replaces that behavior. This observation does not establish the sole cause of the timing change.
- The UI renders 25 message rows and an eight-decision plan sample. Bounded DOM size does not make backend grouping or list serialization constant-time. This test did not cover 100 saved plans, concurrent users, replay execution, a slow device or remote network.
- AWS plans remain limited to 25 snapshots. A cloud load test needs a separate cost-reviewed workload; local SQLite timings cannot be converted into AWS service throughput or charges.

## Saved-plan loading follow-up

The local simulator now returns compact list summaries and fetches one full plan when selected. A separate SQLite table stores only counts, selection size, filters and rate. List state/expiry come from the authoritative plan row. Startup backfills missing summaries for legacy databases once, without changing immutable documents, hashes, approvals or audit records. The first startup of a large legacy database can take longer. Lists remain capped at the newest 100 plans, ordered by creation time and ID. This change does not modify the AWS catalog/API.

On 2026-11-16, `python -m backend.benchmark_plans --plans 5 --messages 10000 --runs 5` measured the equivalent legacy list and new summary list on the same five-plan fixture. It includes loading and JSON serialization, with warm caches allowed and phases run in a fixed order. Legacy IDs are prepared before timing. This is local store work, not HTTP or browser latency.

| Operation | Uncompressed JSON bytes | Median ms | p95 / maximum ms |
| --- | ---: | ---: | ---: |
| Equivalent legacy list, all five full plans | 11,992,525 | 79.12 | 83.03 |
| New list, five summaries | 861 | 2.05 | 2.76 |
| One selected full plan | 2,398,497 | 14.84 | 15.43 |

An actual authenticated HTTP check independently returned an 861-byte five-plan list with no decisions and a 2,398,496-byte selected detail containing all 10,000 decisions. The fixtures have different plan IDs/timestamps, so full-document sizes differ slightly. Full documents remain available for review and export; summaries are never used as approval or execution authority.

Five production-browser cycles were repeated on a fresh 10,000-event database, on the same machine/browser at 1512 × 827. The original viewport was 1512 × 771, so these small samples are indicative observations rather than a controlled speedup estimate. Both runs include an empty first list and four growing nonempty histories.

| Action | Original median ms | Follow-up median ms | Original max ms | Follow-up max ms |
| --- | ---: | ---: | ---: | ---: |
| Open saved plans, including selected detail | 105.5 | 99.8 | 182.5 | 106.1 |
| Generate dry run and display full detail | 325.0 | 258.3 | 365.8 | 283.1 |

The nonempty list now requires a summary request followed by a selected-detail request. Creating a plan still returns its full POST response and then refreshes the selected detail; this keeps existing mutation responses intact but adds a read. Single-plan transfer size remains about 2.40 MB. Further optimization could reuse the create response or page decision details; the list no longer transfers every saved decision document.

The follow-up recorded 56 API resource entries (maximum duration 177.8 ms), an 817 ms initial application-ready observation and one 50 ms Long Task. Independent database inspection again found 10,000 retained originals, five unapproved READY plans, zero jobs and zero deliveries. No approvals or replay were performed by the benchmark.

Verification passed 175 backend tests, 29 frontend tests and the production build. Tests cover compact authenticated lists, complete detail, live expiry/state, deterministic bounds, legacy backfill and repeat startup, and rejection of approval based on altered presentation metadata. Browser checks with a disposable local fixture verified a one-time detail failure, retry, hidden actions while loading, and a delayed old response failing to replace the current selection. Raw results, screenshots and fixture data remain ignored.

## Reproduce

Install the pinned project dependencies using the README setup, then run the backend measurements:

```sh
mkdir -p .data
.venv/bin/python -m backend.benchmark --runs 5 --messages 10000 > .data/benchmark-backend.json
.venv/bin/python -m backend.benchmark_plans --plans 5 --runs 5 --messages 10000 > .data/benchmark-plans.json
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
