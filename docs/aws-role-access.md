# IAM role-access preparation and initial grant

Status: generation/tests are offline; the user-approved initial IAM changes were applied through the AWS console on November 25, 2026. The administrator-owned `queuerescue-dev-execution-boundary` and `queuerescue-dev-deployment-roles` customer-managed policies were created. Only the latter was attached directly to `queuerescue-dev`; the console confirmed one policy added and showed four developer policies, including the original three. No execution roles, service infrastructure or workers were created or enabled. This is the role-delegation portion of access, not complete deployment permission.

Before the grant, live console inspection found no developer groups, none of the six exact project roles, and no matching QueueRescue managed policy. The saved delegation preview was read through all seven statements before attachment, including boundary checks and service-specific PassRole conditions. Private approval/change evidence is in `.data/deployment-access-review/role-grant-change.json`. Effective SCP/resource/session-policy behavior, service acceptance and future role boundary attachment remain unverified; a console attachment is not an IAM simulation. Future runtime roles must still attach the reviewed ceiling through Terraform.

## What is prepared

Terraform accepts `execution_permissions_boundary_arn`, default `null`, on all five base Lambda execution roles, the Step Functions role and the optional audit writer. Only the exact `arn:aws:iam::ACCOUNT:policy/queuerescue-ENV-execution-boundary` name in the selected account/environment is accepted. Terraform does not create or edit that policy, inspect its contents, or prove that it exists. Null preserves the existing configuration; it does not mean delegated IAM access is safe to grant.

The offline generator produces two policy documents for the **minimal base stack**:

- `runtime_boundary`: a ceiling for existing base runtime actions on exact regional tables, queues, functions, workflow and log groups, and the dedicated evidence-object prefix. S3 access also requires the expected bucket owner. No customer KMS key, dashboard-hosting access, audit stream/archive permissions, source-DLQ deletion or evidence-object deletion is included.
- `iam_delegation_fragment`: creation and maintenance of six exact named roles, creation/boundary attachment only with the approved boundary, separate `iam:PassRole` grants for Lambda and Step Functions, and explicit denials of boundary removal or changes to the administrator-owned boundary policy. It does not grant managed-policy attachment, role deletion, IAM user/access-key management or service provisioning.

A boundary limits identity-policy permissions; it does not itself grant permissions. The existing narrower per-role inline policies remain in place. The shared ceiling is the union of base runtime needs, not a replacement for per-role separation. A deployer who can change application code and inline role policies is trusted with project data and can exercise permissions within that ceiling. Review resource-based policies and all other effective grants; this fragment does not constrain unrelated policies or protect against an account administrator. See [AWS boundary semantics](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_boundaries.html) and [role/service restrictions for PassRole](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_passrole.html).

## Generate a private proposal

From the project root, using the reviewed account ID locally:

```sh
python3 -m tools.aws_role_access --account-id YOUR_ACCOUNT_ID \
  --region us-east-1 --environment dev
```

The command uses only Python's standard library, makes no AWS calls and writes `.data/aws-role-access.json` atomically with private permissions. It prints no account-specific values. The CLI has no apply, credential or permission-changing option. Keep account values, generated proposals and later effective-access evidence ignored; never commit them. The JSON bundle contains review metadata and two nested policy documents: it is **not** itself an attachable IAM policy. An authorized administrator must select and review each nested policy separately.

For the current proposal, the delegation document exceeds IAM's 2,048-character aggregate user inline-policy limit. Review it as a customer-managed policy rather than pasting it into a user inline policy. Each customer-managed document must fit the 6,144-character limit; existing attachment counts and all effective grants still need inspection. See [IAM policy limits](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_iam-quotas.html).

## Requirements before any grant

1. An authorized administrator reviews the proposed ceiling against `infra/aws/compute.tf`, exact resource ownership and the complete deployment plan, then creates the managed boundary outside the deployer's control. Confirm policy versions and the actual ARN. Do not grant a deployer permission to create, edit or delete its own ceiling.
2. Inventory all six exact role names before granting maintenance access. Existing roles must belong to this sandbox and already have the approved boundary. The fragment's maintenance statement assumes that precondition; it cannot safely establish it from a matching name. Stop on unrelated name collisions or unbounded existing roles. Review trust policies: Lambda roles trust `lambda.amazonaws.com`; the workflow role trusts `states.amazonaws.com`. Maintenance includes trust/inline-policy changes within the project ceiling, so the deployer remains privileged.
3. Put the approved ARN in ignored `infra/aws/terraform.tfvars` using `execution_permissions_boundary_arn`. Review the resulting saved plan before apply. Validate policy syntax/effective access using authorized AWS tools and inspect all existing deployer grants. Offline assertions are not AWS IAM simulation or proof of service acceptance. This work does not grant new Access Analyzer/simulation permissions automatically.
4. Review the [offline scoped service policy package](aws-service-access.md), including its binding and administrator-bootstrap requirements, then complete the effective service-provisioning grant for S3, DynamoDB, SQS, Lambda, API Gateway, Cognito, Step Functions, logs and any selected notification/budget resources. Scope IAM passing to the six approved ARNs/services; avoid AdministratorAccess and generic `iam:*` grants. Review resource-policy management, code-update powers, global-service exceptions and teardown access explicitly. A name prefix alone is not an access boundary.
5. Resolve account cost eligibility, Lambda quota, notification delivery and the concrete deployment/retention plan. No spending or live deployment is authorized by generating a proposal.

The supplied ceiling deliberately omits standard-profile KMS and optional audit-writer permissions. Terraform can attach an externally reviewed expanded boundary to those roles, but **do not use the generated base ceiling for those profiles**: runtime access would be denied. Review and test expanded ceilings/delegation separately. Hosting does not introduce another execution role, but hosting provisioning/publication permissions are also outside this IAM fragment.

## Compatibility, verification and rollback

The default null input preserves the repository’s previous planned configuration and adds no resource. Existing live roles require drift review before apply. Account/environment/name validation and mocked plans cover both the base and optional audit attachment paths. Ten Python tests cover exact role scope, wrong/missing boundaries, service-specific role passing, explicit deny precedence, protected-boundary edits, destructive runtime exclusions, account/region isolation, input validation and the offline CLI contract. The small policy evaluator tests only the emitted subset; it is not a general IAM authorization engine. Full Python regressions and all mocked Terraform plans are run before delivery. No frontend or recovery-protocol changes are included.

Once a boundary is attached, removing it or restoring null is a security change requiring administrator review. The explicit deny intentionally blocks a deployer from removing it. Role deletion is not granted; Terraform teardown may require separately reviewed administrator actions and permissions. Do not remove the boundary simply to make a destroy succeed. Existing evidence-retention and data-loss approvals still apply. Reverting code is not a safe substitute for reviewing live policy state. The initial managed policies and developer attachment now exist in AWS: rollback requires review of revoking the delegation attachment and any policy cleanup, not just deleting local proposal files. No automatic revocation or policy deletion is authorized by this document.
