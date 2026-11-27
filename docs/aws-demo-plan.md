# Proposed paid AWS demonstration

Prepared November 19, updated November 27, 2026. **The user accepted the $1 project tolerance, approved the quota request, and approved/applied the initial bounded IAM role-management grant. Service permission grants, actual deployment and deletion remain unapproved.** The accepted tolerance replaces the previous strict-$0 cost decision for this proposed single demo; it does not authorize applying infrastructure before review. This workload proposal accompanies a separately saved November 27 idle plan; neither is approved for apply.

## Recommendation

Use one minimal-profile sandbox in `us-east-1`, at most 25 synthetic messages and two separate application identities. Target three total resource-hours: one for setup, one for the active demonstration and one for cleanup. Resolve quota, access, notification and cleanup prerequisites before starting that clock. Do not provision resources while waiting for those prerequisites.

The user accepted **$1 of project spending tolerance** for this single demonstration, acknowledging that AWS does not enforce that amount as a cap. The gross model is approximately $0.06 illustrative / $0.20 with stressed runtimes for three hours. The margin is for planning; unexpected traffic, retries, setup failures or delayed cleanup can exceed it. This is not a prepaid deposit or permission to leave the stack running.

## Current read-only findings

The refreshed private report is `.data/aws-readiness-current.json`, refreshed November 27, 2026. Account identifiers and raw results remain ignored.

| Check | Observed result | Consequence |
| --- | --- | --- |
| Identity | Exact configured non-root development user/account matched | Continue preparation with that profile; do not use root credentials. |
| MFA | One or more MFA devices registered | Registration alone does not prove the current session used MFA. |
| Account plan | `ResourceNotFoundException` | Plan status/eligibility remain unknown. Verify service access and billing plan in the console before apply; do not upgrade automatically. |
| Free Tier usage | Read succeeded; it does not establish complete cost coverage | Deduct no free allowances or credits from this proposal. |
| Regional DynamoDB | Complete listing, no tables, zero provisioned RCU/WCU | Base stack adds 15 RCU + 15 WCU. Other regions/services and account-wide charges were not inventoried. |
| Regional Lambda | No functions; total and unreserved concurrency both 1,000 | Five active worker reservations fit; recheck before activation. |
| Deployment access | Inspection and the approved exact-role management grant; generated service bundle lacks API, pool and mapping bindings | An authorized administrator must review bootstrap, scoped grants and teardown access. No new effective-policy inventory or IAM simulation was performed by this check. |

This narrow inspection is not a complete resource inventory or billing audit. It does not prove there are no unrelated resources or charges.

The table above records the November 27 read-only refresh. On November 25, a separate live IAM review and explicit approval established the [initial exact-role management grant](aws-role-access.md). The developer is no longer inspection-only, but service provisioning and controlled bootstrap remain incomplete. Runtime roles are still absent and the new ceiling is not attached to the developer as an identity permission policy.

## Approved quota request — applied capacity verified

Keep the existing consumer reservation of four and replay reservation of one. The arithmetic requirement for this fresh base stack is 105: five reserved plus AWS's required 100 unreserved. However, on November 25 the AWS console rejected 105 because its request form requires at least the default quota of **1,000**. The user explicitly approved the revised target of 1,000 before submission. This raises the requested capacity ceiling; it does not reserve that capacity or activate workers.

Request status observed in the intended account's `us-east-1` console on November 25, 2026:

| Field | Observed value |
| --- | --- |
| Service / quota | AWS Lambda / Concurrent executions (`L-B99A9384`) |
| Requested value | 1,000 |
| Request status at submission | Pending on November 25 |
| Applied capacity verified November 27 | 1,000 total; 1,000 unreserved |
| Applied value before submission | 10 |
| Support case | None displayed yet |

The earlier developer CLI attempt returned `AccessDeniedException`; that history remains private in `.data/aws-quota-request-review.json`. The existing desktop console session could read the quota after refresh. Its request history was checked for duplicates, the approved 1,000 request was submitted, and the resulting history row showed **Pending**. No IAM policy was added or changed. The new observation is recorded privately in `.data/aws-quota-console-request.json`.

November 27: the refreshed developer session read the actual Lambda account settings: **1,000 total and 1,000 unreserved concurrency**, with no functions. The five reserved worker slots fit the current capacity. This verifies applied capacity rather than assuming the submitted request succeeded. The private current report is `.data/aws-readiness-current.json`. No duplicate request is needed. Recheck capacity before worker activation and continue reviewing scoped bootstrap/service and cleanup access. No plan upgrade or deployment is approved by this quota change.

[AWS documents the 100-unit unreserved requirement, zero-concurrency stop control and no added charge for reserved concurrency](https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html). Reserved concurrency differs from paid provisioned concurrency. [AWS's quota-request procedure](https://repost.aws/knowledge-center/lambda-concurrency-limit-increase) describes submitting and awaiting a decision through Service Quotas.

## Resources and settings to review

The minimal base stack contains five Lambda functions, six execution roles including the workflow role, four standard SQS queues, two initially disabled event mappings, three small provisioned DynamoDB tables, one private/versioned evidence bucket, Cognito pool/client/domain/groups, an HTTP API, one Standard state machine, six log groups and an SNS topic. Related policies/configuration are separate Terraform resources. The existing offline checker expects 62 managed resources without notification/budget resources, or 64 when the reviewed email enables both. The November 27 real saved idle plan contains 64 creates, no updates and no deletions, matching the notification/budget configuration. It passes selected checks; full manual policy/cost review remains outstanding.

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

November 27: [preparation and real-plan checks](aws-plan-review.md) completed with deterministic Lambda packaging, 230 Python tests and 32 mocked infrastructure plans passing. The unchanged frontend last passed 33 tests. The real saved idle plan passes selected checks with 64 creates and no updates/deletions. Private inputs use dashboard callback `http://localhost:8767/` and CLI callback `http://localhost:8765/callback`, preserving current local dashboard settings. Callback ownership/port availability still need review. Canonical state was kept in scope; no apply occurred. Live identity and quota are verified, while billing-plan eligibility and service/bootstrap grants remain unresolved.

1. The paid tolerance and quota submission are approved. Independently verify the account billing plan permits the required services. Any plan upgrade is a separate decision.
2. Applied capacity is verified at 1,000. Recheck before enabling workers; do not create a waiting idle stack.
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
- Submitted with explicit user approval: regional concurrency target 1,000, after the console rejected 105 as below its minimum. Applied total and unreserved capacity were both verified at 1,000 on November 27.
- Applied with explicit approval: create the administrator-owned runtime ceiling and exact-role delegation managed policies, and attach only the delegation policy directly to the developer. Console verification confirmed the new attachment. No runtime role or service resource was created.
- Review the remaining service/bootstrap/user/seeding/cleanup grants and later the saved deployment and destroy plans. These remain unapproved and unapplied.

Until those steps are resolved, continue offline preparation and leave AWS workers/resources undeployed. Account-specific policy documents, plan binaries/JSON, email addresses, IDs and runtime evidence stay in ignored files.
