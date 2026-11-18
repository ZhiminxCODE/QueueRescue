# QueueRescue completion checklist

Status reviewed November 18, 2026 against the original technical prototype and the current repository. Checked items mean the stated implementation or offline verification is complete; they do not imply a successful AWS deployment. The full visual design remains deferred as requested.

## Completed foundation

- [x] Working local React dashboard and Python/SQLite recovery simulator.
- [x] Masked inspection, local failure filtering/grouping, dry-run plans, separate approval, and local reports.
- [x] Immutable plan hashes/expiry, operation reservations, idempotent mutations, bounded replay, pause/resume/cancel, and conservative business verification.
- [x] Independent simulated consumer, restart recovery, and crash-after-send / crash-after-effect tests.
- [x] A reproducible 10,000-event local planning benchmark, plus a [measured local backend/browser report](benchmark-report.md). This is not a measured AWS benchmark.
- [x] AWS collector, encrypted snapshots, DynamoDB ledger, SQS/Lambda shipment demonstration, verifier, and Standard Step Functions worker implemented and tested offline.
- [x] Terraform, deterministic Lambda packaging, dependency locks, GitHub CI, and deployment/teardown documentation.
- [x] AWS dashboard mode: Cognito PKCE, server-provided roles, explicit selection, separate approval, jobs, audit, connections, manual refresh, and persisted same-key request recovery.
- [x] AWS browser workflow checked with offline HTTP fixtures, including ambiguous response recovery and Viewer restrictions.
- [x] Non-root development identity, MFA, temporary CLI sign-in, and a limited account-inspection policy verified.
- [x] Fresh Terraform defaults use minimal mode with consumers/replay off. A one-cent budget warning is configurable; it is not a spending cap.
- [x] Latest implementation checks passed: 229 Python tests, 33 frontend tests including an isolated hosting release build, deterministic Lambda packaging, Terraform validation and 32 mocked Terraform plans. CI is configured; this is not a claim about an uninspected remote CI run.

## 1. Resolve deployment costs — first blocker

- [x] **Me:** Complete the 25-message, one-hour demo estimate across the current minimal stack, including setup/cleanup requests, retry allowances, notifications, network, storage and 7-/30-day retention. [Worksheet and offline calculator](aws-cost-assessment.md): $0.06345450 illustrative or $0.19955518 with stressed runtimes and three total resource-hours, before tax and without allowances/credits. These are scenarios, not a cap or measured AWS cost.
- [ ] **Me + you:** Verify the applicable account/lab plan, credits, expiry, service coverage, shared allowances, and relevant lab restrictions. A missing Free Tier usage entry is not proof that a service is free.
- [ ] **You:** Choose strict $0 with local/offline work, verified credits/lab coverage, or an explicitly reviewed nonzero spending tolerance. The existing strict $0 requirement remains active until changed.
- [x] **Me:** Document proposed count/time/storage stop conditions and retention/teardown choices in the cost worksheet. Worker kill switches do not remove storage or table charges; actual deployment-specific deadlines and destructive cleanup still require review.
- [ ] **Me + you:** Confirm notifications work and understand that they can arrive after additional usage. Do not describe an alert threshold as a hard cap.

Exit: a documented cost decision that covers the complete proposed workload and retained resources. Until then, no live deployment. See [cost assessment](aws-cost-assessment.md).

## 2. Make the account deployable

- [ ] **Me:** Resolve active-demo Lambda concurrency requirements through a reviewed quota-compatible design or a quota increase. The last inspected regional quota does not fit the current active reservation settings. Do not request an increase automatically.
- [x] **Me:** Prepare optional runtime-boundary attachment and an offline minimal-stack IAM role-delegation proposal with exact role/service scope and boundary protections. [Scope, verification and grant prerequisites](aws-role-access.md). No permissions have been granted.
- [x] **Me:** Implement offline scoped minimal-stack service policy generation, exact resource-ID bindings, explicit missing-ID blockers, notification scope and managed-policy document packing. [Scope and bootstrap limitations](aws-service-access.md). No service access has been granted.
- [ ] **Me + you:** Review administrator bootstrap for generated IDs/account-wide log delivery, actual bindings/ownership, all effective grants, approved boundary contents, provider/service acceptance and teardown access. Generated policy files alone do not make the account deployable; the current development policy remains inspection-only.
- [ ] **You:** Review the exact permission changes before they are applied. Complete any password/MFA prompts directly in AWS.
- [x] **Me:** Add offline checks for the full minimal/idle saved-plan JSON, including exact inventory, boundary/worker settings, data protection, encryption, auth/CORS and destructive-change rejection. [Scope and remaining manual review](aws-plan-review.md). This does not create or approve a real account plan.
- [ ] **Me:** Rebuild deployment artifacts and prepare a saved Terraform plan with account, region, encryption, callbacks/CORS, worker switches, retention, resource counts, and cost settings reviewed.
- [ ] **You:** Review the concrete deployment/cost plan before applying it.

Exit: correct non-root identity, sufficient quotas, reviewed permissions and a reviewed saved plan. No permanent/root access keys are needed.

## 3. Validate the first real AWS workflow

- [ ] **Me:** Apply the reviewed sandbox plan and inspect actual resource/IAM configuration. No stack has been deployed yet.
- [ ] **You:** Supply email addresses for distinct Operator and Approver application test accounts; complete their password/MFA steps in Cognito. These are separate from the IAM developer login.
- [ ] **Me:** Create/configure the application users/groups and confirm delivery and role assignment.
- [ ] **Me:** Generate ignored dashboard settings from real outputs; verify real Cognito redirects, sign-out, token expiry, API authorization, and browser CORS.
- [ ] **Me:** Seed no more than 25 synthetic events for the first approved demonstration and confirm source processing/dead lettering.
- [ ] **Me:** Exercise collection consent, masked investigation, explicit selection, immutable plan review, separate approval, replay, and domain-confirmed verification through the browser.
- [ ] **Me:** Verify self-approval and unauthorized operations fail against the real API.
- [ ] **Me:** Exercise an uncertain send/worker failure and prove reconciliation does not blindly resend or create duplicate shipments; also check pause/cancel and late verification.
- [ ] **Me:** Disable workers after the demonstration, inspect retained resources/usage, and record measured evidence without committing secrets or raw sensitive payloads.

Exit: a recorded real-account demonstration with confirmed safeguards and observed usage/cost. Mock tests and screenshots alone do not satisfy this milestone.

## 4. Finish the investigation/reporting features from the brief

- [x] **Me:** Add cloud incident records and normalized schema-category grouping, with server-side filtering, bounded pagination and dashboard group drill-down. [Contract and offline verification](cloud-investigation.md). Groups are indexed samples, not confirmed root causes or a complete queue inventory; no automatic legacy backfill.
- [x] **Me:** Add structured correlation instrumentation and scoped CloudWatch evidence lookup for source messages and exact replay attempts, with explicit dashboard lookup, returned log-stream links, and incomplete/missing/denied states. [Contract and offline verification](cloud-log-evidence.md). Distributed traces remain uninstrumented; no trace links are invented. Real-account evidence and console-link validation still require the reviewed deployment milestone.
- [x] **Me:** Show original enqueue age and estimated DLQ expiry separately from snapshot access deadlines, with 24-hour warnings, explicit unknown/passed estimates and unverified original presence. [Contract and offline verification](cloud-retention.md). Tests cover scoped settings reads, masking/permissions, expiry during inspection and versioned evidence loss; live propagation/lifecycle validation remains part of deployment verification.
- [x] **Me:** Implement bounded, incident-scoped reports with evidence-backed findings, reconciled recovered/skipped/blocked/unresolved counts, recorded job duration, distinct-operation verification coverage, JSON/Markdown export and explicit partial-history diagnostics. [Contract and offline verification](cloud-incident-reports.md). Measured-cost observations and estimates retain Admin-recorded provenance; actual billing data/attribution and live behavior still require deployment validation.
- [x] **Me:** Add seven representative synthetic incident labels and test grouping/indexing, correlation and reporting, including duplicates, uncertain sends, late verification, partial evidence, privacy, cost provenance and transactional bounds.

This work can be implemented and tested offline while account blockers are resolved.

## 5. Finish and verify the customer-deployed prototype

- [x] **Me:** Prepare optional private S3 + CloudFront hosting, exact callback routing, OAC, cache/security policies and an isolated release builder with offline tests. [Hosting runbook](dashboard-hosting.md). Provisioning/delivery default off; actual cost review, publication and live browser/service validation remain deployment work.
- [x] **Me:** Prepare opt-in scoped CloudTrail capture and protected application audit archival, separate capture/retention switches, failure handling/alarms and offline policy/runtime tests. [Coverage, costs, verification and teardown runbook](cloud-audit.md). Governance retention is bypassable by privileged administrators; real AWS delivery, enforcement, completeness and notifications remain unverified and require the reviewed deployment milestone.
- [x] **Me:** Review role escalation, queue access, snapshot masking, CORS/auth, retention and dependencies; fix the material offline findings. [Review, fixes, verification and remaining deployment checks](security-review.md). Real IAM/MFA/CORS enforcement, CloudTrail/protected audit retention and account cost gates remain unverified in AWS.
- [x] **Me:** Measure five fresh 10,000-event local backend trials and five production-browser interaction cycles; document the method, results, zero-send checks and scaling limits in the [benchmark report](benchmark-report.md). Browser timings are scripted action-to-ready measurements, not INP or AWS throughput. Any AWS load test requires a separate cost-reviewed workload; initial cloud plans remain limited to 25 messages.
- [ ] **Me + you:** Rehearse the final demonstration and review the evidence, known limitations, cost/operations runbook, and recovery procedures.
- [ ] **Me + you:** Rehearse a reviewed teardown/retention procedure; confirm which data/resources remain and their possible ongoing costs.
- [ ] **Me:** Finish architecture write-up, reproducible setup instructions, measured results and a short demo recording/walkthrough.

Exit: another developer can reproduce the bounded AWS demonstration and assess its safety, cost and limitations from the repository/runbook.

## Later full-design / production work

- [ ] Full visual design, broader usability/accessibility testing and final responsive polish.
- [ ] Configuration for additional existing customer queues and a real external business verifier, including authentication, freshness and idempotency contracts.
- [ ] Explicit manual resolution of UNKNOWN outcomes without bypassing evidence requirements.
- [ ] Larger-volume collection/replay, stronger concurrency/load testing, durable shared infrastructure state and broader operational monitoring.
- [ ] Explicit source finalization/archive, only if needed and separately authorized. Retaining originals is the current deliberate safeguard.
- [ ] Stronger independent audit archival, recovery drills, and production release/deployment controls.

FIFO-specific semantics, multiple accounts/regions, EventBridge recovery, ECS workers and AI summaries are extensions, not prerequisites for the current standard-SQS shipment prototype.

## Your next inputs

1. Cost path: strict $0, available credits/lab, or a reviewed paid-demo estimate. Do not send financial credentials.
2. Operator and Approver test email addresses when the Cognito milestone is ready.
3. Approval of concrete access/deployment/retention changes when presented, plus direct completion of authentication prompts.

Implementation, testing, documentation, meaningful commits and GitHub pushes remain my work. You do not need to fix code or share passwords/access keys.
