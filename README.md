# QueueRescue prototype

A working local implementation of the supplied technical prototype: investigate failed shipment events, create a selective recovery plan, obtain separate approval, replay at a bounded rate, and verify business outcomes.

**This release runs entirely in simulation.** It does not connect to AWS, deploy infrastructure, receive SQS messages, or delete source messages. It implements the recovery workflow with a React dashboard, Python API, SQLite ledger, and simulated idempotent shipment service. Full AWS deployment and the broader system design remain future work.

## Run

Requires Node.js 22.12+ (or Node 24) and Python 3.10+. The backend has no external Python dependencies.

```sh
npm ci
npm run build
npm start
```

Open **http://localhost:8000**. The server binds to loopback only. First launch seeds 1,000 synthetic events into `.data/queuerescue.sqlite3`; changes survive restarts. Stop with Ctrl+C.

For frontend development, run `npm start` and `npm run dev` in separate terminals. Open http://localhost:5173; Vite proxies `/api` to port 8000. After editing the frontend, rebuild to update the version served on port 8000.

To use a separate dataset or port:

```sh
python3 -m backend.server --db .data/demo-10k.sqlite3 --seed 10000 --port 8001
```

The seed option applies only to an empty database. To restart the walkthrough with fresh data, stop the server and choose a new `--db` path.

## Demo walkthrough

1. Open **Failure inbox**. Filter by error, age, operation ID, or business outcome. Inspect an event for a masked payload, original identity, checksum, attributes, and simulated failure evidence.
2. As **Operator / Alex Chen**, open **Replay plans → New dry run**. Select all groups or a filter, and set a rate from 1–100 messages/sec. No sends occur during planning.
3. Review the candidates, completed-operation skips, blocked schemas, and manual-review exclusions. Export the complete dry run if desired.
4. Switch the top-right demo identity to **Approver / Morgan Lee**. Add a note and approve the exact plan hash. The API rejects self-approval, changed hashes, and expired plans.
5. Switch back to **Operator**. Optionally enable **Simulate interruption after first send**, then start the approved recovery.
6. In **Recovery jobs**, observe the persisted ledger. An injected interruption produces **PAUSED / UNCERTAIN**. Click **Reconcile outcomes**, then **Resume**. The uncertain message is not resent.
7. When sending reaches **VERIFYING**, click **Verify outcomes**. Only confirmed business completions count as recoveries. Export the incident report and inspect **Audit trail**.

To quickly test one eligible event, use operation `shipment:ORD-12345` in a new dry run. The default dataset has 824 warehouse timeouts, 162 invalid-schema events, and 14 already-shipped events. Initially 750 are eligible, 74 require review, 162 are blocked, and 14 are skipped.

## Implemented safeguards

- Server-side capabilities for Viewer, Operator, Approver, and Admin; separate creator and approver identities.
- Allowlisted standard source/destination queues; alternate destinations and FIFO are rejected.
- Immutable versioned plans, SHA-256 hashes, one-hour approval/execution windows, schema validation, and immediate pre-send business verification.
- Stable business `operationId` plus per-send replay job/attempt attributes; original payload bytes and attributes preserved in simulated deliveries.
- Transactions reserve operations across overlapping jobs. No automatic retry of an uncertain send. Unresolved operations remain reserved for manual investigation.
- Persisted attempt intent and lease recorded before delivery. Transport delivery and acknowledgment use separate commits to exercise the real ambiguity boundary.
- Rate-limited scheduling, pause/resume/cancel, restart detection, business verification, and reports. Cancellation leaves already-sent events available for verification.
- Consumer-owned unique shipment identity, default sensitive-field masking, and append-only audit triggers with a verifiable hash chain.
- All domain mutations require an `Idempotency-Key`. Retrying the same actor/key/body returns the original response; a changed request receives HTTP 409.
- Original indexed messages are retained throughout. This prototype has no delete/finalization route.

## API

Routes use the `/api` prefix. Reads require `Authorization: Bearer <demo session token>`. Domain writes also require `Content-Type: application/json` and an `Idempotency-Key` (8–200 characters). The UI supplies these headers.

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/demo/session` | Local-only persona selection; body `{"role":"Operator"}` |
| GET | `/health`, `/session`, `/overview` | Health, identity, counters |
| GET | `/connections` | Allowlisted simulated connection |
| GET | `/incidents`, `/messages` | Filtered, paginated inventory |
| GET | `/messages/{id}` | Masked snapshot and evidence |
| GET / POST | `/replay-plans` | List plans / create immutable dry run |
| GET | `/replay-plans/{id}` | Complete plan and exclusions |
| POST | `/replay-plans/{id}/approve` | Approve with `planHash` and `note` |
| GET / POST | `/replay-jobs` | List jobs / start with `approvedPlanId` |
| GET | `/replay-jobs/{id}` | State, counters, first 100 ledger entries |
| POST | `/replay-jobs/{id}/{pause,resume,cancel}` | Operator controls |
| POST | `/verifications` | Verify/reconcile with `jobId` |
| GET | `/replay-jobs/{id}/report` | Exportable recovery report |
| GET | `/audit` | Paged audit records and hash-chain validation |

Inventory filters: `queue`, `error`, `search`, `outcome`, `hours`. Inventory/audit pagination: `cursor` and `limit` (default 25, maximum 100). Plan/job lists currently return the most recent 100 records. Report counts distinguish exclusions at planning from unresolved execution attempts.

## Verification

```sh
npm test
npm run benchmark
npm run build
```

The tests cover duplicate request/start handling, unknown outcomes, malformed events, payload masking, snapshot/plan tampering, approval expiry, independent approval, role enforcement, allowlists, overlapping operation reservations, cancellation, pause, actual worker pacing, crash-after-send reconciliation, durable restart recovery, HTTP pagination, and audit integrity.

The benchmark indexes and plans 10,000 synthetic events in a temporary SQLite database and prints measured timings. It sends no messages and is **not** an AWS throughput, cost, or UI latency benchmark. The test suite binds an ephemeral loopback port for HTTP integration tests.

[GitHub Actions CI](https://github.com/ZhiminxCODE/QueueRescue/actions/workflows/ci.yml) runs on every push and pull request, and can also be started manually. It runs the backend suite on Python 3.10 and 3.13, the dry-run benchmark on Python 3.13, and a locked dependency install plus frontend build on Node 24. Official actions are pinned to commit revisions; the workflow has read-only repository permissions and requires no AWS credentials.

## Project layout

```text
src/                   React dashboard, API client, responsive CSS
backend/service.py     Recovery protocol, persistence, simulated transport/consumer
backend/server.py      Loopback HTTP API, demo sessions, static asset host
backend/benchmark.py   Reproducible 10,000-event index/plan benchmark
tests/                 Safety, persistence, and HTTP integration tests
```

## Prototype boundaries and next phase

Demo sessions deliberately let anyone on this local machine select a role. They are in-memory, expire after eight hours, and are invalidated by server restarts. This is not production authentication; do not expose the local server publicly. Snapshots and simulated payloads are plaintext in the local database. Audit triggers/hash chains are useful local integrity checks, not independent tamper-proof storage against a database administrator.

The simulated consumer completes synchronously at transport acceptance. Real workers require asynchronous verification, stale-status policy, network timeouts/backoff, and idempotency enforced inside the business service. A unique local shipment key demonstrates that contract; it does not prove universal exactly-once behavior. Evidence is simulated; the app does not invent real CloudWatch or trace links.

The AWS phase should replace local persistence and transport with DynamoDB conditional writes, encrypted S3 evidence, SQS, Lambda workers, Step Functions execution, Cognito authorizers, scoped IAM/KMS, and Terraform. It also needs opt-in collection with explicit SQS receive visibility behavior, real CloudWatch correlation, source retention alarms, adapter credentials in Secrets Manager, lease expiry across distributed workers, retry/circuit-breaker controls, budgets, protected audit exports, and an explicit source finalization process. Infrastructure has intentionally not been provisioned by this prototype.

Relevant platform semantics checked against the primary AWS documentation:

- [SQS DLQ redrive](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-configure-dead-letter-queue-redrive.html): redrive assigns new message IDs and does not provide filtering/modification within a move task.
- [Lambda with SQS](https://docs.aws.amazon.com/lambda/latest/dg/with-sqs.html): processing is at least once; consumers must handle duplicates safely.

The supplied Word specification is retained unchanged.
