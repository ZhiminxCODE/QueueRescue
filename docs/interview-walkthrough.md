# Five-minute project walkthrough

Use this for a backend, cloud or full-stack interview. The spoken script is about 550 words; allow the rest of five minutes for pointing at the screens and brief pauses. Time a rehearsal rather than assuming the word count guarantees the duration.

Open the [gallery](demo-showcase.md), [recording](media/queuerescue-local-demo.mp4) and [local benchmark report](benchmark-report.md). Present the saved screenshots while speaking; the silent recording is an optional demonstration, not an extra 90 seconds to add to the script. No running server, AWS login or deployment is needed. The capture uses synthetic data and simulated identities. This guide prepares a rehearsal; it does not record one as completed.

## Spoken script and screen cues

### 0:00–0:40 — Problem and scope

Show [the failure inbox](media/01-failure-inbox.png).

> QueueRescue helps an operator recover failed shipment events selectively. The problem is that sending an event again does not prove the shipment completed, and a missing acknowledgement does not prove the original send failed. A recovery tool has to handle both cases without blindly repeating a business action.
>
> The working demonstration uses React, a Python API and SQLite, with an independent simulated shipment consumer. I also implemented the AWS backend and Terraform and tested them offline. The AWS deployment has not been verified, so the results I’m showing are local synthetic results.

### 0:40–1:20 — Investigation and planning

Show [masked inspection](media/02-masked-inspection.png), then [the broad dry run](media/03-dry-run-plan.png).

> The operator starts with retained failure snapshots. Inspection masks customer contact fields and shows the evidence used to classify each operation. A dry run selects candidates and explains exclusions without sending anything.
>
> This fixture contains 1,000 events. Its first preview classifies 750 as eligible, 14 as already complete, 74 as needing review and 162 as blocked. That broad preview is never executed. For the demonstration, I create a second plan for ten eligible operations and limit replay to two messages per second.

### 1:20–2:00 — Approval and execution boundaries

Show [the approved ten-operation plan](media/04-separate-approval.png).

> The plan has a versioned hash and an expiry. Approval is tied to the exact selection and settings, and the creator cannot approve their own plan. The local demo switches between simulated Operator and Approver identities; those personas are not production authentication.
>
> The backend enforces the workflow rather than relying on hidden buttons. Operation reservations prevent overlapping recoveries from claiming the same operation. The worker sends at the approved bounded rate, and accepting a delivery is tracked separately from verifying its business result.

### 2:00–3:10 — The difficult failure case

Show [the interrupted job](media/06-interruption-reconciliation.png), then [verified recovery](media/05-verified-recovery.png).

> The key failure case is a worker stopping after sending but before recording acknowledgement. The receiver may already have accepted the event. Immediately retrying could create a duplicate effect.
>
> This demo deliberately creates that gap. The job pauses with one uncertain delivery and nine queued candidates. Reconciliation checks the consumer-owned shipment records before allowing the remaining work to continue. Unknown evidence is not treated as permission to resend.
>
> The consumer deduplicates by business operation ID, and verification is a separate action. The completed fixture has ten verified attempts, ten deliveries for ten distinct operations and all 1,000 original snapshots retained. This demonstrates one controlled scenario; it is not a claim of universal exactly-once delivery.

### 3:10–4:10 — Evidence and an engineering tradeoff

Point to [the benchmark follow-up](benchmark-report.md#saved-plan-loading-follow-up).

> The tests include actual child-process termination after transport acceptance and after shipment creation, followed by reopening the database and checking recovery without a second send or duplicate shipment. Other tests cover self-approval, changed plans, expiry, verifier outages and durable restart behavior.
>
> I also measured planning separately from replay. On a five-plan local fixture, returning all full plans produced about 12 megabytes of JSON. Compact list summaries reduced that response to 861 bytes, while the selected full document remained available for review. Summaries never became approval authority. These measurements are from local synthetic workloads, not AWS throughput benchmarks.

### 4:10–5:00 — Architecture, limits and next step

Show [the local architecture](demo-showcase.md#how-it-works); use [the AWS diagram](aws-architecture.md) only if asked.

> Locally, the API manages plans and the ledger, while the replay worker and independent consumer exchange simulated deliveries through SQLite. Business verification reads the consumer’s shipment proof.
>
> The prepared AWS implementation uses SQS, Lambda, DynamoDB, S3, Step Functions and Cognito. The next milestone is a reviewed, small real-account demonstration, including authorization, interruption recovery and teardown. Cost, permissions and concurrency constraints still need resolution. For production, I would also need a real business verifier with authenticated, fresh evidence and stronger operational validation.

## Questions to practice

| Interview question | Core answer | Repository evidence |
| --- | --- | --- |
| Why not replay every failure? | Some operations are already complete, unsafe or unresolved. Require eligibility evidence and an explicit selection. | [Classification and planning](../backend/service.py); [AWS safety boundaries](aws-architecture.md#safety-boundaries) |
| What happens after an ambiguous send? | Preserve uncertainty, pause, inspect business evidence and avoid automatic resend. A timeout alone does not establish non-delivery. | [Crash/restart safety tests](../tests/test_safety.py); [process-kill tests](../tests/test_async_recovery.py) |
| Is this exactly-once processing? | No universal guarantee. A unique business operation key and reconciliation demonstrate duplicate protection in this shipment domain. External systems need their own idempotency contract. | [Independent consumer](../backend/simulator.py); [prototype boundaries](../README.md#prototype-boundaries-and-next-phase) |
| Why hash a plan? | Bind approval to the reviewed selection and settings; enforce expiry and integrity again on the backend. A hash alone is not authorization. | [Plan creation, approval and execution](../backend/service.py) |
| Why keep originals? | Preserve evidence and avoid destructive source actions during recovery. Retention and source finalization require a separate policy. | [Local showcase](demo-showcase.md); [retention contract](cloud-retention.md) |
| Why SQLite? | It keeps the local demonstration reproducible and supports transactional state. It does not prove distributed scaling; the AWS implementation uses DynamoDB. | [Project map](project-map.md); [AWS architecture](aws-architecture.md) |
| What did the benchmark prove? | Bounded local planning/list behavior under the documented method. It did not measure cloud replay throughput, production capacity or a stable tail latency. | [Method and results](benchmark-report.md) |
| What is the strongest limitation? | Live AWS integration and enforcement remain unverified. Local identities, simulated evidence and offline AWS tests have explicit limits. | [Completion checklist](project-checklist.md) |
| How did you use coding assistance? | Describe the assistance you actually used. Distinguish generated implementation from decisions you reviewed and behavior you can explain and reproduce. Do not imply unaided authorship. | Use specific commits, tests and design decisions you understand. |

## Rehearsal

1. Explain the interrupted-send case without reading the script. Draw API → ledger → worker → delivery → consumer → proof → verification.
2. Give the talk with the screenshots and a five-minute timer. Record the actual duration privately; cut detail if it runs long.
3. Answer the questions above without notes. For weak answers, read the linked implementation or test and explain the behavior in your own words.
4. End with three precise facts: what works locally, what is tested offline for AWS, and what still needs live validation.

Keep the existing application and cloud switches untouched during this rehearsal. Any future live demonstration should use the isolated capture procedure or a separately reviewed deployment plan.
