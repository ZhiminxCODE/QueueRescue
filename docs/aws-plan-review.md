# Offline review of a saved AWS plan

Status: implemented and tested offline. No real-account plan has been generated or approved by this work. The checker reads existing Terraform plan JSON and never invokes AWS, Terraform, grant operations or apply.

November 27 preparation: a fresh ignored `.data/aws-plan-review-*` folder now contains two byte-identical pinned Lambda builds, package/source hashes, private minimal/idle inputs, an isolated Terraform source copy and verification logs. All 229 Python tests, 33 frontend tests, Terraform formatting/validation and 32 mocked plans passed. Provider validation required local plugin communication outside the restricted execution environment; the successful tests used the mocked provider, not live AWS. Existing `dist/`, `.env`, `.env.local` and ignored Terraform values matched their pre-run hashes.

The private locator is `.data/aws-plan-review-latest.json`; it identifies `verification.json` and `next-plan-commands.json`. The latter records exact future read-only plan/export/check arguments, with no apply command. Use the canonical `infra/aws` directory for the real plan so any existing state remains in scope; the isolated copy is for offline validation only. Recheck source/artifact hashes and the actual state before planning. The live session check returned `ExpiredLoginSession`, so no real account plan or current quota result was obtained. No grants, application infrastructure, worker activation or deletion were performed during this preparation.

## Supported plan

`tools/review_aws_plan.py` checks selected invariants in a **full minimal base plan with workers idle**, using Terraform's version-1 [JSON plan representation](https://developer.hashicorp.com/terraform/internals/json-format#plan-representation). It expects the repository's 62 base resources, or 64 when the email subscription and budget are configured. It rejects partial/targeted inventories, extra resources, alternate providers, imports/moves, drift, deferred/incomplete plans, deletions, replacements and unsupported change actions. Creates, non-destructive updates and no-op entries are supported.

The checks require the exact reviewed account/region/environment, minimal mode, consumers/replay/audit/hosting off, data protection on, evidence destruction off and the approved external execution-boundary ARN. Actual resource values must agree: every execution role has that boundary, workers reserve zero concurrency, mappings are disabled without active scaling settings, queues use service-owned encryption, tables retain fixed small capacity and deletion protection, and evidence storage remains private, versioned and AES256 encrypted. The checker also checks expected names/log retention, Cognito MFA/client settings, JWT route authorization, callback/CORS consistency and configured notification endpoint/budget identity.

This is not a general Terraform security scanner or a complete policy/cost review. Some computed fields are unknown before apply. The checker does not validate all IAM/trust/resource policies, lifecycle/retention policy contents, code/artifact integrity, provider/account configuration, resource ownership, actual MFA enforcement, credits, notifications, quotas or billing. Caller identity and plan provenance must be established separately. Callback consistency does not prove domain ownership. It does not inspect a saved binary plan directly or prove that a JSON file came from that binary.

The base profile excludes KMS, optional audit/archive, CloudFront hosting and additional resources. Those selections need separately reviewed cost, policy and plan checks. Administrator bootstrap/targeted/import plans described in the [service access runbook](aws-service-access.md) are outside this checker's supported profile and require their own explicit review. Do not remove protections or omit resources merely to obtain a PASS.

## Use locally after separate plan preparation

First resolve the account, cost, quota and permission prerequisites in the [deployment runbook](aws-deployment.md). Preparing a real plan can read AWS and is a separate step; no real plan was prepared during implementation of this tool.

After a concrete saved plan has been prepared through that reviewed process, export its JSON to ignored private storage:

```sh
umask 077
terraform -chdir=infra/aws show -json reviewed.tfplan > .data/reviewed-plan.json
python3 -m tools.review_aws_plan \
  --plan-json .data/reviewed-plan.json \
  --account-id YOUR_REVIEWED_ACCOUNT_ID \
  --region us-east-1 --environment dev
```

Run from the repository root with `.data/` already present. Use the pinned initialized Terraform provider for exporting; keep saved plans, JSON, account values and reports out of Git and chat. Terraform [warns that JSON output can expose sensitive values in plain text](https://developer.hashicorp.com/terraform/cli/commands/show#json-output).

The tool prints only PASS/FAIL and issue codes, atomically saves `.data/aws-plan-review.json` with private permissions, and exits nonzero on rejected plans. A malformed/unreadable input writes no new report; a previous report may remain and must not be mistaken for current evidence. Repeated successful parses replace the prior report. The `plan_sha256` identifies the exact JSON bytes, including line endings; verify its correspondence with the selected saved binary and current source before any apply. Do not regenerate or replace a reviewed binary plan unnoticed.

`checks_passed=true` means only the selected offline checks passed. `deployment_ready` remains **false** in every report. Actual deployment requires review of the exact saved binary, administrator bootstrap, effective access and ownership, external boundary contents, code hashes, costs/quota, callback ownership, notification delivery and cleanup/retention access. The CLI contains no apply option and a PASS never grants permission to spend or deploy.

## Verification and compatibility

Fifteen Python tests cover target/profile mismatches, strict booleans, missing/changed boundaries, active worker values hidden behind idle variables, destructive changes, drift/imports, missing/duplicate/extra resources, storage protection/encryption, authentication/CORS, unknown/malformed settings, notifications, CLI behavior and exact-byte hashing. The test fixture is authored synthetic data, not a saved account plan or runtime evidence.

The checks were also exercised against the 62 actual resource-change entries emitted by the provider's mocked minimal/idle Terraform test with a configured boundary. Terraform's test renderer omits root variables, so that integration used an explicitly authored matching synthetic variable header. This checks resource-shape compatibility; it is not an actual account plan or a live deployment. All mocked Terraform safety plans and the full Python regressions are run for delivery. No remote CI result is claimed here.

The resource inventory is intentionally tied to this repository. When changing Terraform resources or the first-demo profile, review the checker, its tests, service permission scopes and cost model together. Discarding an ignored report is the only rollback before any apply; the checker changes no AWS state, frontend, local settings, database or running process.
