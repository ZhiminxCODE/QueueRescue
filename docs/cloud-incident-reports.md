# Cloud incident reports

Implemented and verified offline on November 16, 2026. This is reporting for the bounded cloud prototype, not evidence of a deployed AWS demonstration. No billing source was queried, no resources were provisioned and no real incident costs were measured during implementation.

## Generate and export

Open **Failure inbox → Incident groups → Incident report**, or open a group and choose **Open incident report**. Viewers, Operators, Approvers and Admins can read the same authorized incident report. Opening/refreshing it reads recorded metadata; it does not collect messages, fetch S3 payloads, invoke a business verifier, replay/delete messages or extend retention. JSON and Markdown exports contain the generated report, its evidence references, provenance and limitations.

```text
GET /api/incidents/INCIDENT_ID/report
GET /api/incidents/INCIDENT_ID/report?evidenceMessageId=MESSAGE_ID
GET /api/incidents/INCIDENT_ID/report?evidenceMessageId=MESSAGE_ID&jobId=JOB_ID&attemptId=ATTEMPT_ID
```

An optional **Include selected log sample** action adds one exact source-message or replay-attempt lookup using the [existing bounded log-evidence contract](cloud-log-evidence.md). Optional `from`/`to` Unix-second parameters use that contract's lifetime and 24-hour limits. No arbitrary log group, filter or token is accepted. Missing or expired sample evidence remains unavailable/inconclusive, without changing the disposition counts. A sampled lookup is never complete incident-wide log coverage.

The incident must belong to the configured queue and remain within incident-metadata retention. Exports should be saved before that retention expires. The report does not claim original-message presence, complete queue inventory, distributed traces, a confirmed upstream root cause or an independently protected audit archive.

## Counting rules

Counts are mutually exclusive **snapshot** dispositions, deduplicated by snapshot ID across included plans/jobs. Their sum equals the historical indexed count represented by the report. Missing/unobserved members are added to unresolved, not silently dropped. They are not shipment counts.

| Disposition | Recorded basis |
| --- | --- |
| Recovered | A stable-read attempt is VERIFIED with domain outcome COMPLETE and a valid recorded check timestamp, linked through matching snapshot/plan checksums, job plan hash and separate approval identities. Later conflicting non-COMPLETE evidence for that snapshot makes it unresolved. |
| Skipped | A recorded plan SKIP or preflight SKIPPED attempt, without unresolved delivery evidence taking precedence. This does not credit QueueRescue with creating an already-complete shipment. |
| Blocked | Collector-recorded invalid schema or a recorded plan BLOCKED decision, without unresolved delivery evidence taking precedence. |
| Unresolved | Pending, cancelled, uncertain, failed, unverified, missing, inconsistent or otherwise insufficient completion evidence. A send acknowledgment or handler return is insufficient. |

Verification coverage uses **distinct attempted operation references** as its denominator, so duplicate snapshots do not inflate successful business operations. It shows attempted operations, recorded checks, recorded COMPLETE evidence and a completion percentage. A zero denominator is not applicable, not 100%. Reporting never makes a fresh business check; timestamps describe the evidence already recorded.

The duration is the span of the **included replay jobs**, including paused time and any shared-job work. It is not a claim that the entire incident has been resolved. A job's `resolvedAt` is recorded atomically the first time verification establishes that all its attempts are VERIFIED/SKIPPED. Later refresh/verification does not move that timestamp. It can be recorded after cancellation without changing the CANCELLED state. Missing or inconsistent endpoints remain unknown; elapsed time is labeled separately.

## Evidence and hypotheses

Evidence references identify snapshot metadata/checksums, hash-matched plan documents and separate-approval observations, attempt-ledger states/checks and any explicitly sampled correlated telemetry. Bodies, raw message attributes, approval/control free text, queue/account URLs and raw business/Cognito identities are excluded. Business and actor references are hashed identifiers, not secrets or anonymization.

Findings use fixed, evidence-backed descriptions. For example, schema rejection can be supported by collector metadata, and synthetic before/after-effect timeouts or AWS failures can be supported by correlated telemetry. Such observations do not establish every upstream cause. Without supporting observations, the report explicitly says the cause is not established. Recovery success alone does not manufacture a root-cause hypothesis.

## Bounds, missing history and consistency

New plan creation atomically stores an incident/plan reference with its existing plan/audit/idempotency writes. Job start atomically stores incident/job references only for groups with eligible operations, alongside job creation, operation reservations and approval consumption. Approved plan documents/hashes remain unchanged by these references. At the maximum 25 messages across 25 groups, job start uses 80 transactional writes including audit/idempotency records, within the existing 100-write limit. No new table, secondary index or infrastructure permission is required.

Report index rows expire with the incident summary. Audit/business data and uncertain-operation reservations do not gain TTL. Existing plans/jobs are not scanned or automatically backfilled. Legacy member/index markers, missing metadata, invalid checksums/approvals and missing attempts produce partial-coverage diagnostics. Historical membership can be reconstructed from validated plan links when a member row is gone, but that absence remains explicit. The completeness flag concerns the indexed history read through this implementation, not an independent proof that all historical activity was indexed.

Each report reads at most 100 member rows, five plan references, five job references and ten cost observations, with a hard **32 metadata-read** budget. It loads only referenced plans/jobs and their bounded attempts. It does not scan catalog, audit or ledger tables, and it does not follow report-reference continuation tokens automatically. Larger/partial pages are marked incomplete. A job metadata reread detects changed/truncated job observations; a final incident-count reread detects collection changes. Reads remain non-transactional and can span concurrent changes. Coverage diagnostics must be retained when sharing the report.

`reportHash` is SHA-256 of compact, sorted-key UTF-8 JSON with `reportHash` omitted (`cloud.contracts.encode`). Exported whitespace is not part of that digest. It identifies the generated JSON model; it is not a signature, trusted timestamp or independent tamper-proof archive. A browser-exported JSON file was checked against this digest during offline verification.

## Measured costs, estimates and provenance

Missing cost data is null/unavailable, never an invented zero bill. The report keeps an admin-recorded billing measurement separate from an estimate. It does not add overlapping observations together or infer a final incident total. Setup, shared capacity, credits, retained storage and later usage can require separate allocation/reconciliation.

Admins can record an observation from the report's **Record a cost observation** form, or through the authenticated API:

```text
POST /api/incidents/INCIDENT_ID/cost-observations
Idempotency-Key: SAME_KEY_FOR_RETRY
```

The exact JSON fields are `kind`, `amountUSD`, `sourceKind`, `sourceSha256`, `periodStart`, `periodEnd`, and `observedAt`. Amount is a nonnegative decimal USD string; times are integer Unix seconds, ordered and nonfuture, and the window must overlap the incident's collection window. `sourceSha256` identifies the reviewed source artifact without uploading its private contents.

`MEASURED` requires declared source `AWS_BILLING_EXPORT`. `ESTIMATED` accepts `AWS_USAGE_EXPORT`, `OFFLINE_CALCULATOR` or `MANUAL_ESTIMATE`. The application does **not** independently inspect or validate that source or allocation: all observations remain `ADMIN_RECORDED`, `providerVerified: false`, and `ADMIN_ALLOCATED_TO_INCIDENT_WINDOW`, including a recorded zero. The source kind, hash, time window, observed/recorded times and hashed recorder are exported. This is an auditable record of the Admin's declared measurement/estimate, not an automatically verified invoice or spending cap.

Viewers, Operators and Approvers cannot record these observations. Accepted records are appended atomically with audit and same-key request recovery, and expire with incident metadata. Input cannot alter recovery counts, approvals or business evidence. No Cost Explorer/billing permissions are added. Actual billing measurements, their attribution and live authorization still need review during the cost-approved deployment demonstration.

## Offline verification and cost boundary

Seven committed synthetic incident labels cover before-effect recovery/duplicates, after-effect already-complete shipments, invalid schema, unknown verifier outcomes, uncertain transport, late proof after cancellation and mixed dispositions. Tests additionally cover grouping/index association, optional real-SDK/Moto log correlation, privacy, authorization, cost provenance/idempotency, missing/legacy/tampered history, concurrent changes, bounded reads and the maximum transactional write count.

Browser checks used ignored synthetic HTTP fixtures to verify Viewer reporting, missing-history warnings, explicit log sampling, Admin-only estimate entry and both download buttons. Downloaded JSON and Markdown were inspected for count/coverage/provenance consistency and absence of raw private fixture fields. Runtime evidence, downloaded reports, source artifacts and build output are not committed.

Run the full Python suite, frontend tests/build, Lambda packaging and offline Terraform checks. Reporting adds metadata reads/writes and optional bounded log calls within the existing demo allocations; additional reports, history or latency require reassessment. Keep workers idle and the strict cost gate active. Nothing in report generation authorizes live deployment or establishes zero cost.
