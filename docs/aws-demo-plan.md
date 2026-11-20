# Proposed paid AWS demonstration

Prepared November 19, 2026. **The user accepted the $1 project tolerance and approved the quota request. Permission grants, actual deployment and deletion remain unapproved.** The accepted tolerance replaces the previous strict-$0 cost decision for this proposed single demo; it does not authorize applying infrastructure before review. This is a readiness/workload proposal, not an actual saved Terraform plan.

## Recommendation

Use one minimal-profile sandbox in `us-east-1`, at most 25 synthetic messages and two separate application identities. Target three total resource-hours: one for setup, one for the active demonstration and one for cleanup. Resolve quota, access, notification and cleanup prerequisites before starting that clock. Do not provision resources while waiting for those prerequisites.

The user accepted **$1 of project spending tolerance** for this single demonstration, acknowledging that AWS does not enforce that amount as a cap. The gross model is approximately $0.06 illustrative / $0.20 with stressed runtimes for three hours. The margin is for planning; unexpected traffic, retries, setup failures or delayed cleanup can exceed it. This is not a prepaid deposit or permission to leave the stack running.

## Current read-only findings

The refreshed private report is `.data/aws-readiness-current.json`, inspected November 19, 2026 Eastern time. Account identifiers and raw results remain ignored.

| Check | Observed result | Consequence |
| --- | --- | --- |
| Identity | Exact configured non-root development user/account matched | Continue preparation with that profile; do not use root credentials. |
| MFA | One or more MFA devices registered | Registration alone does not prove the current session used MFA. |
| Account plan | `ResourceNotFoundException` | Plan status/eligibility remain unknown. Verify service access and billing plan in the console before apply; do not upgrade automatically. |
| Free Tier usage | Read succeeded; it does not establish complete cost coverage | Deduct no free allowances or credits from this proposal. |
| Regional DynamoDB | Complete listing, no tables, zero provisioned RCU/WCU | Base stack adds 15 RCU + 15 WCU. Other regions/services and account-wide charges were not inventoried. |
| Regional Lambda | No functions; total and unreserved concurrency both 10 | Current active worker reservations do not fit. |
| Deployment access | Existing reviewed access is for inspection; generated service bundle lacks API, pool and mapping bindings | An authorized administrator must review bootstrap, scoped grants and teardown access. No new effective-policy inventory or IAM simulation was performed by this check. |

This narrow inspection is not a complete resource inventory or billing audit. It does not prove there are no unrelated resources or charges.

## Approved quota request — submission denied

Keep the existing consumer reservation of four and replay reservation of one. Request a regional **Concurrent executions quota of 105**, the minimum arithmetic requirement for this fresh base stack: five reserved plus AWS's required 100 unreserved. This does not mean the demo intends 105 concurrent invocations. Recheck actual unreserved capacity before activation, especially if other workloads appear.

Proposed request text:

> Development-only QueueRescue shipment recovery sandbox in us-east-1. Request total concurrent executions of 105. Current quota is 10. The sandbox requires reserved concurrency 4 for one consumer shared by two SQS mappings, and 1 for bounded replay, leaving 100 unreserved. Initial workload is limited to 25 synthetic messages. Polling and replay remain disabled outside the reviewed demonstration. No provisioned concurrency is requested.

The user approved submission from 10 to 105. The exact reviewed non-root identity was rechecked; both quota discovery and a single explicit request returned `AccessDeniedException`. No quota request was accepted and no permissions changed. The attempt/result is retained privately in `.data/aws-quota-request-review.json`.

An authorized administrator can complete the already-approved request without changing the developer's permissions:

1. Open [Lambda Concurrent executions in us-east-1 Service Quotas](https://us-east-1.console.aws.amazon.com/servicequotas/home?region=us-east-1#!/services/lambda/quotas/L-B99A9384) in the intended account. Complete sign-in/MFA directly with AWS.
2. Confirm service **AWS Lambda**, quota **Concurrent executions**, region **us-east-1** and current value **10**. Stop if the account, quota or value differs from the reviewed evidence.
3. Select **Request increase at account level**, enter **105**, and submit. Use the request text above if AWS asks for justification. Keep the request ID/account details private; report its status.
4. Wait for AWS's decision, then verify the applied value with a fresh readiness check before activating workers. A pending request is not an applied increase.

The quota identifier is also listed in [AWS's quota reference](https://github.com/aws/tools-for-devops-agent/blob/main/skills/service-quota-check/references/common-quota-codes.md). AWS can decline or require a different request value. Do not silently change the target, attach broader permissions or remove concurrency controls if it fails. This handoff does not approve a plan upgrade or deploy resources.

[AWS documents the 100-unit unreserved requirement, zero-concurrency stop control and no added charge for reserved concurrency](https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html). Reserved concurrency differs from paid provisioned concurrency. [AWS's quota-request procedure](https://repost.aws/knowledge-center/lambda-concurrency-limit-increase) describes submitting and awaiting a decision through Service Quotas.

## Resources and settings to review

The minimal base stack contains five Lambda functions, six execution roles including the workflow role, four standard SQS queues, two initially disabled event mappings, three small provisioned DynamoDB tables, one private/versioned evidence bucket, Cognito pool/client/domain/groups, an HTTP API, one Standard state machine, six log groups and an SNS topic. Related policies/configuration are separate Terraform resources. The existing offline checker expects 62 managed resources without notification/budget resources, or 64 when the reviewed email enables both. These are source/fixture expectations, not an actual account plan count.

| Setting | First apply | During approved demo | After demo |
| --- | --- | --- | --- |
| `cost_profile` | `minimal` | `minimal` | Same until teardown |
| `consumers_enabled`, `replay_enabled` | Both `false` | Both enabled only in the reviewed active plan | Both `false`; verify mappings and reservations |
| Audit archive/capture and dashboard hosting/delivery | All `false` | All `false` | All `false` |
| `protect_data`, `allow_destroy_evidence` | `true`, `false` | `true`, `false` | Change only under separately reviewed destruction plan |
| External execution boundary | Administrator-owned exact reviewed ARN | Unchanged | Retain until roles are removed through reviewed teardown |
| Notification email / budget | Private reviewed endpoint; proposed existing $0.01 warning threshold | Verify delivery | Review removal with remaining stack |
| Dashboard | Separate AWS-mode build/server in ignored storage | Local browser connected to real Cognito/API | Stop only owned demo server |

Keep the current local application, database, settings and `dist/` untouched. The AWS dashboard build must use separate output and an available loopback port; verify matching Cognito callbacks/CORS before first apply. No CloudFront, custom KMS key, CloudTrail profile, NAT, EC2 or provisioned concurrency is included.

## Cost scenarios and stop conditions

Recomputed with [the existing offline calculator](../tools/estimate_aws_demo.py), using its November 15 regional rate snapshot and documented workload. No allowances/credits are subtracted; tax and unrelated account charges are excluded. Selected primary pricing pages were rechecked during proposal preparation, but this does not claim a new complete price-list snapshot or a guaranteed quote. Full assumptions, service line items and sources are in [the cost assessment](aws-cost-assessment.md).

| Complete resource lifetime | Illustrative USD | Stressed-runtime USD |
| --- | ---: | ---: |
| 3 hours, target | 0.06345450 | 0.19955518 |
| 6 hours, cleanup delay | 0.09856608 | 0.23466676 |
| 24 hours, overnight retention | 0.30923556 | 0.44533624 |
| 7 days | 1.99454259 | 2.13064327 |
| 30 days | 8.45484878 | 8.59094946 |

The longer scenarios retain the stack but do not repeat demo traffic. They are not upper bounds. Three tables alone add $0.01170 per hour before allowances under this model; workers off does not stop those capacity charges. [DynamoDB provisioned pricing](https://aws.amazon.com/dynamodb/pricing/) explains this capacity billing.

Record an actual UTC start, active-stop and cleanup deadline in ignored review notes immediately before apply. Stop new workload and reassess if time exceeds the active hour, collection exceeds ten attempts, Lambda invocations exceed 570, API billed units exceed 400, SQS units exceed 10,000 or workflow transitions exceed 100. These are operational review thresholds from the model, not automatic or always immediately observable limits. Do not wait for delayed billing data to stop activity. Repeated failure tests that exceed the original workload need a revised estimate.

Begin cleanup by the end of the active hour and target complete removal within three total hours. If cleanup fails, keep workers off, inventory residual resources, recalculate lifetime and report the ongoing exposure immediately. A budget email or a timer cannot guarantee a $1 bill.

## Access, deployment and validation order

1. The paid tolerance and quota submission are approved. Independently verify the account billing plan permits the required services. Any plan upgrade is a separate decision.
2. Resolve the quota request and verify approved capacity through a fresh read. Do not create a waiting idle stack.
3. Review the private [runtime boundary/delegation](aws-role-access.md) and [service policy bundle](aws-service-access.md). An administrator must review exact role ownership, policy contents, all effective grants and account-wide API log delivery. Initial API/pool/mapping IDs require a controlled bootstrap and later verified bindings. Do not invent IDs, create resources ad hoc, or attach AdministratorAccess to bypass errors.
4. Establish reviewed application-user creation, bounded seeding and teardown permissions as well as provisioning access. The generated service bundle deliberately omits these. Agree on deletion of only this demo's synthetic resources/data after exporting masked results; actual destroy approval remains separate.
5. Confirm the private notification endpoint and distinct Operator/Approver accounts. Verify delivery when provisioned; keep workers disabled until it works. The first apply can itself incur charges, so its concrete review must include this setup exposure.
6. Rebuild deterministic artifacts offline, record source/package hashes, prepare the real saved **idle** Terraform plan, and run [selected plan checks](aws-plan-review.md). Review its exact resources, trust/IAM/resource policies, account/region, callbacks, retention and cleanup access. User review of the concrete saved plan is required before apply; this document does not replace it.
7. Apply only that approved idle plan. Inspect actual resources and sign-in/role behavior; review the active change before enabling consumers/replay. Seed **once, at most 25 synthetic messages**. Source retry/dead-lettering can take roughly nine minutes plus polling. Collection samples are not guaranteed complete inventory.
8. Through the separate AWS-mode browser, prove masked inspection, explicit selection, dry run, expiry/hash binding, rejection of self-approval, separate approval, bounded replay, and business verification. Record actual decisions/counts rather than assuming all 25 are recoverable. Keep password/MFA steps with the user.
9. Prepare a controlled uncertain-send/worker-failure exercise with exact target, expected durable state, recovery action and workload allowance before executing it. Existing local SIGKILL tests and source timeout-after-effect seeds do not prove a live replay-worker crash. Do not introduce an unreviewed production fault hook or claim a successful interruption test without evidence. Verify no duplicate business effect, pause/cancel and late verification.
10. Stop activity, reconcile in-flight work, verify mappings/replay off and workflows ended, export masked evidence, and prepare the exact destroy plan. Obtain the resource/data-loss review before executing cleanup. Verify residual resources and later billing updates; report observed usage separately from estimates.

## Cleanup review

The intended retention choice is to retain curated masked results locally/GitHub and remove this sandbox's synthetic cloud data and all chargeable demo resources. That is a proposal, not deletion permission. Deletion protection and `force_destroy=false` intentionally prevent unattended complete removal.

The concrete review must cover all S3 versions/delete markers/multipart uploads, three tables, pool/users, queues, API, functions/mappings, workflow, logs, roles/policies and notification/budget resources. Verify names, ownership and Terraform state before deleting anything. Preserve unrelated resources and the existing local demo. Export evidence before disabling protection; never remove a boundary merely to bypass a permissions failure. See [retention and teardown](aws-deployment.md#retention-and-teardown).

## Approval status and remaining reviews

- Accepted: $1 project tolerance for this one demo, understanding it is not an AWS cap.
- Approved: submitting the regional concurrency request from 10 to 105. The developer's submission was denied; administrator submission and AWS approval remain outstanding.
- Review exact administrator bootstrap/grants and later the saved deployment and destroy plans. None has been approved or applied.

Until those steps are resolved, continue offline preparation and leave AWS workers/resources undeployed. Account-specific policy documents, plan binaries/JSON, email addresses, IDs and runtime evidence stay in ignored files.
