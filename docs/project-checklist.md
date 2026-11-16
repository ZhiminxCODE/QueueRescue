# QueueRescue completion checklist

Status reviewed November 16, 2026 against the original technical prototype and the current repository. Checked items mean the stated implementation or offline verification is complete; they do not imply a successful AWS deployment. The full visual design remains deferred as requested.

## Completed foundation

- [x] Working local React dashboard and Python/SQLite recovery simulator.
- [x] Masked inspection, local failure filtering/grouping, dry-run plans, separate approval, and local reports.
- [x] Immutable plan hashes/expiry, operation reservations, idempotent mutations, bounded replay, pause/resume/cancel, and conservative business verification.
- [x] Independent simulated consumer, restart recovery, and crash-after-send / crash-after-effect tests.
- [x] A reproducible 10,000-event local planning benchmark. This is not a measured AWS benchmark or a complete browser performance report.
- [x] AWS collector, encrypted snapshots, DynamoDB ledger, SQS/Lambda shipment demonstration, verifier, and Standard Step Functions worker implemented and tested offline.
- [x] Terraform, deterministic Lambda packaging, dependency locks, GitHub CI, and deployment/teardown documentation.
- [x] AWS dashboard mode: Cognito PKCE, server-provided roles, explicit selection, separate approval, jobs, audit, connections, manual refresh, and persisted same-key request recovery.
- [x] AWS browser workflow checked with offline HTTP fixtures, including ambiguous response recovery and Viewer restrictions.
- [x] Non-root development identity, MFA, temporary CLI sign-in, and a limited account-inspection policy verified.
- [x] Fresh Terraform defaults use minimal mode with consumers/replay off. A one-cent budget warning is configurable; it is not a spending cap.
- [x] Latest implementation checks passed: 139 Python tests, 17 frontend tests, frontend build, deterministic Lambda packaging, Terraform validation and six mocked Terraform plans. CI is configured; this is not a claim about an uninspected remote CI run.

## 1. Resolve deployment costs — first blocker

- [x] **Me:** Complete the 25-message, one-hour demo estimate across the current minimal stack, including setup/cleanup requests, retry allowances, notifications, network, storage and 7-/30-day retention. [Worksheet and offline calculator](aws-cost-assessment.md): $0.06345450 illustrative or $0.19955518 with stressed runtimes and three total resource-hours, before tax and without allowances/credits. These are scenarios, not a cap or measured AWS cost.
- [ ] **Me + you:** Verify the applicable account/lab plan, credits, expiry, service coverage, shared allowances, and relevant lab restrictions. A missing Free Tier usage entry is not proof that a service is free.
- [ ] **You:** Choose strict $0 with local/offline work, verified credits/lab coverage, or an explicitly reviewed nonzero spending tolerance. The existing strict $0 requirement remains active until changed.
- [x] **Me:** Document proposed count/time/storage stop conditions and retention/teardown choices in the cost worksheet. Worker kill switches do not remove storage or table charges; actual deployment-specific deadlines and destructive cleanup still require review.
- [ ] **Me + you:** Confirm notifications work and understand that they can arrive after additional usage. Do not describe an alert threshold as a hard cap.

Exit: a documented cost decision that covers the complete proposed workload and retained resources. Until then, no live deployment. See [cost assessment](aws-cost-assessment.md).

## 2. Make the account deployable

- [ ] **Me:** Resolve active-demo Lambda concurrency requirements through a reviewed quota-compatible design or a quota increase. The last inspected regional quota does not fit the current active reservation settings. Do not request an increase automatically.
- [ ] **Me:** Prepare scoped deployment permissions, including restrictions on IAM role creation/passing and access outside the sandbox. The current development policy is inspection-only.
- [ ] **You:** Review the exact permission changes before they are applied. Complete any password/MFA prompts directly in AWS.
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
- [ ] **Me:** Show DLQ age/retention warnings before source evidence expires, with snapshot permissions and retention behavior tested.
- [ ] **Me:** Implement a complete cloud post-incident report: evidence-backed hypotheses, recovered/skipped/blocked/unresolved counts, duration, verification coverage, and measured cost. Current cloud exports are plan/job snapshots and audit pages.
- [ ] **Me:** Add representative labeled incident fixtures and test grouping/correlation/report behavior.

This work can be implemented and tested offline while account blockers are resolved.

## 5. Finish and verify the customer-deployed prototype

- [ ] **Me:** Implement the brief's private S3 + CloudFront frontend hosting, including SPA callback routing, HTTPS and restricted bucket access, after its costs are reviewed. Current AWS dashboard hosting is local.
- [ ] **Me:** Review CloudTrail coverage and protected audit retention; implement the approved configuration without implying application audit records are tamper-proof against account administrators.
- [ ] **Me:** Run a security/IaC review of role escalation, queue access, snapshot masking, CORS/auth, retention and dependencies; fix material findings.
- [ ] **Me:** Measure the 10,000-event local benchmark and browser responsiveness, then produce a benchmark report. Any AWS load test requires a separate cost-reviewed workload; initial cloud plans remain limited to 25 messages.
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
