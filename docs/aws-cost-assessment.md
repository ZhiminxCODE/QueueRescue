# Cost decision for the AWS sandbox

The optional CloudTrail/audit-archive profile is not included here. Optional dashboard hosting is also excluded (`dashboard_hosting_included=false`); see its [cost and publication gates](dashboard-hosting.md). The calculator reports `audit_logging_included=false`; audit provisioning and capture default off. Enabling them requires a revised event, storage, monitoring and retained-resource estimate before any deployment. See [audit costs and controls](cloud-audit.md).

Reviewed November 15, 2026, for `us-east-1`, the current **minimal** Terraform profile and a locally hosted dashboard. This covers the base stack only. The now-prepared optional CloudFront hosting and audit infrastructure, and larger load tests, need new estimates. No resources were deployed to obtain prices.

**Cost decision: the user accepted a $1 project tolerance for one proposed paid demo, acknowledging that it is not an AWS spending cap.** This replaces the previous strict-$0 decision for that workload. Actual deployment, account plan upgrades, permission grants and destructive cleanup still require their concrete reviews. No credit coverage is assumed. Personal account inspection results stay in ignored `.data/` files.

November 19 update: the user accepted the [paid-demo workload/tolerance](aws-demo-plan.md) and approved the Lambda quota request. AWS denied submission through the inspection-only identity; an administrator handoff is documented. No infrastructure was deployed.

## Result

These are **gross USD before tax**, with **no free allowances or credits deducted**. They are usage scenarios, not measured AWS performance, a quote, a worst-case bound or a spending cap.

| Total resource lifetime, including setup and cleanup | One-second average Lambda duration illustration | Slow-runtime sensitivity |
| --- | ---: | ---: |
| 3 hours: 1 hour setup + 1 hour active demo + 1 hour cleanup | **$0.06345450** | **$0.19955518** |
| 7 days: one demo, then workers off, complete cleanup at day 7 | **$1.99454259** | **$2.13064327** |
| 30 days: one demo, then workers off, complete cleanup at day 30 | **$8.45484878** | **$8.59094946** |

Approximately **7 cents** for the illustrative demo or **20 cents** in the duration sensitivity scenario, with prompt cleanup. Neither is a maximum bill. Retaining the tables is the larger risk.

The slow-runtime scenario substitutes each function's configured timeout **plus 10 billed seconds per invocation** for initialization/overhead. It stresses duration; it does not predict a successful demo that runs every invocation to timeout. Longer initialization, more invocations, traffic, data or cleanup time can exceed it. The one-second average is an assumption, not a benchmark.

## Workload and request budgets

- One seed of **25 synthetic messages**, each at most 1 KiB before attributes, within one billing month. Two application users: Operator and Approver. No SMS, machine-to-machine grants or paid Cognito security features.
- One normal plan/replay workflow, at most 25 snapshots, chunks of at most five. The existing seed contains three UNKNOWN records and three timeout-after-effect records; the remaining 19 are recovery candidates. The estimate conservatively allows 25 replay deliveries. Safety decisions must still skip/block unsuitable records.
- **400 billed HTTP API units**: 200 application calls plus 200 CORS/preflight/request overhead units. Small requests remain below the 512 KiB billing increment. Manual refresh only.
- **570 Lambda invocations**: API 200, collector 10, consumer 100 (75 source deliveries plus 25 replay deliveries), replay worker 10, verifier 250. A collection samples at most ten messages; ten attempts do not guarantee full DLQ enumeration. Stop and reassess if sampling or retries exceed this budget.
- **10,000 standard SQS units** including sends, source retries, replay, receives, empty polling, visibility changes, deletes and setup/cleanup inspection. Messages fit within one 64 KiB unit each. Managed polling counts are an allowance, not measured or enforced. No FIFO/fair-queue message group IDs.
- **100 Standard Step Functions transitions**, including reconciliation/retry/control margin. A normal five-chunk execution uses 15 transitions. Lambda usage is separate.
- **100 S3 write/list-tier requests** and **2,000 read-tier requests**: snapshot uploads, previews/plans/replay, configuration operations, version enumeration, cleanup checks and retry margin. Standard DELETE has no per-delete fee. Repeated deployments/investigations beyond these totals require recalculation.
- **2 MiB total S3 storage**, including current/noncurrent versions and delete-marker/key overhead; **10 MiB DynamoDB storage** across three tables, including item/index overhead; **10 MiB logs ingested** across five functions and HTTP access logs. No compression savings assumed. Retained stacks budget up to two days of log archive occupancy despite configured one-day retention.
- **10 MiB internet egress** for dashboard/API use, inspection/downloads and setup/cleanup. Service traffic stays in-region; no NAT, VPC endpoint or cross-region path.
- Notification reserve: **100 SNS request units and 10 email deliveries**, allowing subscription setup and test/retry overhead. Minimal mode has no alarm producers. Budget emails are direct and free. Conservatively reserve **10 Cognito-related emails plus 1 MiB mail data** at SES rates; this does not assert that Cognito's AWS-managed default sender necessarily bills a separate SES line to this account.

These are planning budgets, not enforced aggregate limits. A failed demo, service retries, extra sessions or public API traffic can exceed them. The three-hour scenario assumes **all chargeable resources cease usage by its end**, including tables. Deploying earlier or cleaning up later adds table hours.

## Complete service estimate: three-hour resource lifetime

| Service / meter | Quantity × regional rate before allowances | USD |
| --- | --- | ---: |
| API Gateway HTTP API | 400 × $1/million | 0.00040000 |
| Lambda requests | 570 × $0.20/million | 0.00011400 |
| Lambda arm64 compute, 256 MiB | 570 × 1 second × 0.25 GiB × $0.0000133334/GB-second | 0.00190001 |
| Four standard SQS queues | 10,000 × $0.40/million; message storage included | 0.00400000 |
| Standard Step Functions | 100 × $0.000025/transition | 0.00250000 |
| DynamoDB provisioned capacity | 3 tables × 5 units × ($0.00065/WCU-hour + $0.00013/RCU-hour) × 3 hours | 0.03510000 |
| DynamoDB Standard storage | 10/1,024 GiB × 3/720 month × $0.25/GiB-month | 0.00001017 |
| S3 Standard writes/list | 100 × $0.005/1,000 | 0.00050000 |
| S3 Standard reads | 2,000 × $0.0004/1,000 | 0.00080000 |
| S3 storage, including versions/markers | 2/1,024 GiB × 3/720 month × $0.023/GiB-month | 0.00000019 |
| CloudWatch Standard log ingestion | 10/1,024 GiB × $0.50/GiB | 0.00488281 |
| CloudWatch log archive | 10/1,024 GiB × 3/720 month × $0.03/GiB-month | 0.00000122 |
| Cognito Lite | 2 MAUs × $0.0055; MAUs are not prorated to one hour | 0.01100000 |
| SNS requests | 100 × $0.50/million | 0.00005000 |
| SNS email reserve | 10 × $2/100,000 | 0.00020000 |
| Cognito/SES email reserve | 10 × $0.10/1,000 + 1/1,024 GiB × $0.12/GiB | 0.00111719 |
| Internet data out | 10/1,024 GiB × $0.09/GiB | 0.00087891 |
| Budgets monitoring/direct email notices | No budget actions or scheduled budget reports | 0 |
| IAM/STS; configured service-owned encryption | No customer-managed KMS key or additional service fee | 0 |
| **Total, calculated before row rounding** | | **0.06345450** |

DynamoDB transactions consume provisioned capacity rather than adding on-demand request charges; throttling can extend work. Slow-runtime sensitivity changes only Lambda compute to **$0.13800069** (10,350 GB-seconds), giving **$0.19955518** overall under the other assumptions.

Incident investigation adds a metadata membership copy and incident-summary update to each new snapshot transaction. These share the existing provisioned ledger capacity and 10 MiB total storage assumption for the 25-message demo; no additional table or index is created. Metadata searches make at most four queries of up to 100 rows per API request. Filter submissions, incident drill-downs and continuation pages must fit within the existing 200 API-Lambda invocation budget. Additional browsing, collection or retries require reassessment; increased capacity pressure can extend runtimes beyond the illustrative average.

The November 16 log-correlation implementation uses the existing function log groups and no tracing service. Allowlisted JSON telemetry must fit within the existing 10 MiB log-ingestion allowance. Explicit source-message lookups make at most two CloudWatch `FilterLogEvents` calls, or four for an exact replay attempt, with at most 25 returned events per call and a 24-hour search window. These bounds limit client requests/results, not provider-side scan work or a bill. Lookup API invocations share the existing 200-invocation API allocation; extended browsing, increased runtimes or logging beyond this scenario require a refreshed estimate before deployment.

Retention warnings add one `GetQueueAttributes` request per message/member-list API read or successful snapshot inspection. These reads share the existing 10,000-request SQS reserve and 200-invocation API allocation; they create no new resources and do not poll in the background or extend queue/snapshot retention. Additional refreshes and request latency must stay within the reviewed workload, or the estimate needs reassessment.

Incident reports add bounded ledger reads (at most 32 metadata operations, with capped member/plan/job/cost history), incident reference writes within existing transactions, and optional single-sample log lookup using the existing two-/four-call bounds. Index/cost rows share the existing ledger/storage allocation and expire with incident metadata; reservations/audit retention are unchanged. Report generation, explicit refreshes and cost-annotation API requests share the existing 200 API invocations. Additional read/write pressure can increase throttling/runtime; larger histories or workloads require reassessment. Report cost fields are declared Admin allocations with source hashes, not an automatic billing integration or proof of zero cost.

The configured event mappings, Cognito public client/domain, HTTP JWT authorizer and state machine add no fixed resource fee. Lambda ZIP artifacts, Terraform state and the frontend are local. No provisioned concurrency/pollers, extra ephemeral storage, custom metrics, alarms, dashboards, log queries/Live Tail, tracing, backups/PITR, customer KMS keys, separate CloudTrail trail/data events, SES dedicated IPs, frontend hosting, custom DNS, VPC/NAT, EC2 or RDS are configured. Their usage is zero here; enabling them changes the estimate. Unrelated account charges, paid support, tax, currency conversion and invoice rounding are outside this project estimate.

### Reproduce without accessing AWS

```sh
python3 -m tools.estimate_aws_demo
python3 -m tools.estimate_aws_demo --stressed
python3 -m tools.estimate_aws_demo --resource-hours 168
python3 -m tools.estimate_aws_demo --resource-hours 720 --stressed
python3 -m unittest discover -s tests -p test_aws_cost_estimate.py -v
```

The calculator uses Decimal arithmetic, no free allowance/credit, no network calls and no infrastructure changes. Resource hours mean the **whole lifetime**, not time added after the demo. Monthly arithmetic explicitly uses 30 days/720 hours; calendar-month billing can differ. Rates are a reviewed snapshot, not a live feed.

## Retention and cleanup

With no free capacity available, tables alone cost **$0.0117/hour**, **$0.2808/day**, **$1.9656/7 days** or **$8.424/30 days**, even with workers disabled. Retention scenarios retain the S3/DynamoDB storage allowances for the full lifetime, ignoring lifecycle/TTL savings. They do not repeat demo traffic, log ingestion or MAUs. Another active month/demo adds usage.

Current defaults preserve data; a one-hour demo does **not** automatically delete resources. Proposed steps for a later approved deployment:

1. Record the stop/cleanup deadline before deployment. Start with workers off. Stop seeding/new requests if counts, retries, storage or time exceed the model. Budget notices are delayed warnings, not automatic shutdown.
2. After the active hour, stop new jobs, reconcile/cancel in-flight work, disable event mappings/replay and verify executions have ended. This stops intended worker traffic, not table/storage charges or arbitrary API requests.
3. Export reviewed, masked evidence locally if needed. Explicitly decide whether to retain or delete AWS audit/business data. Those tables have no TTL; eventual ledger TTL never removes table capacity.
4. Prepare a reviewed retention/destroy plan. DynamoDB/Cognito deletion protection and S3 `force_destroy=false` block unattended complete teardown. Removing protection and deleting evidence require an explicit data-loss decision; no deletion is approved here.
5. If approved, remove **all S3 versions/delete markers** and resolve multipart uploads before deleting the bucket. Delete the three tables, users/pool, six log groups and remaining sandbox resources through the reviewed plan. Request budgets reserve enumeration/configuration/verification. S3 Standard has no early-deletion minimum; DELETE is free but LIST/read remains metered.
6. Verify resource absence, no workflows and no residual provisioned capacity; inspect billing/usage after it updates. Extend estimated resource hours if anything remains. Worker kill switches or a reached deadline do not prove cleanup.

S3 current-version expiration after seven days can create a delete marker; separate noncurrent expiration is one day, with day-boundary/asynchronous processing. This does not promise every byte disappears at hour 168. CloudWatch deletion can lag too; expired events marked for deletion stop contributing to archive charges. See [S3 expiration](https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-expire-general-considerations.html) and [CloudWatch retention](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/Working-with-log-groups-and-streams.html).

## Allowances and cost path

The estimate assumes **none** of the published shared allowances remain available: Lambda 1 million requests/400,000 GB-seconds; SQS 1 million requests; Standard Step Functions 4,000 transitions; DynamoDB Standard 25 RCUs/25 WCUs/25 GB; Cognito direct/social sign-in 10,000 MAUs; CloudWatch 5 GB log allowance; SNS 1 million requests/1,000 email notifications; aggregate internet egress 100 GB/month. Verify eligibility, scope and remaining headroom. Missing Free Tier usage entries do not establish coverage.

Even if all these cover this demo, HTTP API/S3 still need coverage or a nonzero cost decision, as does billable Cognito email. Legacy API allowances are time-limited; creating a new API does not restart them. A current $0 bill does not establish future coverage.

| Path | Decision / evidence needed |
| --- | --- |
| Strict $0/local — previous decision | Keep AWS undeployed; continue local simulator, mocked adapters and offline dashboard work. These local runs generate no AWS usage. |
| Verified credits / approved school lab | Verify available balance, expiry, service coverage and allowed services/IAM before reviewing deployment. Unverified future credits do not resolve the blocker. |
| Paid demonstration — accepted cost path | The user accepted nonzero charges and a **$1 planning tolerance**, acknowledging it is not enforced by AWS. The seven-day scenario already exceeds it. Quotas, permissions and exact deployment/cleanup review remain; the accepted tolerance does not approve an apply. |

## Primary rate sources

- [HTTP API requests and data-out examples](https://aws.amazon.com/api-gateway/pricing/); [aggregate egress allowance](https://aws.amazon.com/ec2/pricing/on-demand/).
- [Lambda pricing](https://aws.amazon.com/lambda/pricing/) and [regional price list](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSLambda/current/us-east-1/index.json), published November 11, 2026: `Lambda-GB-Second-ARM`, `Request`.
- [SQS pricing/metering](https://aws.amazon.com/sqs/pricing/) and [regional price list](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSQueueService/current/us-east-1/index.json), November 11, 2026: `Requests-RBP` standard tier.
- [Standard Step Functions US East example](https://aws.amazon.com/step-functions/pricing/); [DynamoDB capacity/storage](https://aws.amazon.com/dynamodb/pricing/).
- [S3 pricing](https://aws.amazon.com/s3/pricing/) and [regional price list](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/us-east-1/index.json), November 9, 2026: `Requests-Tier1`, `Requests-Tier2`, first Standard `TimedStorage-ByteHrs` tier. Storage GB is binary GiB.
- [CloudWatch log pricing](https://aws.amazon.com/cloudwatch/pricing/), Standard examples; [Cognito pricing](https://aws.amazon.com/cognito/pricing/) and [regional price list](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonCognito/current/us-east-1/index.json), October 20, 2026: `USE1-CognitoLiteMAU`.
- [Cognito email configuration](https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-email.html); [SES sending/data rates](https://aws.amazon.com/ses/pricing/). The reserve avoids relying on an unverified default-sender exemption.
- [SNS pricing](https://aws.amazon.com/sns/pricing/) and [regional price list](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonSNS/current/us-east-1/index.json), November 12, 2026: `Requests-Tier1`, `DeliveryAttempts-SMTP`; [Budgets free monitoring/notices](https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/).

See [cost controls](aws-cost-controls.md), [project checklist](project-checklist.md) and [account readiness](aws-deployment.md#read-only-account-readiness).
