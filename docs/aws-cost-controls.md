# AWS demo cost controls

The target for the first cloud demo is to avoid out-of-pocket spending. This is a constraint to verify against the account's actual plan, credits, and current usage, not something Terraform or a budget alert can guarantee. No resources should be deployed until those account details have been checked.

Optional dashboard hosting is **excluded** (`dashboard_hosting_included=false`); provision/delivery default off. Review CDN/edge traffic, bucket versions/storage/requests, invalidations and actual shared allowances before enabling it. See the [hosting runbook](dashboard-hosting.md).

Optional audit capture is **excluded** from the baseline estimate (`audit_logging_included=false`). Its provision/capture switches default off. Enabling it adds CloudTrail data events, archive requests/retained versions, a sixth Lambda, four alarms and a custom metric; review these costs and existing trail copies separately. See the [audit runbook](cloud-audit.md). Pausing capture preserves storage and monitoring costs.

The [cost decision worksheet](aws-cost-assessment.md) prices the complete current minimal stack for a 25-message, one-hour demo, including setup, cleanup, retained storage and runtime sensitivity. Its offline calculator applies no unverified allowances or credits. A small estimate does not establish a zero bill or enforce a spending cap.

An AWS budget is a warning threshold. It does not reserve money, prepay a service, or prevent requests from being billed. Billing and notification data are delayed; more usage can occur before a warning arrives. The stack now includes a zero-spend notification that warns about recorded usage costs before credits, plus the existing percentage notification. Neither is a real-time spending cap.

AWS Free account plans and Paid account plans have different behavior. An eligible Free plan can prevent out-of-pocket charges while active, but expires based on time or exhausted credits and restricts some services. A Paid plan can charge beyond free allowances or credits. Do not upgrade a plan merely to make this demo work without reviewing that consequence. The account's Free Tier page and credit balance are authoritative for its eligibility.

## Minimal profile for a fresh synthetic sandbox

Fresh configuration now defaults to `cost_profile="minimal"`, `consumers_enabled=false`, and `replay_enabled=false`; the example values file does too. The optional `standard` profile remains available for customer-managed key control and backups. Minimal mode:

- Uses SSE-S3 for evidence, SSE-SQS for queues, and default AWS-owned DynamoDB encryption. Data remains encrypted at rest; the stack creates no customer-managed KMS key and grants no KMS key permissions.
- Uses three provisioned DynamoDB tables at five read and five write capacity units each, for a fixed total of 15 RCUs and 15 WCUs. There is no autoscaling. This is below the published 25/25 capacity allowance by itself, but the allowance is shared with other eligible account usage and does not establish that this account pays nothing. Capacity throttling is possible for transactional bursts; retry ambiguous API requests using the same idempotency key.
- Turns off point-in-time backup and metric-alarm resources for the synthetic demo. This reduces backup and monitoring coverage; retain the standard profile for stronger recovery/operational requirements.
- Retains logs for one day. Evidence retains its existing short lifecycle; business idempotency and audit records do not receive evidence TTL.
- Uses Cognito Lite, bounded requests, and the existing replay rate limits. No NAT gateway, EC2, RDS, ECS, or hosted frontend is added.

Two separate switches control activity. `consumers_enabled=false` disables the SQS event source mappings so they do not poll idle queues and sets consumer reserved concurrency to zero. `replay_enabled=false` sets the replay function's reserved concurrency to zero. Enable them only for an intentional demo, then stop consumers and replay afterward. Turning off activity does not delete storage, provisioned capacity, the API, Cognito, or other resources, and it is not a complete account billing shutdown.

The local ignored deployment file uses minimal mode and leaves both activity switches off. It stores the supplied account ID, region, and notification email locally; these personal deployment values are not committed to GitHub. The named AWS profile requires a local temporary-credential sign-in.

## What must be checked in the account

1. Free versus Paid plan, approximate creation date, credits remaining, and their expiry.
2. Existing Free Tier usage in all relevant services, including DynamoDB provisioned capacity, SQS requests, Lambda, logs, API Gateway, Cognito, Step Functions, and S3. A service-level allowance is not dedicated to this project.
3. Existing AWS resources or charges unrelated to QueueRescue. The budget notification is account-wide and may warn about those too.
4. Availability of every required service under the current plan. A restricted service should remain undeployed until an alternative is agreed.

The [read-only readiness helper](aws-deployment.md#read-only-account-readiness) collects MFA registration, account plan/offer data, and regional capacity metadata under a narrow inspection policy. It never grants permissions, upgrades a plan, creates resources, or calls the paid Cost Explorer API. Inaccessible data remains unknown and requires follow-up. API Gateway's legacy free usage is limited to the first 12 months after account signup; an older account with no applicable credits may incur usage charges even for a tiny demo.

Check Lambda concurrency before enabling activity. The active sandbox reserves four units for consumers and one for replay, while [AWS requires 100 units to remain unreserved](https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html). A regional quota of ten cannot accommodate those active settings. Both idle switches use zero reservations; they do not make the active demo quota requirement disappear. The readiness report flags available reservation capacity. November 27 live inspection verified 1,000 total and 1,000 unreserved units, sufficient for the five active worker reservations. Recheck before activation. If capacity becomes insufficient, review a quota increase or a separately tested design change before enabling workers; this repository never requests an increase automatically.

If zero charges cannot be established acceptably for the proposed demo, keep using the local simulator and offline AWS tests. They do not require deploying billable resources.

## Existing stacks

Minimal mode is intended for a fresh sandbox. Existing stacks must explicitly retain their current `cost_profile` and worker settings before planning with changed defaults. Switching a populated standard stack can remove a KMS key, change encryption management, disable backups, and remove alarms. Existing S3 object versions may still need the old key. Do not apply a profile switch to existing encrypted evidence without a reviewed key/data migration and retention plan. Terraform moved blocks preserve the existing standard key addresses during the module's optional-key refactor; they do not make a deliberate key deletion safe.

## Primary sources

- [AWS Free Tier plans](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier.html)
- [AWS Budgets notification delay](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html)
- [KMS key and request pricing](https://aws.amazon.com/kms/pricing/)
- [DynamoDB pricing and provisioned Free Tier](https://aws.amazon.com/dynamodb/pricing/)
- [DynamoDB AWS-owned encryption](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/EncryptionAtRest.html)
- [S3 managed encryption](https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingServerSideEncryption.html)
- [SQS encryption at rest](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-server-side-encryption.html)
- [API Gateway pricing and 12-month Free Tier](https://aws.amazon.com/api-gateway/pricing/)
