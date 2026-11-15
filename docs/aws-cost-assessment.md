# Cost decision for the AWS sandbox

Pricing reviewed on November 15, 2026 for `us-east-1`. This is a reproducible cost worksheet, not a quote, a spending limit, or permission to deploy. Personal account eligibility, balances, and inspection results remain in ignored `.data/` files.

## Decision under a strict zero-charge requirement

Keep the AWS stack undeployed unless applicable credits or verified allowances cover every relevant meter. The working local simulator and offline AWS dashboard fixtures incur no AWS service usage. Low traffic, a small estimate, and budget emails cannot guarantee a zero invoice on a paid/legacy account.

The current complete cloud design uses API Gateway HTTP APIs and S3 evidence storage. These must be treated as metered unless account-specific coverage is established. API Gateway's legacy free allowance lasts 12 months after account signup; creating a new API does not restart it. Modern Free Tier credits depend on eligibility and remaining balance. See [API Gateway pricing](https://aws.amazon.com/api-gateway/pricing/), [S3 pricing](https://aws.amazon.com/s3/pricing/), and [AWS Free Tier plans](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier.html).

Do not remove authentication, evidence retention, or replay safeguards merely to avoid a tiny service fee. An alternative architecture needs its own implementation and validation.

## Service-by-service review

| Component in the minimal profile | Potential coverage and remaining checks |
| --- | --- |
| HTTP API | Legacy allowance expires after 12 months. Otherwise the initial request tier is $1 per million billed units, with data transfer separate. Requires applicable account coverage or acceptance of nonzero usage charges. |
| S3 Standard evidence | Storage and write/read/list requests are metered. Service-owned encryption has no customer-managed key fee. Seven-day expiration does not make storage or requests free; include versions and actual deletion behavior in review. |
| Three DynamoDB Standard tables | Total 15 RCUs and 15 WCUs. The published allowance is 25 RCUs, 25 WCUs and 25 GB; verify eligibility and shared headroom. No PITR, on-demand backups, autoscaling or global replicas are enabled in minimal mode. |
| Lambda arm64, 256 MB | Published monthly allowance: one million requests and 400,000 GB-seconds. Check shared usage. Reserved concurrency has no reservation fee; no provisioned concurrency or provisioned event pollers are configured. Capacity quota is a separate blocker. |
| Standard SQS | Published allowance: one million request units/month for all customers. Payload size, retries and empty receives affect unit counts; leave consumers off while idle. |
| Standard Step Functions | Published allowance: 4,000 state transitions/month, continuing beyond 12 months. Retries and workflow loops count. The stack does not use Express workflows. |
| Cognito Lite | Published allowance for direct/social sign-in: 10,000 MAUs/month, shared per account or organization, subject to pricing terms. Use two application users and password/authenticator MFA. No SMS, Plus/advanced-security features or machine-to-machine grants are configured. |
| CloudWatch Logs | Published allowance includes 5 GB ingestion and 5 GB archive. Minimal retention is one day, with no metric alarms. Log queries, extra metrics, and other account usage need separate review. |
| SNS / Budgets | Review requests, email delivery and notification terms. There are no alarm producers in minimal mode. The budget is a delayed notification, never a resource shutdown or spending cap. |

Primary pricing sources: [DynamoDB](https://aws.amazon.com/dynamodb/pricing/), [Lambda](https://aws.amazon.com/lambda/pricing/), [SQS](https://aws.amazon.com/sqs/pricing/), [Step Functions](https://aws.amazon.com/step-functions/pricing/), [Cognito](https://aws.amazon.com/cognito/pricing/), [CloudWatch](https://aws.amazon.com/cloudwatch/pricing/), [SNS](https://aws.amazon.com/sns/pricing/), [Budgets](https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/).

## Illustrative API/S3 charges

Assume 1,000 billed HTTP API request units, 100 S3 PUT/COPY/POST/LIST operations, 1,000 S3 GET/HEAD operations, and 1 MiB of current-version evidence retained for seven days of a 30-day month. Apply no free allowance or credits to these four meters.

| Meter | Calculation | Raw USD charge |
| --- | --- | ---: |
| HTTP API | `1,000 × $1 / 1,000,000` | 0.00100000 |
| S3 writes/list | `100 × $0.005 / 1,000` | 0.00050000 |
| S3 reads | `1,000 × $0.0004 / 1,000` | 0.00040000 |
| S3 current-version storage | `(1 / 1,024 GiB) × (7 / 30 months) × $0.023` | 0.00000524 |
| **These four meters only** | | **0.00190524** |

S3 rates were checked against the [public regional AWS price list](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/us-east-1/index.json), publication November 9, 2026, effective October 1, 2026: `Requests-Tier1`, `Requests-Tier2`, and the first `TimedStorage-ByteHrs` tier. S3's GB unit is binary (GiB). The HTTP API request rate is documented in AWS's [HTTP API pricing example](https://aws.amazon.com/api-gateway/pricing/).

This subtotal is **not the cost of the complete stack**. It excludes every other service, deployment/control requests, version/delete-marker overhead, retries beyond the assumed counts, extra retention, data transfer, taxes, invoice rounding, other projects, and unexpected traffic. It is not a worst-case bound. If the other services have sufficient remaining eligible free allowances, their covered meters may add zero; that must be verified rather than assumed.

One material recurring risk is provisioned DynamoDB capacity. With **none** of its capacity allowance available, 15 WCUs and 15 RCUs in this region cost approximately `15 × ($0.00065 + $0.00013) × 24 × 30 = $8.424` per 30-day month, before table storage or other features. Idle queues and Lambda kill switches do not stop those table-capacity charges. These rates and the free-capacity treatment appear in the [DynamoDB provisioned pricing example](https://aws.amazon.com/dynamodb/pricing/).

## Ways forward

1. **Strict $0 now:** keep using local simulation, offline adapter tests and the mocked AWS dashboard workflow. Do not deploy this stack yet.
2. **Credits-covered cloud demo:** obtain eligible credits or an approved school/lab account, then verify remaining credit, expiry, service coverage and sandbox permissions. A lab may not permit this stack's services or IAM roles.
3. **Small paid cloud demo:** explicitly accept nonzero usage and the absence of an automatic hard cap, review a complete usage/retention estimate and exact Terraform plan, then run a short synthetic demonstration. Retained resources must be reviewed afterward. No spend tolerance or deployment is authorized by this worksheet.

Fresh repository defaults are minimal/idle and use a one-cent budget warning when email notifications are configured. The dashboard refreshes manually. These controls reduce avoidable usage; they do not make a billable architecture free. See [cost controls](aws-cost-controls.md) and [account readiness](aws-deployment.md#read-only-account-readiness).
