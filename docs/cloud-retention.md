# DLQ age and snapshot retention warnings

Implemented and verified offline on November 16, 2026. No AWS resources or permissions were applied. The local simulator remains a separate implementation, and real-account validation still requires the reviewed deployment and cost decisions.

## Two independent retention windows

The AWS inbox and masked snapshot inspection show original-message age/expiry estimates separately from the snapshot access deadline. Warnings begin when a known deadline is within **24 hours**. All values are stamped with the server calculation time and require deliberate refresh; there is no timer-based AWS polling or automatic collection/replay.

For the supported **standard** SQS queues, original-message age is measured from the message's original `SentTimestamp`, not collection time, first receive time or an approximate queue-age metric. The expiry estimate is that timestamp plus the observed **DLQ** `MessageRetentionPeriod`. The original enqueue timestamp is preserved when standard messages enter a DLQ; the separate FIFO behavior is outside this prototype. See [AWS's DLQ retention explanation](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-dead-letter-queues.html).

An estimate does not prove current presence or deletion. Another consumer, administrator, purge or SQS expiration may remove the original. A passed estimate is labeled `EXPIRY_ESTIMATE_PASSED`; it is never converted into confirmed deletion. Collection neither deletes originals nor renews their SQS retention. The UI calls the age **enqueue age**, because the time spent specifically in the DLQ is not established by the captured attributes.

Snapshot `expires_at` is the application's **access deadline**, currently assigned at collection using the configured seven-day snapshot retention. It is independent of original-message expiry. Masked inspection can remain available after the original is removed, provided this deadline has not passed, the evidence is present, integrity checks pass, and storage access is authorized. An original-message warning does not change replay eligibility: retained approved evidence, schema checks, separate approval and conservative business verification still govern recovery.

The access deadline is not a promise that S3 bytes remain available until then or disappear exactly then. Existing S3 lifecycle rules expire current versions after seven days and noncurrent versions after one day; versioned expiration can create a delete marker, and processing is asynchronous. An immutable object can also predate successful indexing after an interrupted collection. The API does not bypass expiry by fetching older object versions. See [S3 lifecycle expiration behavior](https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-expire-general-considerations.html).

## Observation and unknown states

Each authenticated `/messages` list, incident-member list and successful masked detail inspection makes at most **one** `GetQueueAttributes` request to the configured DLQ, asking only for `MessageRetentionPeriod` and `QueueArn`. The returned ARN must match that queue and the period must be within 60–1,209,600 seconds. No queue metrics, message payloads or arbitrary queue identifiers are requested. Incident-summary listings and log-evidence lookup do not perform this settings read.

These requests read settings without receiving messages, changing visibility, sending, deleting, updating retention or extending evidence lifetime. A fresh API read makes a new settings request; there is no persisted settings cache or per-message settings request. Queue changes can still be propagating even after a successful read: AWS documents that retention changes can take up to 15 minutes and affect existing messages. See [GetQueueAttributes](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_GetQueueAttributes.html).

Missing, malformed, future or unsupported timestamps yield unknown enqueue age and expiry. Denied, missing, unavailable, unsupported or mismatched queue metadata yields unknown original expiry without hiding otherwise valid snapshots. If the enqueue timestamp is valid, its age can still be shown even when the retention period is unknown. No default retention period is invented. Malformed SQS timestamps no longer interrupt collection: the raw system attributes stay in immutable evidence, while indexed `sourceSentAt` is zero and its estimate is unknown.

Every listed snapshot includes a derived `retention` object:

| Field | Meaning |
| --- | --- |
| `asOf`, `warningThresholdSeconds` | Server calculation time and 24-hour warning threshold |
| `queue.status`, `queue.checkedAt`, `queue.periodSeconds` | Observed settings or a safe diagnostic status; raw AWS error text is excluded |
| `original.status` | `UNKNOWN`, `NEAR_EXPIRY`, `ESTIMATED_WINDOW_OPEN`, or `EXPIRY_ESTIMATE_PASSED` |
| `original.sentAt`, `enqueueAgeSeconds`, `estimatedExpiresAt`, `remainingSeconds` | Known original enqueue time, age and estimated remaining window; unknown values are null |
| `original.presence` | Always `UNVERIFIED` |
| `snapshot.status` | `UNKNOWN`, `NEAR_EXPIRY`, `WITHIN_ACCESS_WINDOW`, or `EXPIRED` |
| `snapshot.accessExpiresAt`, `remainingSeconds`, `availability` | Application access deadline and read observation; listings use `NOT_CHECKED`, successful inspection uses `READ_SUCCEEDED` |

Lists still omit expired metadata and never read S3 payloads. Snapshot detail reads reject expired access with HTTP 410 and unknown access retention with HTTP 409, before requesting payloads. The deadline is rechecked after storage access and before returning a masked payload, so an inspection that expires during the read is rejected too. Missing current evidence returns HTTP 410; denied S3 access returns a safe HTTP 503 without exposing the provider's error text. The frontend closes a previous payload view if inspection refresh fails. Previously delivered/downloaded content cannot be revoked by these request checks.

Detail responses use `originalPresence: UNVERIFIED` and `sourceDeletionPerformed: false` in place of the old, ambiguous `originalsRetained` detail flag. The collection response's legacy `originalsRetained` flag describes QueueRescue's non-deleting operation, not current queue presence. The cloud UI likewise says that QueueRescue performs no source deletion rather than promising perpetual retention.

## Permission and cost boundary

The prepared API policy adds only `sqs:GetQueueAttributes` on this stack's exact source-DLQ ARN. It adds no receive, visibility-change, delete, retention-update, S3 version-read or S3 write permission. Existing snapshot reads remain restricted to the evidence prefix. S3 lifecycle, DynamoDB TTL, idle worker switches and data protections are unchanged. This IaC still needs the existing account/deployment review before applying it.

One SQS metadata request per relevant API read must fit the existing reviewed 10,000-request SQS allocation and 200 API-Lambda invocation allocation for the bounded demo. Extra refreshes, latency and investigations beyond the reviewed workload require reassessment. No new resources are created by these warnings, and no zero-bill guarantee or spending cap is implied.

## Offline verification

Tests cover original-enqueue semantics, threshold/deadline boundaries, independent snapshot lifetime, malformed metadata, changed DLQ settings, denial/unavailability, single-request bounds, masking, Viewer and queue scope, snapshot expiry during reads, storage access failures, and versioned delete markers without bypassing them. A masked snapshot remains readable after explicitly removing the original in Moto, while business classification remains independent of the warning. Terraform mock assertions protect exact queue permissions, evidence-prefix access and existing lifecycle periods.

The browser fixture checks near/passed/unknown original estimates, near-expiry snapshots, explicit refresh, Viewer restrictions and closure of a stale inspection after HTTP 410. All fixture data is synthetic and remains ignored. Run the full Python suite, frontend tests/build and documented offline Terraform checks; live SQS propagation, lifecycle timing and IAM behavior remain deployment-verification tasks.
