# Scoped AWS service policy preparation

Status: service policies are implemented and tested offline; none has been granted. The separate [initial IAM role-delegation grant](aws-role-access.md) was explicitly approved and applied on November 25. Actual service provisioning authorization, administrator bootstrap, quota resolution and grant/apply review remain outstanding.

## Scope and limits

`tools/aws_service_access.py` prepares S3 bucket configuration, three DynamoDB tables, four SQS queues, five Lambda functions, the recovery state machine, six log groups and the SNS alert topic using exact account/region/environment resource names. It complements the separate [IAM role-access proposal](aws-role-access.md), which supplies the bounded role creation and passing permissions. The service documents contain no IAM administration or PassRole grant.

Cognito user pools, API Gateway APIs and Lambda event mappings have AWS-assigned IDs. Without independently reviewed bindings, their configuration grants are omitted and reported as missing. The generator never substitutes account-wide write access for a missing ID. Once bound, API access covers the exact API and its children; pool access covers the exact regional pool; mapping updates require both an exact UUID and the expected consumer function. API tag permissions use the exact URL-encoded API/stage resource ARNs.

This is deliberately **not a complete first-deployment bootstrap identity**. An authorized administrator must review how to create the initial pool, API and mappings under a controlled first apply, retain their Terraform state/addresses, and verify their ownership before delegation. Do not run ad hoc creation commands or invent IDs to make the report pass. Replacements that create new IDs need a new reviewed binding/grant cycle.

API Gateway access-log delivery can also require account-wide CloudWatch Logs operations, including `logs:PutResourcePolicy` and log-delivery configuration. These cannot all be restricted to one log-group ARN. The tool lists this administrator-controlled setup requirement instead of emitting wildcard write grants. Review the actual provider/service calls and account-wide policy impact before any first apply. Missing permission errors must be investigated; do not fix them by attaching AdministratorAccess. See [HTTP API logging prerequisites](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-logging.html).

The only delegated `Resource: "*"` statements are regional `logs:DescribeLogGroups` and `cognito-idp:DescribeUserPoolDomain` metadata reads, which lack resource-level authorization. Budgets uses an exact account/name ARN and the service's global endpoint model, without a regional condition. Other service grants are regional; S3 bucket maintenance additionally checks the resource owner. Bucket creation is restricted to the dedicated name, but a matching name is not proof of ownership of a pre-existing resource.

Direct S3 evidence-object operations, DynamoDB item operations, queue traffic/purges, function invocation and workflow execution are explicitly denied for the deployment identity on the selected resources. Destruction of the bucket, tables, functions, log groups, pool, API and state machine is not delegated. Configuration and child-resource maintenance still carry real powers: a trusted deployer can change application code, role usage, policies, routes, MFA/client settings, retention/TTL, lifecycle, backups and capacity. These permissions can affect project data, public exposure, availability and costs. Direct-data denials do not prevent a malicious code update from acting through a runtime role. This is scoped trusted-deployer access, not production administrator isolation.

Standard-profile KMS, optional audit/archive and CloudFront hosting are excluded. Application-user/group membership administration, demo seeding and destructive teardown also require separate reviewed access. The generated policies cannot be used as a generic destroy identity; teardown requires an administrator-reviewed retention/data-loss plan and suitable access before the demo begins.

## Generate and review

Without bindings, generate an explicitly incomplete private proposal:

```sh
python3 -m tools.aws_service_access --account-id YOUR_ACCOUNT_ID \
  --region us-east-1 --environment dev
```

For known resources, create an ignored `.data/aws-service-bindings.json` with the following shape. All values below are placeholders:

```json
{
  "account_id": "YOUR_ACCOUNT_ID",
  "region": "us-east-1",
  "environment": "dev",
  "cost_profile": "minimal",
  "audit_logging_enabled": false,
  "dashboard_hosting_enabled": false,
  "api_id": "ACTUAL_API_ID",
  "cognito_user_pool_id": "ACTUAL_REGIONAL_POOL_ID",
  "event_source_mapping_ids": {
    "source": "ACTUAL_SOURCE_MAPPING_UUID",
    "replay": "ACTUAL_REPLAY_MAPPING_UUID"
  },
  "notification_email": "YOUR_REVIEWED_NOTIFICATION_EMAIL"
}
```

The three resource-binding fields can be omitted while preparing a draft; the proposal remains incomplete. If `event_source_mapping_ids` is present, both distinct canonical UUIDs are required. Omit `notification_email` only when Terraform notifications are disabled. Otherwise the email must exactly match ignored Terraform settings. The optional profile fields accept only the supported minimal/base selection. The tool validates syntax and target consistency, not ownership or whether the inputs reflect actual Terraform/account state.

```sh
python3 -m tools.aws_service_access --account-id YOUR_ACCOUNT_ID \
  --region us-east-1 --environment dev \
  --bindings .data/aws-service-bindings.json
```

Both commands make no AWS calls. They atomically save `.data/aws-service-access.json` with private permissions, print only summary counts, and have no apply or credential option. Existing output is replaced; preserve an earlier proposal separately if needed. Account IDs, email addresses, live bindings, saved plans and review evidence remain ignored.

The bundle includes:

- `service_policies`: individual service fragments for review.
- `managed_policies`: the same statements packed into documents within IAM's 6,144-character managed-policy limit. Review attachment limits and existing grants before choosing how to install them; do not attach every service fragment as a separate policy by default.
- `missing_bindings`, `policy_bindings_complete` and `administrator_requirements`: unresolved resource IDs and first-apply requirements.
- `deployment_ready`: always false. Completing bindings is not authorization to grant access, apply Terraform or incur costs.

Select and review the nested policy documents; the entire JSON bundle is not an attachable IAM policy. An administrator must inspect all effective policies, trust/resource policies, the runtime boundary, existing resource ownership, region/global exceptions and current Terraform source before any grant. Never attach both the packed policies and duplicate individual fragments.

Email subscriptions are limited to the exact topic, `email` protocol and reviewed endpoint. No arbitrary endpoint subscription or SNS publish grant is included. The optional exact-budget grant uses `budgets:ModifyBudget`, which is coarse and can also delete that budget; it is an explicit review point. Budget alerts are delayed warnings, not spending caps. Confirm notification delivery separately.

## Verification and release order

November 25 preparation: the current private review packet is in ignored `.data/deployment-access-review/`. It contains the separately extracted runtime ceiling, IAM delegation document, two packed service documents, review notes and file/source hashes. All four fit the customer-managed document size limit, and 28 role/service policy tests passed. Three generated-ID binding categories remain missing.

The pre-grant live IAM console review confirmed the developer had only `IAMUserChangePassword`, `SignInLocalDevelopmentAccess` and inline `QueueRescueReadiness`, with no groups or user boundary. Their policy previews were read. The complete six-row role inventory contained none of the six proposed project roles; managed-policy search returned no QueueRescue matches. These observations are not an SCP/resource/session-policy simulation. Private `live-iam-review.json` records that baseline. The user subsequently approved the exact boundary creation, role-delegation policy creation and developer attachment; those three actions are complete and verified in the console. `role-grant-change.json` records the result. The two service policy documents remain unapproved and unattached; missing bindings and bootstrap are still blockers.

Tests exercise missing/partial bindings, account/environment/region isolation, exact API/pool/mapping scope, wrong principal/endpoint rejection, profile exclusions, direct-data separation, document packing and CLI behavior. A checked-in fixture contains selected public facts for 146 actions from AWS's [programmatic authorization reference](https://docs.aws.amazon.com/service-authorization/latest/reference/service-reference.html), reviewed November 18, 2026. It checks action names, resource-level support and emitted service-specific condition keys without network access in CI. It is not a complete IAM simulator, live-policy validation or a guarantee that every provider call is covered. Recheck references when changing the Terraform provider or selected resources.

Before live use: review costs and quotas; review the saved Terraform plan and administrator bootstrap; validate actual IAM/service acceptance; preserve state; verify exact bindings and owners; review and install the chosen policies; then verify effective access and first-apply behavior with workers off. Failed provider calls can require a revised reviewed proposal. No remote CI result or deployed authorization behavior is claimed by offline test success.

The generator and tests do not alter frontend source, local settings, `dist/`, runtime databases or running processes. Before any grant, rollback is discarding or regenerating ignored proposals. After grants, revoking policies or undoing infrastructure changes needs separate administrator/retention review; Git reversion does not undo live access or retained resources.
