# AWS sandbox deployment and verification

The AWS backend, dashboard integration, and infrastructure are implemented and tested offline. This is not evidence of a successful deployment into your account. The React dashboard defaults to local simulation; [configure AWS dashboard mode](dashboard-aws.md) after deployment, or use the authenticated AWS console helper below. Development account inspection uses a local temporary-credential profile; passwords and keys must not be shared or committed.

For a demo constrained by Free Tier eligibility, first read [cost controls](aws-cost-controls.md). A fresh sandbox can select `cost_profile="minimal"`, which keeps service-owned encryption while removing customer-managed KMS keys, paid backups, and metric alarms. Check the account's plan, credits, and shared usage before deployment; there is no guaranteed $0 cap. Leave `consumers_enabled=false` and `replay_enabled=false` until an intentional demo is ready.

The optional [CloudTrail and protected audit archive](cloud-audit.md) is prepared offline and disabled by default. Its scoped profile requires `us-east-1`, a separate cost/retention review, one more active Lambda reservation, confirmed notifications and live verification. Keep `audit_logging_enabled=false` and `audit_collection_enabled=false` until that review. Once provisioned, pause with collection false while retaining provisioning true; the protected bucket deliberately blocks generic destruction.

## Inputs needed before deployment

- A development AWS account ID and region. The default example region is `us-east-1`; the stack rejects production environment labels and unexpected account IDs.
- A local temporary-credential profile, normally `queuerescue-dev`, using a non-root identity. Do not send passwords, access keys, or session tokens in chat or add them to Git.
- A budget alert email and an account-wide monthly notification threshold. The example value is configuration, not a cost estimate. Budgets are delayed notifications, not a hard spending cap.
- Two different email identities for the Operator and Approver. No application users or administrator memberships are created automatically.
- Permission to create the listed sandbox resources and scoped IAM roles. Your account owner controls these permissions; no permanent vendor administrator key is required.

The optional standard profile creates five Lambda functions, four standard SQS queues, three encrypted on-demand DynamoDB tables, a versioned encrypted S3 bucket, a customer-managed KMS key, a Cognito user pool/client/groups, an authenticated HTTP API, a Standard state machine, log groups, alarms, and an SNS topic. A budget and email subscription are created only when an alert email is configured. KMS, alarms, point-in-time recovery, storage, and usage can incur charges even with little traffic. There is no NAT gateway, ECS cluster, EC2 instance, or hosted frontend.

That resource list describes the standard profile. The default minimal profile uses fixed provisioned DynamoDB capacity, AWS-owned/service-owned encryption, and no customer-managed key, PITR, or metric alarms. Consumers and replay default off. It retains authenticated access, deletion protection, audit records, and controlled replay. Neither profile is a cost guarantee.

## Account-free build and checks

From the repository root, with Python 3.10+ and Terraform 1.17.2:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-aws-test.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/package_aws.py
terraform -chdir=infra/aws init -backend=false -input=false -lockfile=readonly
terraform -chdir=infra/aws fmt -check -recursive
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws test
```

The infrastructure tests use `mock_provider`; they never create AWS resources or authenticate to a customer account. The provider and runtime dependencies are pinned. The deterministic zip is stored in ignored `infra/aws/.build/`; it includes only runtime source and SDK dependencies. It excludes credentials, state, tests, the Word specification, and local databases.

## Authenticate and review deployment

Configure your non-root local AWS identity first. A recent AWS CLI supports browser-based temporary-credential login; accounts using IAM Identity Center use `aws configure sso` / `aws sso login` instead.

```sh
aws login --profile queuerescue-dev
aws sts get-caller-identity --profile queuerescue-dev
```

Confirm the account and identity yourself. Copy `infra/aws/terraform.tfvars.example` to `infra/aws/terraform.tfvars` and enter your account, profile, region, email, and budget threshold. The local values file, state, saved plans, and deployment outputs are ignored by Git. Keep Terraform state backed up securely; migrate it to an encrypted, access-controlled remote backend before collaboration or production use.

Browser-based `aws login` requires AWS CLI 2.32.0 or newer. Update an older CLI before using that path; IAM Identity Center users may use the supported SSO flow. No access keys need to be shared in chat.

Check `aws --version` before starting. If a newer CLI has been prepared in the ignored project tools folder, run `.data/bin/aws --version` and use `.data/bin/aws` in place of `aws` for these commands. This does not change the system CLI or your shell configuration. See the [official installation instructions](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) for a normal installation or update.

For the console-based login flow, the non-root IAM user or role needs `SignInLocalDevelopmentAccess`, in addition to its separately reviewed deployment permissions. That policy enables temporary local sign-in; it does not grant permission to deploy the stack. If you only have the account's root login, establish a non-root development identity before continuing. See [AWS's browser login prerequisites](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html). Do not approve a root session for this project.

A new user with only `SignInLocalDevelopmentAccess` and `IAMUserChangePassword` can sign in but cannot list IAM users or deploy resources. An `iam:ListUsers` denial on an IAM landing page is expected for that initial setup; do not recreate the user or add administrator access just to clear the page. Complete CLI login with the existing non-root session, then use `sts get-caller-identity` to verify it. AWS documents that [GetCallerIdentity requires no permissions](https://docs.aws.amazon.com/STS/latest/APIReference/API_GetCallerIdentity.html). MFA enrollment and project deployment permissions still need to be configured through an authorized account administrator before deployment.

This developer login is separate from `python -m tools.aws_console login`, which signs an application Operator or Approver into Cognito after deployment. The next account milestone is to verify the developer identity and cost eligibility, then review a Terraform plan. Preparing a plan does not deploy resources.

### Read-only account readiness

Before granting deployment permissions, generate a narrow inspection policy offline:

```sh
python3 -m tools.aws_readiness policy --account-id YOUR_ACCOUNT_ID
```

The generated `.data/aws-readiness-policy.json` grants seven read operations: the selected IAM user's identity and MFA registrations; account Free Tier plan/usage; and regional DynamoDB provisioned-capacity metadata and Lambda account limits. It cannot read table items, create resources, manage IAM, upgrade an account plan, or run CloudShell. An authorized administrator can review it and add it as an inline policy named `QueueRescueReadiness` on the development user. Use the console's JSON editor; do not commit the account-specific policy. Adding this access is a separate, explicit permission change.

With the temporary development profile signed in, collect private evidence:

```sh
python3 -m tools.aws_readiness check --account-id YOUR_ACCOUNT_ID
```

The default CLI path is `.data/bin/aws`; pass `--aws-cli aws` for a normal installation. The command first rejects root, unexpected accounts, and unexpected IAM users. It uses only an explicit read-operation allowlist and never invokes Cost Explorer, deployment, or permission-changing APIs. Errors are recorded as unknown, and truncated capacity/offer results are marked incomplete. The report is saved to ignored `.data/aws-readiness.json` with mode 0600. Only check statuses are printed.

Review the report alongside the billing console. Registering an MFA device does not prove an existing CLI session used MFA; sign in again after enrollment. Missing Free Tier offers are not proof of eligibility, and regional capacity totals do not establish account-wide allowances. In particular, [API Gateway's legacy free usage expires after 12 months](https://aws.amazon.com/api-gateway/pricing/). This tool always leaves `deployment_ready=false`: human cost/permission review remains necessary. It does not apply Terraform or guarantee a $0 bill.

For an active audited demo, add `--audit-logging-enabled` to the readiness check so it includes the archive writer's extra reserved unit (six total). This does not authorize provisioning or capture.

The Lambda capacity section also checks whether a fresh/idle stack can allocate the base active demo's five reserved concurrency units while leaving AWS's required unreserved pool available. Resolve an insufficient quota before enabling consumers or replay; idle settings use zero reservations and no positive SQS mapping concurrency setting. A `ResourceNotFoundException` from the account-plan API remains unknown eligibility, even if other reads succeed; it is not permission to deploy.

Build the Lambda package, then prepare a saved plan:

```sh
.venv/bin/python tools/package_aws.py
terraform -chdir=infra/aws init -input=false -lockfile=readonly
terraform -chdir=infra/aws plan -out=reviewed.tfplan
```

Review the exact resource creation, permissions, target account, deletion protection, and notification settings. Only after that review, deploy the saved plan:

```sh
terraform -chdir=infra/aws apply reviewed.tfplan
mkdir -p .data
terraform -chdir=infra/aws output -json deployment > .data/aws-deployment.json
```

No CI job applies Terraform or holds AWS credentials. Confirm the SNS email subscription if email notifications were configured. Budget notifications use the supplied account-wide threshold, so existing account spending also contributes.

## Create separate operator and approver identities

Using the Cognito console or your authenticated AWS CLI, create two users in the output user pool. Add one to the `Operator` group and the other to `Approver`; use `Viewer` for read-only inspection. Keep initial passwords and password changes in AWS's sign-in flow. The prepared pool requires software-token MFA: complete TOTP enrollment in hosted sign-in, then verify a subsequent sign-in challenges for the second factor. AWS has first-sign-in verification exceptions; do not count the enrollment session as proof of a subsequent MFA challenge. The public Cognito client uses authorization code + PKCE and has no client secret; API Gateway requires a scoped access token and the Lambda separately enforces group capabilities. This configuration has only been tested offline; existing pools require a reviewed Terraform apply.

Application approval compares Cognito `sub` identities. An Admin cannot self-approve a plan they created. The local demo identity selector does not grant AWS permissions and is never used by this backend.

## Seed a bounded synthetic incident

The helper verifies the AWS account and rejects root before writing. Each seed run uses unique operation IDs and conditional initial business records. It never overwrites an existing shipment.

If activity was disabled during initial setup, review and apply `consumers_enabled=true` and `replay_enabled=true` only after confirming the account's cost/Free Tier constraints. Disable the switches again after the bounded demonstration.

```sh
.venv/bin/python -m tools.aws_console seed --profile queuerescue-dev --count 20
```

About one in ten events has UNKNOWN business state; another uses a timeout after the shipment effect; the rest time out before the effect. The source Lambda returns partial batch failures. With a 180-second visibility timeout and three receives before dead lettering, allow several minutes for failures to reach the DLQ; the full path can take roughly nine minutes plus polling. Completed-but-timed-out source events continue failing until dead lettering so the demonstration can show why blind replay is unsafe.

## Sign in and inspect AWS snapshots

```sh
.venv/bin/python -m tools.aws_console login
```

Complete Cognito sign-in as Operator in the browser. The callback binds only to loopback port 8765 and checks a random OAuth state; the code exchange uses PKCE. Only the access token is saved to ignored `.data/aws-token.json` with mode 0600. No passwords or refresh tokens are saved. `login --no-browser` prints the authorization URL if you prefer a private window or a separate browser profile. Sign in again after token expiry.

Create `.data/collect.json` containing:

```json
{"acknowledgeVisibilityChange": true}
```

Then run:

```sh
.venv/bin/python -m tools.aws_console call POST /collections --body .data/collect.json
.venv/bin/python -m tools.aws_console call GET /messages
```

Collection invokes a separately scoped Lambda. It samples at most ten DLQ messages, snapshots their bodies and attributes, and releases their visibility lease. It does not delete originals. Repeated sampling may return already indexed messages; it is not a guarantee of a complete queue inventory. Pagination uses opaque `nextCursor` values, e.g. `--query '{"limit":10,"cursor":"RETURNED_CURSOR"}'`. Message details are available at `/messages/MESSAGE_ID` and return a masked payload.

The inbox supports server-side metadata filters and incident groups at `/incidents` and `/incidents/INCIDENT_ID/messages`. Empty bounded-search pages can have a continuation; preserve filters when following it. Groups describe indexed schema categories and collection windows, not confirmed consumer exceptions. See the [investigation contract](cloud-investigation.md) for bounds, retention and legacy-snapshot behavior.

Retention warnings read only the configured DLQ's retention period/ARN, once per message-list/member-list read or successful snapshot inspection. They estimate expiry from original standard-SQS enqueue time and keep original presence unverified. Snapshot access has a separate enforced deadline, with safe errors for expired, missing or inaccessible evidence. The prepared API role adds only scoped `sqs:GetQueueAttributes`; review this permission with the deployment plan. See the [retention contract](cloud-retention.md).

## Plan, approve, execute, and verify

Create `.data/plan.json` with explicit indexed snapshot IDs, a controlled rate, and no arbitrary destination:

```json
{"messageIds": ["REPLACE_WITH_SNAPSHOT_ID"], "rate": 5}
```

```sh
.venv/bin/python -m tools.aws_console call POST /replay-plans --body .data/plan.json
.venv/bin/python -m tools.aws_console call GET /replay-plans/PLAN_ID
```

The first sandbox supports 1–25 messages per plan and 1–25 messages/second as a maximum send rate. It may send more slowly because of orchestration overhead. Plans bind the exact body/evidence checksums, deployment package version, destination, decisions, and one-hour expiry. Missing proof, schema errors, conflicting attributes, oversized bodies, and unknown/in-progress outcomes are excluded; completed operations are skipped.

Sign out of the Operator's Cognito session and use the separate Approver identity. A private browser profile with `login --no-browser` helps keep identities distinct. Create `.data/approve.json`:

```json
{"planHash": "REPLACE_WITH_EXACT_PLAN_HASH", "note": "Reviewed business outcomes and bounded replay rate."}
```

```sh
.venv/bin/python -m tools.aws_console call POST /replay-plans/PLAN_ID/approve --body .data/approve.json
```

Sign back in as Operator. Create `.data/start.json` with `{"approvedPlanId":"PLAN_ID"}` and start:

```sh
.venv/bin/python -m tools.aws_console call POST /replay-jobs --body .data/start.json
.venv/bin/python -m tools.aws_console call GET /replay-jobs/JOB_ID
```

The Standard workflow invokes bounded replay chunks. The replay role can send only to the dedicated replay queue. Conditional operation reservations prevent overlapping jobs. Each send has a durable intent and unique replay identity before SQS is called; original body bytes and attributes are preserved. A send timeout or worker error pauses for reconciliation rather than blindly retrying.

Create `.data/verify.json` with `{"jobId":"JOB_ID"}`:

```sh
.venv/bin/python -m tools.aws_console call POST /verifications --body .data/verify.json
.venv/bin/python -m tools.aws_console call GET /audit
```

Only domain-confirmed shipments are VERIFIED. Accepted sends without completion proof remain UNVERIFIED; late proof can move PARTIAL to COMPLETED. The tool retries ambiguous HTTP failures with the same idempotency key. API Gateway may time out before a longer Lambda finishes; reuse the same request key to recover the committed response, rather than generating a new logical mutation. To explicitly retain a key across separate CLI invocations, pass `--key YOUR_UNIQUE_REQUEST_KEY`.

Pause, resume, and cancel use POST `/replay-jobs/JOB_ID/pause`, `/resume`, and `/cancel` with an optional `reason`. Cancellation stops new scheduling and retains already accepted sends for verification. Reconciliation never automatically removes source messages or discards reservations for uncertain business effects.

## Kill switch and failure investigation

Set `replay_enabled=false`, review the plan, and apply it to set the replay function's reserved concurrency to zero. This blocks new invocations; an already running invocation may finish its bounded in-flight work. Source consumers are independent. Restore the switch only after investigating the incident.

Inspect Step Functions execution status, the job ledger, replay DLQ, and CloudWatch alarms. The send task has no broad automatic Retry. Its Catch path invokes conservative ledger reconciliation; retry is limited to safe reconciliation invocation failures. A workflow-level timeout cannot be caught by that task Catch, so it raises an alarm and requires operator reconciliation. If needed, an authenticated deployment operator can invoke the replay Lambda with `{"jobId":"JOB_ID","generation":0,"action":"recover"}` using the job's actual generation, then verify before resuming.

## Retention and teardown

Evidence objects and expired index/request records have a short retention policy. DynamoDB TTL is eventual; expired snapshots are also rejected in application checks. Business idempotency and audit tables do not have evidence TTL. Audit application roles can append/read but not update/delete; this is not protection against account administrators. The default profile leaves the optional archive absent. The [opt-in audit profile](cloud-audit.md) prepares a separate Governance-retained bucket and scoped trail, with administrator bypass and explicit capture gaps; it is not an immutable external vault. If provisioned, follow that runbook before any generic teardown: its bucket intentionally blocks destruction.

Deletion protection is on for tables and the user pool; S3 evidence is not force-deleted by default. Teardown therefore requires a deliberate choice about exports and evidence destruction. Review a destroy plan and its retained-data implications before disabling `protect_data` or enabling `allow_destroy_evidence`. Scheduled KMS deletion can eventually make retained encrypted data unreadable; retain the key with any retained evidence. Do not assume a failed partial destroy leaves all other resources unchanged. Check the account afterward for remaining KMS keys, tables, storage, alarms, and budget resources that can continue to incur charges.

```sh
terraform -chdir=infra/aws plan -destroy -out=reviewed-destroy.tfplan
```

Do not apply that plan until the data-retention decision and protection settings have been reviewed. This implementation has not performed a deployment or teardown in your account.

## Remaining AWS milestones

Structured telemetry and exact-message/attempt CloudWatch lookup are implemented and verified offline; see the [log-evidence contract](cloud-log-evidence.md). Real-account log delivery, scoped IAM access and console deep links still require live validation. Real account and browser sign-in/CORS validation, large-volume inventory/replay, manual resolution, automated billing attribution and stronger administrator separation for audit archives remain future work. The browser integration is implemented but has not been exercised against a live deployed stack. The current offline tests and mocked Terraform plans do not prove real IAM propagation, Cognito email delivery, service quotas, account policy compatibility, or actual AWS cost.

Incident-scoped [reports](cloud-incident-reports.md) are implemented offline with JSON/Markdown export, indexed recovery evidence and explicit history limits. Cost observations require an application Admin and retain declared source/window/hash provenance; the application does not independently validate a bill or allocation. The initial Operator/Approver demo accounts do not gain Admin automatically. Live source/authorization and actual measured-cost attribution remain deployment-validation tasks.
