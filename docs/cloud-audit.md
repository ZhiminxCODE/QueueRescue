# CloudTrail coverage and protected audit retention

The controls below are implemented and tested **offline**. They have not been provisioned or verified in AWS. The local SQLite simulation, frontend and running application are unchanged. This is a bounded development profile, not a compliance certification or protection against an account administrator.

## Configuration and cost boundary

Both switches default to false:

| Setting | Meaning |
| --- | --- |
| `audit_logging_enabled=false` | No audit bucket, trail, writer, stream mapping or audit alarms are provisioned. The existing application audit table remains. |
| `audit_logging_enabled=true` | Provision the protected archive and its supporting controls. Once created, leave this true to retain ownership of the protected bucket. |
| `audit_collection_enabled=true` | Start CloudTrail and the archive stream reader; reserve one additional Lambda concurrency unit. Requires provisioned audit controls. |
| `audit_collection_enabled=false` | Stop new trail capture and archive polling; reserve zero archive concurrency. Retained objects, alarms and other provisioned resources remain. |
| `audit_retention_days=7` | Default Governance retention for **new versions**, configurable to an integer from 1–90. Changing the default does not shorten existing version retention. |

Only `us-east-1` is accepted for this optional profile. AWS delivers global IAM/STS events to single-region trails in that region; a different region or multi-region design needs separate review. Other stack profiles can still use other configured regions with these audit controls absent. [AWS global-service event scope](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-concepts.html).

Do not enable either switch using the existing baseline cost estimate as approval. It explicitly reports `audit_logging_included=false`. This option adds one Lambda/log group, an audit stream/mapping, a private versioned Object Lock bucket, a CloudTrail trail, four alarms and one custom log-derived metric. Include data-event charges, duplicate management-event copies from existing trails, requests, SDK reads on duplicate deliveries, failed-batch storage, retries, log/metric charges and retained versions in a revised account-specific worksheet. A paused stack can continue to incur costs. Neither alerts nor retry limits impose a spending cap.

The readiness helper accepts `--audit-logging-enabled` for an **active audited demo**. It checks six additional reserved units (consumer four + replay one + audit one), rather than five, while leaving AWS's required unreserved pool. It still reports `deployment_ready=false`. No quota increase or IAM grant is performed automatically.

## Coverage and deliberate gaps

| Evidence | Prepared coverage | Limits |
| --- | --- | --- |
| Application audit table | Existing transactional audit entries remain authoritative for application actions; no TTL is added. | Append-only application IAM is not protection against privileged table administration. |
| Full application audit archive | `NEW_AND_OLD_IMAGES` stream records from the exact audit stream, including INSERT, MODIFY and REMOVE, copied into `audit-records/`. | No historical table backfill. The source stream retains records for 24 hours; gaps must be reconciled. |
| CloudTrail management | Regional write management events, plus supported global-service events in `us-east-1`; log-file validation enabled. | Account-wide regional management scope, not a stack-only resource filter. Other regions and read-only management activity are outside this trail. |
| DynamoDB data events | Write API allowlist on the exact audit table, including transactional writes. | No table-wide reads, business/ledger data events, stream polling or TTL-deletion coverage. A transactional event can describe other tables in the same request. |
| S3 data events | Reads/writes on the evidence prefix, application archive prefix and Lambda failure prefix. Trail-object reads, deletes and retention/legal-hold changes use a separate event-name allowlist. | Trail delivery writes are excluded to avoid recursive logging. A malicious new PutObject version under the trail prefix is not captured by that allowlist; protected prior versions and digest validation remain relevant. |
| Application HTTP activity | Existing authenticated API authorization and sanitized API Gateway access logs. | This trail does not add complete end-user sign-in, Lambda invocation, SQS data-event or distributed-trace coverage. CloudTrail's service principal is not the Cognito application subject. |

Data events are explicitly selected; CloudTrail event history alone is not their verification surface. See [management events](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/logging-management-events-with-cloudtrail.html), [data-event selectors](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/filtering-data-events.html), and [DynamoDB CloudTrail coverage](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/logging-using-cloudtrail.html).

Read-only management activity, including STS credential operations when classified read-only, is excluded; this is not a complete identity/session audit.

Pausing capture creates an intentional gap. Stop cloud application writes before pausing; do not claim new activity is archived while capture is off. Re-enabling polling can only recover stream records still available from its checkpoint. Existing audit rows are not backfilled merely by enabling the stream. A separate reviewed export/reconciliation is required for historical rows or a gap.

## Retention, integrity and access

The separate S3 bucket has public access blocked, ownership enforced, SSE-S3 default encryption, TLS-only access, versioning and default **GOVERNANCE** Object Lock retention. Terraform sets `force_destroy=false` and `prevent_destroy=true`; the evidence bucket's separate cleanup option never applies to this archive. The lifecycle schedules current and noncurrent expiry after `audit_retention_days + 1` days and cleans expired markers. Deletion is asynchronous, and noncurrent-version age can extend storage beyond the original lock period.

Governance retention protects **versions**, not key names. Privileged principals with bypass permission can remove that protection, and an administrator can change policies, stop logging, disable the stream or submit forged activity. New versions or delete markers can obscure a retained version. Always verify the intended VersionId, checksum and retain-until date. No Compliance mode or automatic bypass is introduced. Enabling Object Lock is a long-lived bucket decision; do not assume it can later be disabled. [Object Lock behavior](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html), [Governance bypass and lifecycle considerations](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock-managing.html).

The archive writer has no table-item write, queue send/delete, retention-changing, object-delete or bypass permissions. It reads only the exact configured stream; metadata-only ListStreams needs a wildcard resource with a regional condition. Its S3 access is limited to this account's dedicated bucket: conditional record writes/checksum reads under `audit-records/`, failure writes under `aws/lambda/`, and bucket listing required by the failure destination. Standard mode adds only Decrypt through DynamoDB on the existing key. Ordinary API/collector/replay/business roles receive no archive access. CloudTrail delivery is restricted to its exact source ARN and `cloudtrail/AWSLogs/<account>/` prefix. [DynamoDB stream authorization](https://docs.aws.amazon.com/service-authorization/latest/reference/list_dynamodb.html), [CloudTrail bucket policy](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/create-s3-bucket-policy-for-cloudtrail.html).

Identical record envelopes have stable content-addressed keys. SHA-256 and conditional creation avoid replacing an existing record on redelivery. A duplicate checks the S3-computed checksum; a mismatch or uncertain/denied operation remains failed. The writer never changes recovery/business state. Archives contain raw audit context and potentially sensitive notes; they are private evidence, not masked dashboard exports. Stream/source checks are not cryptographic proof against a privileged direct Lambda invoker.

## Failure handling

Batches contain at most 100 records, preserve ordering by stopping at the first failure, and return its DynamoDB sequence number. Invalid checkpoint data fails the invocation. Configured partial-batch reporting, five retries, bisection and a one-hour maximum record age bound ordinary function-error retries. Service throttles and pre-invocation failures have separate behavior; these settings are not a total request/cost limit.

Discarded invocations target the same protected S3 bucket. AWS uses `aws/lambda/<ESM-UUID>/<shardID>/...`, not a function-name directory. S3 destinations retain the original invocation payload alongside metadata; SQS/SNS metadata alone would not be a full-record archive. Delivery failure is still possible and must be checked. [AWS failure-destination behavior and required permissions](https://docs.aws.amazon.com/lambda/latest/dg/services-dynamodb-errors.html).

Four alarms cover Lambda errors, log-derived partial-batch failures, iterator age over five minutes and destination delivery failures. Partial failures log only `{"event":"AUDIT_ARCHIVE_FAILED"}`; no body, actor, source ARN or credentials are printed. Missing metrics do not prove successful capture. Configure and confirm the existing SNS notification endpoint before relying on alerts; also inspect trail delivery status independently. CloudTrail can retry a misconfigured destination for up to 30 days with charges; a worker kill switch does not stop trail redelivery. Correct delivery promptly. If it cannot be corrected, separately review removal of the broken trail while retaining its protected bucket and required evidence; do not delete the destination first. [AWS delivery warning](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/create-s3-bucket-policy-for-cloudtrail.html).

To recover a failed archive batch, inspect its protected S3 invocation record using separately reviewed evidence-reader permissions. Extract the original payload into an ignored, mode-0600 file and reprocess **only through the archive writer** after correcting the cause. Preserve the exact source stream and record envelopes; a changed stream ARN requires a separately reviewed import/reconciliation. Never send audit failures into shipment replay. Compare audit keys/stream event IDs and inspect all failure destinations before claiming completeness.

## Deployment verification — not yet performed

After the account, cost, permissions, quota and saved plan are reviewed:

1. Inventory existing trails, covered regions/data selectors and possible duplicate charges. Choose a notification endpoint and retention explicitly. Review Object Lock's lasting effects and retained-resource costs.
2. Provision with both provisioning and capture true **before** the bounded synthetic workflow. Verify actual IAM, encryption, ownership, bucket policy, exact selectors, stream ARN, mapping, concurrency and confirmed notifications. Keep the separate shipment/replay switches off until their own workflow is ready.
3. Create one synthetic application action. Compare the table audit key and complete stream envelope with its archive object. Verify VersionId, SHA-256, Governance mode and retain-until time using GetObjectAttributes/HeadObject/GetObjectRetention as appropriate; do not publish raw evidence. Test redelivery without creating a replacement version.
4. Confirm CloudTrail IsLogging, latest delivery/digest status and actual regional/global-service coverage. Verify an approved synthetic audit-table write, evidence read, archive read and harmless IAM metadata change. Inspect delivered trail files, not only event history, and validate their digest chain for the observed interval. Record missing/excluded events honestly.
5. On a **disposable synthetic object version**, verify the application roles cannot delete or bypass retention. Do not test irreversible cleanup against required evidence or use bypass to make the check pass.
6. Exercise a reviewed temporary writer failure. Confirm the partial-failure/error/age signals, alert delivery, full failed payload in the S3 destination and a successful archive-only recovery. Confirm DestinationDeliveryFailures remains clear. In particular verify real S3 destination delivery with Object Lock defaults; mock tests cannot establish native service integration.
7. Reconcile the finite set of table audit keys, archived records, failed batches and any intentional gaps. Validate late data after delivery settles before declaring the audit capture complete.

Use read-only CLI calls such as `cloudtrail get-trail-status`, `cloudtrail get-event-selectors`, `lambda get-event-source-mapping`, `s3api get-object-lock-configuration`, `s3api get-object-retention`, `s3api list-object-versions` and `cloudtrail validate-logs` with the reviewed non-root profile. Keep account-specific arguments and output in ignored files; never paste credentials or raw logs into Git. These checks are a runbook, not commands executed by this implementation.

## Pause, rollback and teardown

- Stop the bounded cloud workload and inventory audit writes. Let the writer drain; review errors, partial failures, iterator age, destination failures and version counts. Confirm final trail delivery/digests.
- Review a saved plan setting `audit_collection_enabled=false` while keeping `audit_logging_enabled=true`. Verify the trail is stopped, mapping disabled and writer concurrency zero. Leave shipment consumers/replay off as well. This pauses capture, not storage charges, and creates a documented coverage gap.
- Do **not** toggle provisioning false, switch off Object Lock, shorten existing retention or force-delete versions as an idle procedure. Terraform intentionally blocks audit-bucket destruction; generic stack destroy will encounter that protection. Reverting the code does not undo retention, stream history or already provisioned resources.
- If an emergency pause occurs before draining, record the gap immediately. Stream data expires after 24 hours. Failed native-destination delivery can lose the independent copy; reconcile from retained application audit rows or other reviewed evidence before deleting any source data.
- Retain the protected bucket and ownership metadata through the chosen retention period. To tear down the rest of a deployed stack, first prepare a separately reviewed migration/import of the retained archive resources to a retention-only Terraform configuration/state. Verify the new owner/plan before removing anything from the old state; do not orphan the bucket or bypass `prevent_destroy` with targeted deletion. No state migration is executed here.
- Later, review exports, version/marker inventory, actual retain-until dates and billing. Wait for retention/lifecycle and verify physical deletion before a separately approved bucket teardown. Keep any required archive readers until evidence disposition is complete. Cross-account/organization archival, stronger administrator separation and legal Compliance retention remain future work.

## Offline evidence

The focused SDK tests cover exact source checks, full insert/update/delete images, conditional/checksummed writes, duplicates, corruption, denial, timeout, privacy, checkpoints and client retry settings. Mocked Terraform plans cover disabled/idle/active profiles, private locked storage, selector/role boundaries, encryption and invalid retention/region settings. The package includes the handler. These checks do not prove AWS retention enforcement, CloudTrail delivery/completeness, native failure delivery, notification timing or billing.

Current checkpoint: 186 Python tests and 22 mocked Terraform plans passed; Terraform validation/formatting and pinned Lambda packaging passed. The focused trace run covered all 56 executable lines reported for the archive module; branch coverage and live service enforcement are not established by that result.
