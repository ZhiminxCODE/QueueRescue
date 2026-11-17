# Offline IAM role-access preparation

Status: prepared and tested offline. No AWS calls, permission grants, deployment, quota requests or account changes were made. This completes the runtime-boundary and IAM role-delegation portion of deployment preparation, not the full service-provisioning policy.

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

## Requirements before any grant

1. An authorized administrator reviews the proposed ceiling against `infra/aws/compute.tf`, exact resource ownership and the complete deployment plan, then creates the managed boundary outside the deployer's control. Confirm policy versions and the actual ARN. Do not grant a deployer permission to create, edit or delete its own ceiling.
2. Inventory all six exact role names before granting maintenance access. Existing roles must belong to this sandbox and already have the approved boundary. The fragment's maintenance statement assumes that precondition; it cannot safely establish it from a matching name. Stop on unrelated name collisions or unbounded existing roles. Review trust policies: Lambda roles trust `lambda.amazonaws.com`; the workflow role trusts `states.amazonaws.com`. Maintenance includes trust/inline-policy changes within the project ceiling, so the deployer remains privileged.
3. Put the approved ARN in ignored `infra/aws/terraform.tfvars` using `execution_permissions_boundary_arn`. Review the resulting saved plan before apply. Validate policy syntax/effective access using authorized AWS tools and inspect all existing deployer grants. Offline assertions are not AWS IAM simulation or proof of service acceptance. This work does not grant new Access Analyzer/simulation permissions automatically.
4. Complete a **separate** scoped service-provisioning policy for S3, DynamoDB, SQS, Lambda, API Gateway, Cognito, Step Functions, logs and any selected notification/budget resources. Scope IAM passing to the six approved ARNs/services; avoid AdministratorAccess and generic `iam:*` grants. Review resource-policy management, code-update powers, global-service exceptions and teardown access explicitly. A name prefix alone is not an access boundary.
5. Resolve account cost eligibility, Lambda quota, notification delivery and the concrete deployment/retention plan. No spending or live deployment is authorized by generating a proposal.

The supplied ceiling deliberately omits standard-profile KMS and optional audit-writer permissions. Terraform can attach an externally reviewed expanded boundary to those roles, but **do not use the generated base ceiling for those profiles**: runtime access would be denied. Review and test expanded ceilings/delegation separately. Hosting does not introduce another execution role, but hosting provisioning/publication permissions are also outside this IAM fragment.

## Compatibility, verification and rollback

The default null input preserves the repository’s previous planned configuration and adds no resource. Existing live roles require drift review before apply. Account/environment/name validation and mocked plans cover both the base and optional audit attachment paths. Ten Python tests cover exact role scope, wrong/missing boundaries, service-specific role passing, explicit deny precedence, protected-boundary edits, destructive runtime exclusions, account/region isolation, input validation and the offline CLI contract. The small policy evaluator tests only the emitted subset; it is not a general IAM authorization engine. Full Python regressions and all mocked Terraform plans are run before delivery. No frontend or recovery-protocol changes are included.

Once a boundary is attached, removing it or restoring null is a security change requiring administrator review. The proposed explicit deny intentionally blocks a deployer from removing it. Role deletion is not granted; Terraform teardown may require separately reviewed administrator actions and permissions. Do not remove the boundary simply to make a destroy succeed. Existing evidence-retention and data-loss approvals still apply. Reverting code is not a safe substitute for reviewing live policy state. Until any reviewed grant/apply occurs, rollback consists only of discarding the ignored proposal; there is no AWS state from this preparation to undo.
