# Correlated cloud log evidence

Implemented and verified offline on November 16, 2026. No AWS deployment, real log ingestion, IAM propagation or console deep-link behavior has been validated in an account. The local simulator keeps its separate simulated evidence implementation.

## Instrumentation

The AWS collector, shipment consumer, replay worker, business verifier and API emit single-line JSON telemetry with `telemetryVersion: 1`. Emission accepts only known component/event names, bounded timestamps, approved outcome codes, HTTP status codes and SHA-256 references. It omits bodies, raw attributes, access tokens, business identifiers and exception text. Platform-generated Lambda messages are separate from this application schema.

References use `SHA256(kind + ":" + value)` for message, operation, correlation, queue, job, attempt, snapshot and request identifiers. Matching references are stable across components. These are correlation references, not secrets or anonymization: predictable inputs can be guessed. An optional original `correlationId` attribute is hashed; it never grants authority to associate another message with evidence. Missing or unusable attributes do not invent a correlation ID.

The collector stores message/operation/correlation references alongside indexed metadata. The consumer derives message and queue references from its SQS event, and derives job/attempt references only when that event's source ARN is the configured replay queue. Replay preserves the original body and attributes and uses the existing job/attempt attributes; instrumentation adds no SQS attributes or new business mutations.

Consumer failures distinguish schema rejection, blocked business state, the demo's timeout before/after the business effect, AWS errors and unexpected errors. Replay telemetry distinguishes send intent, an SDK send acknowledgment, uncertainty and worker interruption. Verifier telemetry records its observed domain status. Telemetry emission failures are swallowed so they cannot turn an accepted send or committed business effect into a processing retry. The ledger, approval and verifier remain authoritative for recovery decisions.

## Scoped lookup API

Authenticated readers, including Viewers, can explicitly request:

```text
GET /api/messages/SNAPSHOT_ID/evidence
GET /api/messages/SNAPSHOT_ID/evidence?jobId=JOB_ID&attemptId=ATTEMPT_ID
```

The snapshot must exist in the configured queue and remain within metadata retention. Source lookup matches the exact original message reference and configured source-queue reference in the configured **consumer** log group. Legacy snapshots can derive these references from existing metadata without fetching an S3 body. Replay lookup also validates the job/attempt/snapshot relationship, approved evidence checksums and configured destination before matching exact queue, operation, job and attempt references in the **consumer** and **replay** groups. A shared operation or correlation ID alone is never enough.

Callers cannot supply a log group, log stream, filter pattern, account, region, raw service continuation token or arbitrary search expression. Only optional `from` and `to` Unix-second bounds and the exact job/attempt pair are accepted. Each window is at most 24 hours and must fall within server-derived resource bounds. The default source window ends at the earlier of now or five minutes after collection and starts at the later of that end minus 24 hours or the original send time minus five minutes (falling back to collection time if the send time is absent). Replay bounds start five minutes before job creation and end at now; the default is the latest 24 hours within those bounds. The response identifies clipped windows. Earlier bounded windows can be requested explicitly.

For each group, lookup makes at most **two** `FilterLogEvents` calls, requesting at most **25 events per call**, with `unmask=false`. Source lookup therefore makes at most two calls; replay makes at most four and can return up to 100 validated records. Empty pages are followed only within that bound; remaining or repeated continuation tokens yield `TRUNCATED`. There is no background polling, automatic retry, Logs Insights query or new telemetry service. A bound on returned events/calls is not a guarantee about provider-side work or latency. AWS documents partial and empty pages in the [FilterLogEvents API](https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_FilterLogEvents.html).

Responses revalidate the telemetry version, component, exact references and timestamps, and project approved fields only. Raw log messages and service error text are never returned. Returned events may include a regional CloudWatch log-stream link built from the configured group and a stream actually returned by CloudWatch. Links do not imply an independently verified root cause, exact event anchor, or permission to open the AWS console. The console requires its own authorized AWS identity; application sign-in does not grant that access. Distributed tracing is not instrumented: `traceStatus` is `NOT_INSTRUMENTED`, and no trace IDs or trace links are invented.

## Reading results

`status: FOUND` means at least one matching telemetry record was returned. `INCONCLUSIVE` means none was returned; it is never proof of success. Each source reports `COMPLETE`, `TRUNCATED`, `DENIED`, `MISSING`, `UNAVAILABLE` or `NOT_CONFIGURED`, plus the number of pages read. Partial evidence is retained if another source fails. `searchComplete` describes completion of the bounded search only and is false for clipped windows. It does not establish complete logging or inventory coverage.

`failureObserved` reports whether the returned telemetry contains a consumer-failure event. Both an SDK-accepted send and a consumer handler return remain diagnostic evidence, with `businessVerification: NOT_ASSESSED`. A timeout after a business effect can coexist with a domain verifier reporting COMPLETE. Neither logs nor their absence update a job or classify a message as replay eligible.

Logs can be delayed, missing, expired or outside the selected time window. Standard profile function logs retain seven days; minimal profile logs retain one day. Older events or snapshots created before instrumentation may have no matching records. Imported/redriven events whose original identity is unavailable can remain uncorrelated. Administrators and authorized writers can modify infrastructure or write misleading logs; these are not tamper-proof forensic records.

## Infrastructure and cost boundary

Terraform supplies `QR_CONSUMER_LOG_GROUP` and `QR_REPLAY_LOG_GROUP`. Missing or invalid configuration returns `NOT_CONFIGURED` without a log request. Only the API receives `logs:FilterLogEvents`, restricted to this stack's exact consumer and replay log-group ARNs. It receives no `logs:Unmask`, `logs:StartQuery`, broad log discovery or cross-account lookup permission. The action supports log-group resource scoping in the [AWS authorization reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_logs.html).

These are prepared code/IaC changes, not applied account permissions. Consumers and replay remain disabled in idle Terraform defaults. Instrumentation uses existing log groups; all lookup API invocations, logging volume and retries must fit the reviewed demo budget. Larger investigations require a new cost review. This feature does not authorize deployment or establish zero cost.

## Offline verification

The tests exercise real SDK request shapes against stubs and Moto, including actual consumer-emitted JSON ingested into a mocked CloudWatch group and retrieved through the lookup. They cover exact reference binding, queue/attempt membership, field masking, empty/repeated pages, call limits, retention and time windows, denied/missing/throttled services, partial results, and logging failure after a business effect. Existing uncertain-send, crash and idempotency safety tests still pass.

Run `.venv/bin/python -m unittest discover -s tests -v`, `npm run test:frontend`, `npm run build`, and the documented mocked Terraform tests. Browser verification uses an ignored local HTTP fixture; it is separate from both the real SDK/mock-service tests and required future live-account verification.
